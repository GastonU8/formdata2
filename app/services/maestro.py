"""ABM del maestro de productos.

El maestro es la tabla de la que dependen todos los indicadores: si tiene el mismo
producto cargado dos veces, cada informe reparte los usos entre las dos filas y
ninguna cifra cierra. Por eso acá hay tres cuidados que no son negociables:

* **La clave manda.** No se puede dar de alta un producto cuya clave normalizada
  ya exista: sería crear el duplicado a mano.
* **No se borra.** Un producto usado en ensayos de 2019 no se puede eliminar sin
  romper la trazabilidad de esos ensayos. Se discontinúa: deja de ofrecerse para
  mapeos nuevos y sigue explicando el pasado.
* **Los duplicados se fusionan, no se ocultan.** Fusionar mueve usos y mapeos al
  producto que queda y deja el otro discontinuado, con la constancia de a dónde fue.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update

from ..extensions import db
from ..unidad_de_trabajo import transaccion
from ..models import (
    DetalleMezcla,
    EstadoProducto,
    MapeoProducto,
    Mezcla,
    Producto,
    Tratamiento,
)
from ..security import registrar
from . import normalizacion as norm

CAMPOS = ("nombre_comercial", "numero_registro", "marca", "activo", "categoria", "tipo_formulacion", "observaciones")

LARGOS = {
    "nombre_comercial": 180,
    "numero_registro": 60,
    "marca": 120,
    "activo": 180,
    "categoria": 120,
    "tipo_formulacion": 60,
    "observaciones": 2000,
}


class DatosInvalidos(Exception):
    def __init__(self, errores: list[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


def _limpiar(datos: dict) -> dict:
    limpios = {}
    for campo in CAMPOS:
        valor = (datos.get(campo) or "").strip()
        limpios[campo] = valor[: LARGOS[campo]] if valor else None
    return limpios


def _validar(datos: dict, actual: Producto | None = None) -> str:
    errores: list[str] = []
    nombre = datos.get("nombre_comercial")
    if not nombre:
        errores.append("El nombre comercial es obligatorio.")
        raise DatosInvalidos(errores)

    clave = norm.clave(nombre)
    if not clave:
        errores.append("El nombre comercial tiene que tener al menos una letra o número.")
        raise DatosInvalidos(errores)

    existente = db.session.scalar(
        select(Producto).where(Producto.clave_normalizada == clave)
    )
    if existente is not None and (actual is None or existente.id != actual.id):
        errores.append(
            f"Ya existe «{existente.nombre_comercial}» con ese mismo nombre normalizado. "
            "Si son el mismo producto, fusionalos en lugar de duplicarlo."
        )
    if errores:
        raise DatosInvalidos(errores)
    return clave


def crear(datos: dict, usuario) -> Producto:
    with transaccion():
        return _crear(datos, usuario)


def _crear(datos: dict, usuario) -> Producto:
    limpios = _limpiar(datos)
    clave = _validar(limpios)

    producto = Producto(clave_normalizada=clave, estado=EstadoProducto.VIGENTE, **limpios)
    db.session.add(producto)
    db.session.flush()
    registrar(
        "producto_creado",
        entidad="producto",
        entidad_id=producto.id,
        usuario_id=getattr(usuario, "id", None),
        datos={"nombre": producto.nombre_comercial},
        confirmar=False,
    )
    return producto


def editar(producto: Producto, datos: dict, usuario) -> Producto:
    with transaccion():
        return _editar(producto, datos, usuario)


def _editar(producto: Producto, datos: dict, usuario) -> Producto:
    limpios = _limpiar(datos)
    clave = _validar(limpios, actual=producto)

    cambios = {
        campo: (getattr(producto, campo), limpios[campo])
        for campo in CAMPOS
        if getattr(producto, campo) != limpios[campo]
    }
    for campo, valor in limpios.items():
        setattr(producto, campo, valor)
    producto.clave_normalizada = clave

    registrar(
        "producto_editado",
        entidad="producto",
        entidad_id=producto.id,
        usuario_id=getattr(usuario, "id", None),
        datos={"nombre": producto.nombre_comercial, "campos": sorted(cambios)},
        confirmar=False,
    )
    return producto


def cambiar_estado(producto: Producto, estado: EstadoProducto, usuario) -> Producto:
    """Discontinúa o rehabilita. Nunca borra: el histórico lo sigue referenciando."""
    with transaccion():
        return _cambiar_estado(producto, estado, usuario)


def _cambiar_estado(producto: Producto, estado: EstadoProducto, usuario) -> Producto:
    producto.estado = estado
    registrar(
        "producto_estado",
        entidad="producto",
        entidad_id=producto.id,
        usuario_id=getattr(usuario, "id", None),
        datos={"nombre": producto.nombre_comercial, "estado": estado.value},
        confirmar=False,
    )
    return producto


def fusionar(origen: Producto, destino: Producto, usuario) -> int:
    """Pasa todo lo que apunta a `origen` hacia `destino` y discontinúa el origen.

    Se mueven las dos puntas: los detalles de mezcla (los usos históricos) y los
    mapeos (las reglas ya confirmadas). Si solo se movieran los detalles, la próxima
    importación volvería a mandar filas al producto viejo y el duplicado renacería.
    """
    if origen.id == destino.id:
        raise DatosInvalidos(["No se puede fusionar un producto consigo mismo."])

    with transaccion():
        return _fusionar(origen, destino, usuario)


def _fusionar(origen: Producto, destino: Producto, usuario) -> int:

    usos = db.session.execute(
        update(DetalleMezcla)
        .where(DetalleMezcla.producto_id == origen.id)
        .values(producto_id=destino.id)
        .execution_options(synchronize_session=False)
    ).rowcount or 0

    mapeos = db.session.execute(
        update(MapeoProducto)
        .where(MapeoProducto.producto_id == origen.id)
        .values(producto_id=destino.id, nombre_normalizado=destino.nombre_comercial)
        .execution_options(synchronize_session=False)
    ).rowcount or 0

    origen.estado = EstadoProducto.DISCONTINUADO
    nota = f"Fusionado en «{destino.nombre_comercial}» (id {destino.id})."
    origen.observaciones = f"{origen.observaciones}\n{nota}".strip() if origen.observaciones else nota

    registrar(
        "producto_fusionado",
        entidad="producto",
        entidad_id=origen.id,
        usuario_id=getattr(usuario, "id", None),
        datos={
            "origen": origen.nombre_comercial,
            "destino": destino.nombre_comercial,
            "usos_movidos": usos,
            "mapeos_movidos": mapeos,
        },
        confirmar=False,
    )
    return usos


# --------------------------------------------------------------------- ficha


@dataclass
class Ficha:
    producto: Producto
    usos: int
    variantes: list[tuple[str, int]]
    tratamientos: list[Tratamiento]
    mapeos: list[MapeoProducto]


def ficha(producto_id: int) -> Ficha | None:
    """Todo lo que hay que ver antes de tocar un producto del maestro.

    Incluye las grafías con las que se lo escribió: es la prueba de que el mapeo
    está bien hecho y, cuando está mal, es donde salta a la vista.
    """
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        return None

    usos = db.session.scalar(
        select(func.count(DetalleMezcla.id)).where(DetalleMezcla.producto_id == producto_id)
    ) or 0

    variantes = db.session.execute(
        select(DetalleMezcla.nombre_original, func.count(DetalleMezcla.id))
        .where(DetalleMezcla.producto_id == producto_id)
        .group_by(DetalleMezcla.nombre_original)
        .order_by(func.count(DetalleMezcla.id).desc())
        .limit(20)
    ).all()

    tratamientos = list(
        db.session.scalars(
            select(Tratamiento)
            .join(Mezcla, Mezcla.tratamiento_id == Tratamiento.id)
            .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
            .where(DetalleMezcla.producto_id == producto_id)
            .order_by(Tratamiento.fecha.desc().nullslast())
            .distinct()
            .limit(8)
        )
    )

    mapeos = list(
        db.session.scalars(
            select(MapeoProducto)
            .where(MapeoProducto.producto_id == producto_id)
            .order_by(MapeoProducto.nombre_original)
        )
    )

    return Ficha(
        producto=producto,
        usos=usos,
        variantes=[(nombre, veces) for nombre, veces in variantes],
        tratamientos=tratamientos,
        mapeos=mapeos,
    )


def candidatos_a_fusionar(producto: Producto, limite: int = 5) -> list[tuple[Producto, float]]:
    """Otros productos del maestro que se parecen a este, por si son el mismo."""
    otros = db.session.scalars(select(Producto).where(Producto.id != producto.id))
    parecidos = [
        (p, norm.puntaje(producto.clave_normalizada, p.clave_normalizada)) for p in otros
    ]
    return sorted(
        [(p, v) for p, v in parecidos if v >= norm.UMBRAL_MEDIA],
        key=lambda par: par[1],
        reverse=True,
    )[:limite]
