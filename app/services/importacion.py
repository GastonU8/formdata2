"""Carga en base de lo que leyó el lector de Excel.

Separado de la lectura a propósito: el lector no sabe de SQLAlchemy y el cargador
no sabe de openpyxl. Eso hace que el parser se pueda testear con un diccionario
y que el cargador se pueda reutilizar el día que la fuente sea otra.

Idempotencia: el número de tratamiento es único en todo el sistema (confirmado
con el negocio: no se reutiliza entre años). Reimportar el mismo archivo
actualiza los tratamientos en lugar de duplicarlos.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field

from sqlalchemy import select

from ..extensions import db
from ..models import (
    ArchivoImportado,
    Cliente,
    DetalleMezcla,
    EstadoFlujo,
    EstadoImportacion,
    EstadoMapeo,
    EstadoNormalizacion,
    MapeoProducto,
    Mezcla,
    OrigenTratamiento,
    Producto,
    Resultado,
    Tratamiento,
)
from ..security import registrar
from . import lector_excel as lector
from . import normalizacion as norm

MAXIMO_ERRORES_GUARDADOS = 300


@dataclass
class Resumen:
    tratamientos_nuevos: int = 0
    tratamientos_actualizados: int = 0
    productos_vinculados: int = 0
    productos_pendientes: int = 0
    clientes_nuevos: int = 0
    productos_maestro_nuevos: int = 0
    mapeos_nuevos: int = 0
    filas_omitidas: int = 0
    errores: list[dict] = field(default_factory=list)

    def como_dict(self) -> dict:
        datos = self.__dict__.copy()
        datos["errores"] = len(self.errores)
        return datos


def calcular_hash(ruta: str) -> tuple[str, int]:
    """SHA-256 del archivo, leído por bloques para no cargarlo entero."""
    digest = hashlib.sha256()
    tamano = 0
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
            tamano += len(bloque)
    return digest.hexdigest(), tamano


# ------------------------------------------------------------------ catálogos


class _Cache:
    """Índices en memoria para no consultar la base fila por fila."""

    def __init__(self) -> None:
        self.clientes = {
            c.clave_normalizada: c
            for c in db.session.scalars(select(Cliente))
        }
        self.productos = {
            p.clave_normalizada: p for p in db.session.scalars(select(Producto))
        }
        self.mapeos = {
            m.clave_normalizada: m for m in db.session.scalars(select(MapeoProducto))
        }


def _obtener_cliente(cache: _Cache, nombre: str | None, resumen: Resumen, **extra) -> Cliente | None:
    if not nombre:
        return None
    clave = norm.clave(nombre)
    if not clave:
        return None
    cliente = cache.clientes.get(clave)
    if cliente is None:
        cliente = Cliente(nombre=nombre[:180], clave_normalizada=clave)
        db.session.add(cliente)
        db.session.flush()
        cache.clientes[clave] = cliente
        resumen.clientes_nuevos += 1
    # Los datos de contexto se completan si faltaban, nunca se pisan.
    for campo, valor in extra.items():
        if valor and not getattr(cliente, campo, None):
            setattr(cliente, campo, valor)
    return cliente


def _cargar_maestro(cache: _Cache, filas: list[dict], resumen: Resumen) -> None:
    for datos in filas:
        clave = norm.clave(datos["nombre"])
        if not clave:
            continue
        producto = cache.productos.get(clave)
        if producto is None:
            producto = Producto(nombre_comercial=datos["nombre"][:180], clave_normalizada=clave)
            db.session.add(producto)
            resumen.productos_maestro_nuevos += 1
            cache.productos[clave] = producto
        for campo, valor in [
            ("numero_registro", datos.get("numero_registro")),
            ("marca", datos.get("empresa")),
            ("activo", datos.get("activos")),
            ("categoria", datos.get("categoria")),
            ("tipo_formulacion", datos.get("formulacion")),
        ]:
            if valor and not getattr(producto, campo):
                setattr(producto, campo, valor)
    db.session.flush()


def _cargar_mapeos(cache: _Cache, pares: list[tuple[str, str]], resumen: Resumen) -> None:
    """Trae las equivalencias del Excel como mapeos ya confirmados.

    Son decisiones que un humano tomó en la planilla: entran como CONFIRMADO,
    no como sugerencia.
    """
    for original, final in pares:
        clave = norm.clave(original)
        if not clave:
            continue
        producto = cache.productos.get(norm.clave(final))
        mapeo = cache.mapeos.get(clave)
        if mapeo is None:
            mapeo = MapeoProducto(
                nombre_original=original[:220],
                clave_normalizada=clave,
                nombre_normalizado=final[:220],
                producto_id=producto.id if producto else None,
                estado=EstadoMapeo.CONFIRMADO,
            )
            db.session.add(mapeo)
            cache.mapeos[clave] = mapeo
            resumen.mapeos_nuevos += 1
        else:
            mapeo.nombre_normalizado = final[:220]
            if producto and not mapeo.producto_id:
                mapeo.producto_id = producto.id
    db.session.flush()


def _resolver_producto(cache: _Cache, nombre: str):
    """Del nombre crudo al producto del maestro.

    Deliberadamente ignora las columnas `Producto_Final` e `Id_Producto` del
    Excel: en el archivo real esa fórmula devuelve productos equivocados en lugar
    de «SIN MAPEAR», y heredar ese error arruinaría todo el análisis posterior.
    El orden es: mapeo confirmado, después coincidencia directa contra el
    maestro, y si no, pendiente.

    Un mapeo RECHAZADO también es una decisión tomada: alguien miró ese texto en la
    bandeja de normalización y dijo que no es un producto. Sin este caso, cada
    reimportación del histórico devolvería esas filas a PENDIENTE y el trabajo de
    la etapa 3 se borraría solo.
    """
    clave = norm.clave(nombre)
    if not clave:
        return None, None, EstadoNormalizacion.PENDIENTE

    mapeo = cache.mapeos.get(clave)
    if mapeo is not None:
        if mapeo.estado is EstadoMapeo.RECHAZADO:
            return mapeo, None, EstadoNormalizacion.SIN_PRODUCTO
        if mapeo.producto_id:
            return mapeo, mapeo.producto_id, EstadoNormalizacion.CONFIRMADA
        producto = cache.productos.get(norm.clave(mapeo.nombre_normalizado or ""))
        if producto is not None:
            mapeo.producto_id = producto.id
            return mapeo, producto.id, EstadoNormalizacion.CONFIRMADA
        return mapeo, None, EstadoNormalizacion.PENDIENTE

    producto = cache.productos.get(clave)
    if producto is not None:
        return None, producto.id, EstadoNormalizacion.AUTOMATICA

    return None, None, EstadoNormalizacion.PENDIENTE


# ------------------------------------------------------------------- proceso


def procesar(archivo: ArchivoImportado, ruta: str) -> Resumen:
    """Lee el archivo y vuelca su contenido. Actualiza el estado del ArchivoImportado."""
    resumen = Resumen()
    archivo.estado = EstadoImportacion.VALIDANDO
    db.session.commit()

    try:
        lectura = lector.leer(ruta)
    except lector.ArchivoInvalido as error:
        archivo.estado = EstadoImportacion.INVALIDO
        archivo.detalle_errores = [
            {"fila": 0, "columna": "archivo", "mensaje": m} for m in error.errores
        ]
        archivo.observaciones = "El archivo no tiene la estructura esperada."
        db.session.commit()
        return resumen

    archivo.estado = EstadoImportacion.PROCESANDO
    archivo.filas_leidas = lectura.filas_leidas
    db.session.commit()

    resumen.errores = [e.como_dict() for e in lectura.errores]
    resumen.filas_omitidas = lectura.filas_omitidas

    cache = _Cache()
    _cargar_maestro(cache, lectura.maestro, resumen)
    _cargar_mapeos(cache, lectura.mapeos, resumen)
    db.session.commit()

    con_error = {e["fila"] for e in resumen.errores}

    for datos in lectura.tratamientos:
        if datos["fila"] in con_error and not datos["productos"]:
            continue
        try:
            _cargar_tratamiento(cache, archivo, datos, resumen)
            # Una fila por transacción: una fila rota no tira abajo las anteriores.
            db.session.commit()
        except Exception as error:  # noqa: BLE001 — se reporta, no se propaga
            db.session.rollback()
            resumen.errores.append(
                {
                    "fila": datos["fila"],
                    "columna": "—",
                    "mensaje": f"No se pudo guardar el tratamiento {datos['numero']}: {error}",
                }
            )

    archivo.filas_ok = resumen.tratamientos_nuevos + resumen.tratamientos_actualizados
    archivo.filas_con_error = len(resumen.errores)
    archivo.detalle_errores = resumen.errores[:MAXIMO_ERRORES_GUARDADOS]
    archivo.estado = (
        EstadoImportacion.FINALIZADO_CON_ERRORES
        if resumen.errores
        else EstadoImportacion.FINALIZADO
    )
    archivo.observaciones = (
        f"{archivo.filas_ok} tratamientos cargados. "
        f"{resumen.productos_pendientes} productos quedaron sin mapear."
    )
    registrar(
        "importacion_finalizada",
        entidad="archivo_importado",
        entidad_id=archivo.id,
        usuario_id=archivo.usuario_id,
        datos=resumen.como_dict(),
        confirmar=False,
    )
    db.session.commit()
    return resumen


def _cargar_tratamiento(cache: _Cache, archivo: ArchivoImportado, datos: dict, resumen: Resumen) -> None:
    tratamiento = db.session.scalar(
        select(Tratamiento).where(Tratamiento.numero_tratamiento == datos["numero"])
    )
    if tratamiento is None:
        tratamiento = Tratamiento(numero_tratamiento=datos["numero"])
        db.session.add(tratamiento)
        resumen.tratamientos_nuevos += 1
    else:
        resumen.tratamientos_actualizados += 1
        # Se reemplaza la composición: la fuente de verdad es el archivo.
        for mezcla in list(tratamiento.mezclas):
            db.session.delete(mezcla)
        db.session.flush()

    cliente = _obtener_cliente(
        cache, datos.get("cliente"), resumen,
        ubicacion=datos.get("ubicacion"), clasificacion=datos.get("clasificacion"),
    )
    cliente_softland = _obtener_cliente(
        cache, datos.get("cliente_softland"), resumen,
        numero_cuenta=datos.get("numero_cuenta"),
    )

    tratamiento.archivo_id = archivo.id
    tratamiento.cliente_id = cliente.id if cliente else None
    tratamiento.cliente_softland_id = cliente_softland.id if cliente_softland else None
    tratamiento.fecha = datos["fecha"]
    tratamiento.origen = OrigenTratamiento.IMPORTACION
    # Lo importado ya ocurrió: no recorre el circuito de aprobación.
    tratamiento.estado_flujo = EstadoFlujo.FINALIZADA

    for campo in (
        "pais", "laboratorio", "cultivo", "momento_aplicacion", "target",
        "tipo_tratamiento", "observaciones",
    ):
        setattr(tratamiento, campo, datos.get(campo))
    tratamiento.enlace = datos.get("enlace")
    tratamiento.tasa_aplicacion = datos.get("tasa_aplicacion")
    tratamiento.cantidad_ha = datos.get("cantidad_ha")
    db.session.flush()

    mezcla = Mezcla(
        tratamiento_id=tratamiento.id,
        nombre_mezcla=datos.get("mezcla_texto"),
        orden=1,
    )
    db.session.add(mezcla)
    db.session.flush()

    for linea in datos["productos"]:
        mapeo, producto_id, estado = _resolver_producto(cache, linea["nombre"])
        if estado is EstadoNormalizacion.PENDIENTE:
            resumen.productos_pendientes += 1
        else:
            resumen.productos_vinculados += 1
        db.session.add(
            DetalleMezcla(
                mezcla_id=mezcla.id,
                orden_producto=linea["orden"],
                nombre_original=linea["nombre"][:220],
                clave_normalizada=norm.clave(linea["nombre"]),
                mapeo_id=mapeo.id if mapeo else None,
                producto_id=producto_id,
                estado_normalizacion=estado,
                # El Excel no registra dosis por producto: solo TasaAplicacion
                # a nivel tratamiento. Queda vacío a propósito.
                dosis=None,
                unidad=None,
            )
        )

    resultado = tratamiento.resultado or Resultado(tratamiento_id=tratamiento.id)
    for campo in (
        "ph_agua", "dureza_agua", "ph_mezcla", "agua_corregida",
        "ph_agua_corregida", "dureza_agua_corregida",
        "observacion_cinco_min", "volumen_cinco_min",
        "observacion_diez_min", "volumen_diez_min",
        "suspensibilidad", "dosis_compatibilizador",
        "granulometria_300", "granulometria_180", "granulometria_150",
        "granulometria_106", "granulometria_53",
    ):
        setattr(resultado, campo, datos.get(campo))
    resultado.estado_resultado = datos.get("observacion_diez_min") or datos.get(
        "observacion_cinco_min"
    )
    db.session.add(resultado)


def procesar_en_segundo_plano(app, archivo_id: int, ruta: str) -> threading.Thread:
    """Lanza el procesamiento fuera del request.

    Un histórico de miles de filas no entra en el tiempo de una petición HTTP.
    El request solo guarda el archivo y devuelve; la pantalla muestra el avance
    consultando el estado. Cuando el volumen lo justifique, este hilo se cambia
    por una cola (RQ o Celery) sin tocar `procesar()`.
    """

    def tarea() -> None:
        with app.app_context():
            archivo = db.session.get(ArchivoImportado, archivo_id)
            if archivo is None:
                return
            try:
                procesar(archivo, ruta)
            except Exception as error:  # noqa: BLE001
                db.session.rollback()
                archivo = db.session.get(ArchivoImportado, archivo_id)
                if archivo is not None:
                    archivo.estado = EstadoImportacion.INVALIDO
                    archivo.observaciones = f"Error inesperado: {error}"
                    db.session.commit()

    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    return hilo
