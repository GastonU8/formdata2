"""Cálculo de indicadores del tablero.

Se calculan a demanda con SQL agregado. La tabla `Indicador` queda para snapshots
históricos (comparar mes contra mes), no para servir el tablero: un tablero que
lee valores congelados miente apenas entra una importación nueva.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    ArchivoImportado,
    DetalleMezcla,
    EstadoNormalizacion,
    Producto,
    Tratamiento,
)

PENDIENTES = (EstadoNormalizacion.PENDIENTE,)
RESUELTOS = (
    EstadoNormalizacion.AUTOMATICA,
    EstadoNormalizacion.CONFIRMADA,
    EstadoNormalizacion.SIN_PRODUCTO,
)


@dataclass(frozen=True)
class SaludDelDato:
    """Estado de normalización del repositorio: el indicador que gobierna al resto."""

    total: int
    resueltos: int
    pendientes: int

    @property
    def porcentaje_resuelto(self) -> float:
        return round(self.resueltos / self.total * 100, 1) if self.total else 100.0

    @property
    def confiable(self) -> bool:
        return self.porcentaje_resuelto >= 95.0


def salud_del_dato() -> SaludDelDato:
    total = db.session.scalar(select(func.count(DetalleMezcla.id))) or 0
    pendientes = (
        db.session.scalar(
            select(func.count(DetalleMezcla.id)).where(
                DetalleMezcla.estado_normalizacion.in_(PENDIENTES)
            )
        )
        or 0
    )
    return SaludDelDato(total=total, resueltos=total - pendientes, pendientes=pendientes)


def resumen_general() -> dict:
    return {
        "tratamientos": db.session.scalar(select(func.count(Tratamiento.id))) or 0,
        "productos_maestro": db.session.scalar(select(func.count(Producto.id))) or 0,
        "importaciones": db.session.scalar(select(func.count(ArchivoImportado.id))) or 0,
        "cultivos": db.session.scalar(
            select(func.count(func.distinct(Tratamiento.cultivo))).where(
                Tratamiento.cultivo.is_not(None)
            )
        )
        or 0,
    }


def productos_mas_usados(limite: int = 8) -> list[dict]:
    filas = db.session.execute(
        select(Producto.nombre_comercial, Producto.activo, func.count(DetalleMezcla.id).label("usos"))
        .join(DetalleMezcla, DetalleMezcla.producto_id == Producto.id)
        .group_by(Producto.id)
        .order_by(func.count(DetalleMezcla.id).desc())
        .limit(limite)
    ).all()
    return [{"nombre": f[0], "activo": f[1], "usos": f[2]} for f in filas]


def tratamientos_por_cultivo(limite: int = 8) -> list[dict]:
    filas = db.session.execute(
        select(Tratamiento.cultivo, func.count(Tratamiento.id).label("cantidad"))
        .where(Tratamiento.cultivo.is_not(None))
        .group_by(Tratamiento.cultivo)
        .order_by(func.count(Tratamiento.id).desc())
        .limit(limite)
    ).all()
    return [{"cultivo": f[0], "cantidad": f[1]} for f in filas]


def ultimas_importaciones(limite: int = 5) -> list[ArchivoImportado]:
    return list(
        db.session.scalars(
            select(ArchivoImportado)
            .order_by(ArchivoImportado.creado_en.desc())
            .limit(limite)
        )
    )
