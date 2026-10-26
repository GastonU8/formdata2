"""Alta de solicitudes de mezcla y carga de resultados.

La resolución de productos usa el mismo motor de normalización que la importación
de Excel: el vendedor escribe el nombre como lo conoce y el sistema intenta
resolverlo contra el maestro. Si no resuelve, el detalle queda PENDIENTE y aparece
en la bandeja de normalización — igual que una fila importada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..unidad_de_trabajo import transaccion
from ..models import (
    Cliente,
    DetalleMezcla,
    EstadoFlujo,
    EstadoNormalizacion,
    Mezcla,
    OrigenTratamiento,
    Producto,
    Resultado,
    Tratamiento,
    Usuario,
)
from . import flujo
from . import normalizacion as norm

MAXIMO_PRODUCTOS = 15


class DatosInvalidos(Exception):
    """Errores de carga, listos para mostrarle al usuario."""

    def __init__(self, errores: list[str]):
        self.errores = errores
        super().__init__("; ".join(errores))


@dataclass
class LineaProducto:
    nombre: str
    dosis: float | None = None
    unidad: str = ""


def _resolver_producto(nombre: str) -> tuple[Producto | None, EstadoNormalizacion]:
    clave = norm.clave(nombre)
    if not clave:
        return None, EstadoNormalizacion.PENDIENTE
    producto = db.session.scalar(select(Producto).where(Producto.clave_normalizada == clave))
    if producto is not None:
        return producto, EstadoNormalizacion.AUTOMATICA
    return None, EstadoNormalizacion.PENDIENTE


def crear_solicitud(
    *,
    autor: Usuario,
    cliente_id: int | None,
    cultivo: str,
    tipo_tratamiento: str,
    target: str,
    observaciones: str,
    fecha: date | None,
    lineas: list[LineaProducto],
    enviar: bool = False,
) -> Tratamiento:
    lineas = [linea for linea in lineas if linea.nombre.strip()]

    errores: list[str] = []
    if not cultivo.strip():
        errores.append("Indicá el cultivo.")
    if not lineas:
        errores.append("La mezcla necesita al menos un producto.")
    if len(lineas) > MAXIMO_PRODUCTOS:
        errores.append(f"No se admiten más de {MAXIMO_PRODUCTOS} productos por mezcla.")
    if cliente_id and not db.session.get(Cliente, cliente_id):
        errores.append("El cliente elegido no existe.")
    for indice, linea in enumerate(lineas, start=1):
        if linea.dosis is not None and linea.dosis < 0:
            errores.append(f"La dosis del producto {indice} no puede ser negativa.")
    if errores:
        raise DatosInvalidos(errores)

    # Validar antes y escribir después: abrir la transacción recién acá deja claro
    # que nada de lo de arriba toca la base.
    with transaccion():
        return _crear(
            autor, cliente_id, cultivo, tipo_tratamiento, target, observaciones,
            fecha, lineas, enviar,
        )


def _crear(
    autor, cliente_id, cultivo, tipo_tratamiento, target, observaciones,
    fecha, lineas, enviar,
) -> Tratamiento:
    tratamiento = Tratamiento(
        numero_tratamiento=flujo.proximo_numero(),
        fecha=fecha,
        cliente_id=cliente_id,
        cultivo=cultivo.strip(),
        tipo_tratamiento=tipo_tratamiento.strip() or None,
        target=target.strip() or None,
        observaciones=observaciones.strip() or None,
        estado_flujo=EstadoFlujo.BORRADOR,
        origen=OrigenTratamiento.SOLICITUD,
        solicitado_por_id=autor.id,
    )
    db.session.add(tratamiento)
    db.session.flush()

    mezcla = Mezcla(tratamiento_id=tratamiento.id, nombre_mezcla="Mezcla solicitada", orden=1)
    db.session.add(mezcla)
    db.session.flush()

    for orden, linea in enumerate(lineas, start=1):
        producto, estado = _resolver_producto(linea.nombre)
        db.session.add(
            DetalleMezcla(
                mezcla_id=mezcla.id,
                orden_producto=orden,
                nombre_original=linea.nombre.strip(),
                clave_normalizada=norm.clave(linea.nombre),
                producto_id=producto.id if producto else None,
                estado_normalizacion=estado,
                dosis=linea.dosis,
                unidad=linea.unidad.strip() or None,
            )
        )

    if enviar:
        flujo.ejecutar(tratamiento, "enviar", autor)

    return tratamiento


def cargar_resultado(
    *,
    tratamiento: Tratamiento,
    usuario: Usuario,
    datos: dict,
) -> Tratamiento:
    """Guarda las mediciones y cierra el circuito.

    La carga y el cambio de estado van en la misma transacción: un tratamiento
    finalizado sin resultado, o un resultado en un tratamiento abierto, serían
    estados imposibles de explicar después.
    """
    if tratamiento.estado_flujo is not EstadoFlujo.EN_ENSAYO:
        raise flujo.TransicionInvalida(
            "Solo se cargan resultados sobre un tratamiento en ensayo."
        )

    errores = []
    for campo, minimo, maximo, etiqueta in [
        ("porcentaje_efectividad", 0, 100, "efectividad"),
        ("ph_agua", 0, 14, "pH del agua"),
        ("ph_mezcla", 0, 14, "pH de la mezcla"),
    ]:
        valor = datos.get(campo)
        if valor is not None and not (minimo <= valor <= maximo):
            errores.append(f"La {etiqueta} debe estar entre {minimo} y {maximo}.")
    if not (datos.get("estado_resultado") or "").strip():
        errores.append("Indicá la conclusión del ensayo.")
    if errores:
        raise DatosInvalidos(errores)

    with transaccion():
        return _guardar_resultado(tratamiento, usuario, datos)


def _guardar_resultado(tratamiento, usuario, datos) -> Tratamiento:
    resultado = tratamiento.resultado or Resultado(tratamiento_id=tratamiento.id)
    for campo in (
        "porcentaje_efectividad", "ph_agua", "dureza_agua", "ph_mezcla",
        "observacion_cinco_min", "observacion_diez_min",
        "suspensibilidad", "granulometria", "estado_resultado",
    ):
        setattr(resultado, campo, datos.get(campo))
    db.session.add(resultado)

    flujo.ejecutar(tratamiento, "finalizar", usuario)
    return tratamiento


# -------------------------------------------------------------------- bandeja


def bandeja(usuario: Usuario, estado: EstadoFlujo | None = None) -> list[Tratamiento]:
    consulta = (
        select(Tratamiento)
        .where(Tratamiento.origen == OrigenTratamiento.SOLICITUD)
        .options(
            joinedload(Tratamiento.cliente),
            joinedload(Tratamiento.solicitado_por),
            selectinload(Tratamiento.mezclas).selectinload(Mezcla.detalles),
        )
        .order_by(Tratamiento.actualizado_en.desc())
    )
    if estado is not None:
        consulta = consulta.where(Tratamiento.estado_flujo == estado)
    else:
        consulta = consulta.where(Tratamiento.estado_flujo.in_(flujo.bandeja_de(usuario)))
    return list(db.session.scalars(consulta).unique())


def conteo_por_estado(usuario: Usuario) -> dict[EstadoFlujo, int]:
    filas = db.session.execute(
        select(Tratamiento.estado_flujo, func.count(Tratamiento.id))
        .where(Tratamiento.origen == OrigenTratamiento.SOLICITUD)
        .group_by(Tratamiento.estado_flujo)
    ).all()
    conteos = dict(filas)
    return {estado: conteos.get(estado, 0) for estado in flujo.bandeja_de(usuario)}


def catalogo_para_formulario() -> dict:
    return {
        "clientes": list(db.session.scalars(select(Cliente).order_by(Cliente.nombre))),
        "productos": list(
            db.session.scalars(select(Producto).order_by(Producto.nombre_comercial))
        ),
    }
