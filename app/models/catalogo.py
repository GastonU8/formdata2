"""Catálogos maestros: clientes, productos y el puente de normalización."""

from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import MarcasDeTiempo


class EstadoProducto(enum.Enum):
    VIGENTE = "vigente"
    DISCONTINUADO = "discontinuado"


class EstadoMapeo(enum.Enum):
    """Ciclo de vida de una equivalencia nombre-escrito → producto del maestro."""

    SUGERIDO = "sugerido"      # lo propuso el sistema, nadie lo confirmó
    CONFIRMADO = "confirmado"  # un usuario técnico lo validó
    RECHAZADO = "rechazado"    # se descartó explícitamente


class Cliente(MarcasDeTiempo, db.Model):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    clave_normalizada: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    numero_cuenta: Mapped[str | None] = mapped_column(String(60))
    ubicacion: Mapped[str | None] = mapped_column(String(180))
    clasificacion: Mapped[str | None] = mapped_column(String(80))

    # Un tratamiento apunta dos veces a Cliente (el que pidió el ensayo y la
    # cuenta de Softland), así que hay que decir cuál de las dos es esta.
    tratamientos = relationship(
        "Tratamiento",
        back_populates="cliente",
        foreign_keys="Tratamiento.cliente_id",
    )

    def __repr__(self) -> str:
        return f"<Cliente {self.nombre}>"


class Producto(MarcasDeTiempo, db.Model):
    """Maestro técnico de productos agroquímicos."""

    __tablename__ = "producto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_comercial: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    clave_normalizada: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    numero_registro: Mapped[str | None] = mapped_column(String(60), index=True)
    marca: Mapped[str | None] = mapped_column(String(120), index=True)
    activo: Mapped[str | None] = mapped_column(String(180), index=True)
    categoria: Mapped[str | None] = mapped_column(String(120), index=True)
    tipo_formulacion: Mapped[str | None] = mapped_column(String(60))
    estado: Mapped[EstadoProducto] = mapped_column(
        Enum(EstadoProducto), default=EstadoProducto.VIGENTE, nullable=False
    )
    observaciones: Mapped[str | None] = mapped_column(Text)

    mapeos = relationship("MapeoProducto", back_populates="producto")
    usos = relationship("DetalleMezcla", back_populates="producto")

    __table_args__ = (
        Index("ix_producto_busqueda", "activo", "categoria", "marca"),
    )

    def __repr__(self) -> str:
        return f"<Producto {self.nombre_comercial}>"


class MapeoProducto(MarcasDeTiempo, db.Model):
    """Equivalencia entre cómo se escribió un producto en el Excel y el maestro.

    Cambio respecto del diagrama original: se agrega `clave_normalizada` con índice
    único. Sin esa clave, "Round Up", "roundup" y "ROUND-UP " generan tres mapeos
    distintos y el maestro se vuelve a ensuciar, que es justo el problema que el
    sistema viene a resolver.
    """

    __tablename__ = "mapeo_producto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_original: Mapped[str] = mapped_column(String(220), nullable=False)
    clave_normalizada: Mapped[str] = mapped_column(
        String(220), nullable=False, unique=True, index=True
    )
    nombre_normalizado: Mapped[str | None] = mapped_column(String(220))

    producto_id: Mapped[int | None] = mapped_column(ForeignKey("producto.id"), index=True)
    producto = relationship("Producto", back_populates="mapeos")

    estado: Mapped[EstadoMapeo] = mapped_column(
        Enum(EstadoMapeo), default=EstadoMapeo.SUGERIDO, nullable=False, index=True
    )
    confirmado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    confirmado_por = relationship("Usuario")

    detalles = relationship("DetalleMezcla", back_populates="mapeo")

    def __repr__(self) -> str:
        return f"<MapeoProducto {self.nombre_original} → {self.producto_id}>"
