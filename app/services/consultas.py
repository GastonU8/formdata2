"""Búsqueda y detalle de tratamientos.

Toda la lógica de filtrado vive acá y no en las vistas: la vista traduce el
formulario a un objeto `Filtros` y muestra lo que este módulo devuelve. Así el
filtrado se puede testear sin cliente HTTP y se reutiliza después desde la capa
de herramientas del asistente IA (etapa 5) sin duplicar una línea.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    Cliente,
    DetalleMezcla,
    EstadoNormalizacion,
    Mezcla,
    Producto,
    Resultado,
    Tratamiento,
)

POR_PAGINA = 20
MAXIMO_POR_PAGINA = 100


@dataclass
class Filtros:
    """Criterios de búsqueda. Todos opcionales y combinables entre sí."""

    texto: str = ""
    cultivo: str = ""
    tipo_tratamiento: str = ""
    cliente_id: int | None = None
    producto_id: int | None = None
    producto_texto: str = ""
    estado_resultado: str = ""
    desde: date | None = None
    hasta: date | None = None
    solo_sin_mapear: bool = False
    orden: str = "fecha_desc"

    ORDENES = {
        "fecha_desc": "Más recientes primero",
        "fecha_asc": "Más antiguos primero",
        "numero": "Por número de tratamiento",
        # El porcentaje de efectividad solo existe en los ensayos cargados por el
        # circuito: el Excel histórico no trae esa columna. La etiqueta lo dice para
        # que nadie interprete que los importados quedaron últimos por malos.
        "efectividad": "Mayor efectividad primero (solo ensayos del sistema)",
    }

    @property
    def hay_alguno(self) -> bool:
        return any(
            [
                self.texto,
                self.cultivo,
                self.tipo_tratamiento,
                self.cliente_id,
                self.producto_id,
                self.producto_texto,
                self.estado_resultado,
                self.desde,
                self.hasta,
                self.solo_sin_mapear,
            ]
        )

    def descripcion(self) -> list[str]:
        """Los criterios activos, en texto, para que el usuario vea qué está filtrando."""
        partes: list[str] = []
        if self.texto:
            partes.append(f'texto "{self.texto}"')
        if self.cultivo:
            partes.append(f"cultivo {self.cultivo}")
        if self.tipo_tratamiento:
            partes.append(f"tipo {self.tipo_tratamiento}")
        if self.producto_texto:
            partes.append(f'producto o activo "{self.producto_texto}"')
        if self.estado_resultado:
            partes.append(f"resultado {self.estado_resultado}")
        if self.desde:
            partes.append(f"desde {self.desde:%d/%m/%Y}")
        if self.hasta:
            partes.append(f"hasta {self.hasta:%d/%m/%Y}")
        if self.solo_sin_mapear:
            partes.append("con productos sin mapear")
        return partes


def _entero(argumentos, nombre: str) -> int | None:
    valor = (argumentos.get(nombre) or "").strip()
    return int(valor) if valor.isdigit() else None


def _fecha(argumentos, nombre: str) -> date | None:
    valor = (argumentos.get(nombre) or "").strip()
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


def leer_filtros(argumentos) -> Filtros:
    """Traduce una query string a criterios. Nada que no se entienda se ignora.

    Vive acá y no en una vista porque hay tres pantallas que tienen que interpretar
    los mismos parámetros igual: la búsqueda, el tablero y la exportación. Si cada
    una los leyera por su cuenta, el número del tablero y la lista que se abre al
    hacer clic terminarían mostrando conjuntos distintos, que es la clase de
    inconsistencia que hace que nadie vuelva a confiar en un informe.
    """
    orden = (argumentos.get("orden") or "fecha_desc").strip()
    return Filtros(
        texto=(argumentos.get("texto") or "").strip()[:120],
        cultivo=(argumentos.get("cultivo") or "").strip(),
        tipo_tratamiento=(argumentos.get("tipo") or "").strip(),
        cliente_id=_entero(argumentos, "cliente"),
        producto_id=_entero(argumentos, "producto_id"),
        producto_texto=(argumentos.get("producto") or "").strip()[:120],
        estado_resultado=(argumentos.get("resultado") or "").strip(),
        desde=_fecha(argumentos, "desde"),
        hasta=_fecha(argumentos, "hasta"),
        solo_sin_mapear=(argumentos.get("sin_mapear") or "") == "1",
        orden=orden if orden in Filtros.ORDENES else "fecha_desc",
    )


def subconsulta_ids(filtros: Filtros):
    """Los ids de tratamiento que pasan los filtros, para usar como `IN (...)`.

    Es el puente entre la búsqueda y los indicadores: cada gráfico del tablero se
    calcula sobre este mismo conjunto, así que ningún indicador puede contar algo
    que la búsqueda no muestre. Se le saca el `ORDER BY` porque una subconsulta
    ordenada no aporta nada y PostgreSQL la rechaza en algunos contextos.
    """
    return (
        _armar_consulta(filtros)
        .with_only_columns(Tratamiento.id)
        .order_by(None)
    )


@dataclass
class Opciones:
    """Valores que existen realmente en la base, para poblar los desplegables.

    Se leen de los datos y no de una lista fija: si mañana entra un cultivo nuevo
    por importación, aparece solo en el filtro.
    """

    cultivos: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    estados_resultado: list[str] = field(default_factory=list)
    clientes: list[Cliente] = field(default_factory=list)


def _valores_distintos(columna) -> list[str]:
    return [
        v
        for (v,) in db.session.execute(
            select(columna).where(columna.is_not(None), columna != "").distinct().order_by(columna)
        ).all()
    ]


def opciones_de_filtro() -> Opciones:
    return Opciones(
        cultivos=_valores_distintos(Tratamiento.cultivo),
        tipos=_valores_distintos(Tratamiento.tipo_tratamiento),
        estados_resultado=_valores_distintos(Resultado.estado_resultado),
        clientes=list(db.session.scalars(select(Cliente).order_by(Cliente.nombre))),
    )


def _ids_por_producto(filtros: Filtros):
    """Subconsulta con los tratamientos que usan cierto producto.

    Va como subconsulta y no como JOIN directo a propósito: un tratamiento que usa
    tres productos coincidentes aparecería tres veces en el listado. Con `IN` el
    resultado es una fila por tratamiento sin necesidad de `DISTINCT`.
    """
    consulta = (
        select(Mezcla.tratamiento_id)
        .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
        .outerjoin(Producto, Producto.id == DetalleMezcla.producto_id)
    )

    if filtros.producto_id:
        consulta = consulta.where(DetalleMezcla.producto_id == filtros.producto_id)

    if filtros.producto_texto:
        patron = f"%{filtros.producto_texto.strip()}%"
        consulta = consulta.where(
            or_(
                Producto.nombre_comercial.ilike(patron),
                Producto.activo.ilike(patron),
                Producto.categoria.ilike(patron),
                Producto.marca.ilike(patron),
                # También busca en el nombre crudo: así encuentra tratamientos
                # cuyo producto todavía no resolvió contra el maestro.
                DetalleMezcla.nombre_original.ilike(patron),
            )
        )

    return consulta


def _ids_sin_mapear():
    return (
        select(Mezcla.tratamiento_id)
        .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
        .where(DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE)
    )


def _armar_consulta(filtros: Filtros):
    consulta = (
        select(Tratamiento)
        .outerjoin(Cliente, Cliente.id == Tratamiento.cliente_id)
        .outerjoin(Resultado, Resultado.tratamiento_id == Tratamiento.id)
    )

    if filtros.texto:
        patron = f"%{filtros.texto.strip()}%"
        consulta = consulta.where(
            or_(
                Tratamiento.numero_tratamiento.ilike(patron),
                Tratamiento.target.ilike(patron),
                Tratamiento.observaciones.ilike(patron),
                Tratamiento.laboratorio.ilike(patron),
                Cliente.nombre.ilike(patron),
            )
        )

    if filtros.cultivo:
        consulta = consulta.where(Tratamiento.cultivo == filtros.cultivo)
    if filtros.tipo_tratamiento:
        consulta = consulta.where(Tratamiento.tipo_tratamiento == filtros.tipo_tratamiento)
    if filtros.cliente_id:
        consulta = consulta.where(Tratamiento.cliente_id == filtros.cliente_id)
    if filtros.estado_resultado:
        consulta = consulta.where(Resultado.estado_resultado == filtros.estado_resultado)
    if filtros.desde:
        consulta = consulta.where(Tratamiento.fecha >= filtros.desde)
    if filtros.hasta:
        consulta = consulta.where(Tratamiento.fecha <= filtros.hasta)

    if filtros.producto_id or filtros.producto_texto:
        consulta = consulta.where(Tratamiento.id.in_(_ids_por_producto(filtros)))
    if filtros.solo_sin_mapear:
        consulta = consulta.where(Tratamiento.id.in_(_ids_sin_mapear()))

    ordenes = {
        "fecha_desc": (Tratamiento.fecha.desc().nullslast(), Tratamiento.id.desc()),
        "fecha_asc": (Tratamiento.fecha.asc().nullsfirst(), Tratamiento.id.asc()),
        "numero": (Tratamiento.numero_tratamiento.asc(),),
        "efectividad": (Resultado.porcentaje_efectividad.desc().nullslast(),),
    }
    return consulta.order_by(*ordenes.get(filtros.orden, ordenes["fecha_desc"]))


def buscar(filtros: Filtros, pagina: int = 1, por_pagina: int = POR_PAGINA):
    """Devuelve una página de tratamientos con cliente y resultado ya cargados."""
    por_pagina = max(1, min(por_pagina, MAXIMO_POR_PAGINA))
    consulta = _armar_consulta(filtros).options(
        joinedload(Tratamiento.cliente), joinedload(Tratamiento.resultado)
    )
    return db.paginate(consulta, page=max(1, pagina), per_page=por_pagina, error_out=False)


def contar_sin_mapear_por_tratamiento(ids: list[int]) -> dict[int, int]:
    """Cuántos productos sin resolver tiene cada tratamiento del listado.

    Se resuelve en una sola consulta para toda la página, no una por fila.
    """
    if not ids:
        return {}
    filas = db.session.execute(
        select(Mezcla.tratamiento_id, func.count(DetalleMezcla.id))
        .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
        .where(
            Mezcla.tratamiento_id.in_(ids),
            DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE,
        )
        .group_by(Mezcla.tratamiento_id)
    ).all()
    return {tratamiento_id: cantidad for tratamiento_id, cantidad in filas}


def obtener_detalle(tratamiento_id: int) -> Tratamiento | None:
    """Trae el tratamiento con toda su composición en una sola ida a la base."""
    return db.session.scalar(
        select(Tratamiento)
        .where(Tratamiento.id == tratamiento_id)
        .options(
            joinedload(Tratamiento.cliente),
            joinedload(Tratamiento.resultado),
            joinedload(Tratamiento.archivo),
            selectinload(Tratamiento.mezclas)
            .selectinload(Mezcla.detalles)
            .joinedload(DetalleMezcla.producto),
        )
    )


def tratamientos_relacionados(tratamiento: Tratamiento, limite: int = 5) -> list[Tratamiento]:
    """Otros ensayos con el mismo cultivo que comparten al menos un producto.

    Es lo que un técnico busca a continuación: "¿y esto ya lo probamos con otra cosa?".
    """
    productos = {
        detalle.producto_id
        for mezcla in tratamiento.mezclas
        for detalle in mezcla.detalles
        if detalle.producto_id
    }
    if not productos:
        return []

    coincidentes = (
        select(Mezcla.tratamiento_id)
        .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
        .where(DetalleMezcla.producto_id.in_(productos))
    )
    consulta = (
        select(Tratamiento)
        .where(Tratamiento.id.in_(coincidentes), Tratamiento.id != tratamiento.id)
        .options(joinedload(Tratamiento.resultado))
        .order_by(Tratamiento.fecha.desc().nullslast())
        .limit(limite)
    )
    if tratamiento.cultivo:
        consulta = consulta.where(Tratamiento.cultivo == tratamiento.cultivo)
    return list(db.session.scalars(consulta))


# ------------------------------------------------------------------ productos


def buscar_productos(texto: str = "", categoria: str = "", limite: int = 200):
    """Maestro de productos con el conteo de usos, para la pantalla de productos."""
    consulta = (
        select(Producto, func.count(DetalleMezcla.id).label("usos"))
        .outerjoin(DetalleMezcla, DetalleMezcla.producto_id == Producto.id)
        .group_by(Producto.id)
        .order_by(func.count(DetalleMezcla.id).desc(), Producto.nombre_comercial)
        .limit(limite)
    )
    if texto:
        patron = f"%{texto.strip()}%"
        consulta = consulta.where(
            or_(
                Producto.nombre_comercial.ilike(patron),
                Producto.activo.ilike(patron),
                Producto.marca.ilike(patron),
                Producto.numero_registro.cast(String).ilike(patron),
            )
        )
    if categoria:
        consulta = consulta.where(Producto.categoria == categoria)

    return [
        {"producto": producto, "usos": usos}
        for producto, usos in db.session.execute(consulta).all()
    ]


def categorias_de_producto() -> list[str]:
    return _valores_distintos(Producto.categoria)
