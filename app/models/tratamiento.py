"""Núcleo del dominio: lo que se importa desde el Excel y queda trazable."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import MarcasDeTiempo


class EstadoImportacion(enum.Enum):
    PENDIENTE = "pendiente"
    VALIDANDO = "validando"
    INVALIDO = "invalido"
    PROCESANDO = "procesando"
    FINALIZADO = "finalizado"
    FINALIZADO_CON_ERRORES = "finalizado_con_errores"


class EstadoFlujo(enum.Enum):
    """Etapas por las que pasa una solicitud de mezcla.

    Los tratamientos que entran por importación de Excel ya ocurrieron: nacen
    directamente en FINALIZADA, sin recorrer el circuito.
    """

    BORRADOR = "borrador"
    PENDIENTE_APROBACION = "pendiente_aprobacion"
    RECHAZADA = "rechazada"
    APROBADA = "aprobada"
    EN_ENSAYO = "en_ensayo"
    FINALIZADA = "finalizada"
    ANULADA = "anulada"

    @property
    def etiqueta(self) -> str:
        return {
            EstadoFlujo.BORRADOR: "Borrador",
            EstadoFlujo.PENDIENTE_APROBACION: "Pendiente de aprobación",
            EstadoFlujo.RECHAZADA: "Rechazada",
            EstadoFlujo.APROBADA: "Aprobada",
            EstadoFlujo.EN_ENSAYO: "En ensayo",
            EstadoFlujo.FINALIZADA: "Finalizada",
            EstadoFlujo.ANULADA: "Anulada",
        }[self]

    @property
    def tono(self) -> str:
        """Clase visual. Agrupa los estados por cómo hay que leerlos."""
        if self in (EstadoFlujo.FINALIZADA, EstadoFlujo.APROBADA):
            return "ok"
        if self in (EstadoFlujo.RECHAZADA, EstadoFlujo.ANULADA):
            return "error"
        return "pendiente"


class OrigenTratamiento(enum.Enum):
    IMPORTACION = "importacion"   # vino del Excel histórico
    SOLICITUD = "solicitud"       # nació dentro del sistema


class EstadoNormalizacion(enum.Enum):
    PENDIENTE = "pendiente"      # el nombre no encontró producto en el maestro
    AUTOMATICA = "automatica"    # resolvió por coincidencia exacta de clave
    CONFIRMADA = "confirmada"    # un usuario técnico la validó
    SIN_PRODUCTO = "sin_producto"  # se decidió que no corresponde a ningún producto


class ArchivoImportado(MarcasDeTiempo, db.Model):
    """Cada carga de Excel. Es el ancla de trazabilidad de todo lo que entra.

    Agregados respecto del diagrama: `hash_sha256` para detectar recargas del mismo
    archivo (idempotencia) y contadores de filas para poder mostrar un resumen real
    en la pantalla de resultado de importación.
    """

    __tablename__ = "archivo_importado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tamano_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    usuario = relationship("Usuario", back_populates="archivos")

    estado: Mapped[EstadoImportacion] = mapped_column(
        Enum(EstadoImportacion), default=EstadoImportacion.PENDIENTE, nullable=False, index=True
    )
    filas_leidas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filas_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filas_con_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Lista de {fila, columna, mensaje} para mostrar los errores sin volver a leer el archivo.
    detalle_errores: Mapped[list | None] = mapped_column(JSON)
    observaciones: Mapped[str | None] = mapped_column(Text)

    tratamientos = relationship("Tratamiento", back_populates="archivo")

    def __repr__(self) -> str:
        return f"<ArchivoImportado {self.nombre_archivo} {self.estado.value}>"


class Tratamiento(MarcasDeTiempo, db.Model):
    __tablename__ = "tratamiento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero_tratamiento: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    fecha: Mapped[date | None] = mapped_column(Date, index=True)

    archivo_id: Mapped[int | None] = mapped_column(ForeignKey("archivo_importado.id"), index=True)
    archivo = relationship("ArchivoImportado", back_populates="tratamientos")

    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), index=True)
    cliente = relationship(
        "Cliente", back_populates="tratamientos", foreign_keys=[cliente_id]
    )

    # --- Circuito de aprobación ---
    estado_flujo: Mapped[EstadoFlujo] = mapped_column(
        Enum(EstadoFlujo), default=EstadoFlujo.BORRADOR, nullable=False, index=True
    )
    origen: Mapped[OrigenTratamiento] = mapped_column(
        Enum(OrigenTratamiento), default=OrigenTratamiento.SOLICITUD, nullable=False, index=True
    )
    solicitado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), index=True)
    solicitado_por = relationship("Usuario", foreign_keys=[solicitado_por_id])
    aprobado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    aprobado_por = relationship("Usuario", foreign_keys=[aprobado_por_id])
    tomado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    tomado_por = relationship("Usuario", foreign_keys=[tomado_por_id])

    fecha_envio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_aprobacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_rechazo: Mapped[str | None] = mapped_column(Text)

    historial = relationship(
        "HistorialEstado", back_populates="tratamiento",
        cascade="all, delete-orphan", order_by="HistorialEstado.ocurrido_en",
    )

    # Segundo cliente: en el Excel, "Cliente" (quien pidió el ensayo) y
    # "CLIENTE SOFTLAND" (la cuenta del ERP) son entidades distintas, no dos
    # formas de escribir lo mismo. Se guardan por separado.
    cliente_softland_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), index=True)
    cliente_softland = relationship("Cliente", foreign_keys=[cliente_softland_id])

    pais: Mapped[str | None] = mapped_column(String(80), index=True)
    laboratorio: Mapped[str | None] = mapped_column(String(120), index=True)
    cultivo: Mapped[str | None] = mapped_column(String(120), index=True)
    momento_aplicacion: Mapped[str | None] = mapped_column(String(120))
    tasa_aplicacion: Mapped[float | None] = mapped_column(Numeric(10, 2))
    cantidad_ha: Mapped[float | None] = mapped_column(Numeric(12, 2))
    enlace: Mapped[str | None] = mapped_column(String(500))
    tipo_tratamiento: Mapped[str | None] = mapped_column(String(120), index=True)
    target: Mapped[str | None] = mapped_column(String(180))
    observaciones: Mapped[str | None] = mapped_column(Text)

    mezclas = relationship(
        "Mezcla", back_populates="tratamiento", cascade="all, delete-orphan"
    )
    resultado = relationship(
        "Resultado", back_populates="tratamiento", uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # El número de tratamiento es único en todo el sistema: el negocio confirma
        # que no se reutiliza entre años. Eso lo vuelve la clave natural del
        # histórico y permite reimportar el mismo archivo sin duplicar nada.
        UniqueConstraint("numero_tratamiento", name="uq_numero_tratamiento"),
        Index("ix_tratamiento_filtros", "fecha", "cultivo", "tipo_tratamiento"),
    )

    # `anio` y `mes` del diagrama original se derivan de `fecha`: guardarlos como
    # columnas propias abre la puerta a que queden desincronizados.
    @property
    def anio(self) -> int | None:
        return self.fecha.year if self.fecha else None

    @property
    def mes(self) -> int | None:
        return self.fecha.month if self.fecha else None

    def __repr__(self) -> str:
        return f"<Tratamiento {self.numero_tratamiento}>"


class Mezcla(MarcasDeTiempo, db.Model):
    """Combinación de productos ensayada dentro de un tratamiento.

    El diagrama original ataba Tratamiento 1—1 Mezcla. Se abre a 1—N: un mismo
    tratamiento suele ensayar más de una combinación, y con 1—1 la única salida
    sería duplicar el tratamiento entero.
    """

    __tablename__ = "mezcla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tratamiento_id: Mapped[int] = mapped_column(
        ForeignKey("tratamiento.id"), nullable=False, index=True
    )
    tratamiento = relationship("Tratamiento", back_populates="mezclas")

    nombre_mezcla: Mapped[str | None] = mapped_column(String(180))
    descripcion: Mapped[str | None] = mapped_column(Text)
    orden: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    detalles = relationship(
        "DetalleMezcla", back_populates="mezcla",
        cascade="all, delete-orphan", order_by="DetalleMezcla.orden_producto",
    )

    def __repr__(self) -> str:
        return f"<Mezcla {self.id} trat={self.tratamiento_id}>"


class DetalleMezcla(MarcasDeTiempo, db.Model):
    """Un producto dentro de una mezcla, tal como vino escrito y tal como se resolvió."""

    __tablename__ = "detalle_mezcla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mezcla_id: Mapped[int] = mapped_column(ForeignKey("mezcla.id"), nullable=False, index=True)
    mezcla = relationship("Mezcla", back_populates="detalles")

    orden_producto: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Dato crudo: nunca se pisa. Es la evidencia de qué decía el Excel.
    nombre_original: Mapped[str] = mapped_column(String(220), nullable=False)
    clave_normalizada: Mapped[str] = mapped_column(String(220), nullable=False, index=True)

    # Dato resuelto: el mapeo es la razón, el producto es el resultado cacheado
    # para poder filtrar por activo/categoría sin un JOIN extra en cada consulta.
    mapeo_id: Mapped[int | None] = mapped_column(ForeignKey("mapeo_producto.id"), index=True)
    mapeo = relationship("MapeoProducto", back_populates="detalles")
    producto_id: Mapped[int | None] = mapped_column(ForeignKey("producto.id"), index=True)
    producto = relationship("Producto", back_populates="usos")

    estado_normalizacion: Mapped[EstadoNormalizacion] = mapped_column(
        Enum(EstadoNormalizacion), default=EstadoNormalizacion.PENDIENTE,
        nullable=False, index=True,
    )

    dosis: Mapped[float | None] = mapped_column(Numeric(12, 4))
    unidad: Mapped[str | None] = mapped_column(String(20))

    def __repr__(self) -> str:
        return f"<DetalleMezcla {self.nombre_original}>"


class Resultado(MarcasDeTiempo, db.Model):
    """Medición del ensayo de compatibilidad de la mezcla."""

    __tablename__ = "resultado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tratamiento_id: Mapped[int] = mapped_column(
        ForeignKey("tratamiento.id"), nullable=False, unique=True, index=True
    )
    tratamiento = relationship("Tratamiento", back_populates="resultado")

    porcentaje_efectividad: Mapped[float | None] = mapped_column(Numeric(6, 2))
    ph_agua: Mapped[float | None] = mapped_column(Numeric(5, 2))
    dureza_agua: Mapped[float | None] = mapped_column(Numeric(8, 2))
    ph_mezcla: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # El laboratorio corrige el agua antes de armar la mezcla y vuelve a medir.
    agua_corregida: Mapped[bool | None] = mapped_column(Boolean)
    ph_agua_corregida: Mapped[float | None] = mapped_column(Numeric(5, 2))
    dureza_agua_corregida: Mapped[float | None] = mapped_column(Numeric(8, 2))

    observacion_cinco_min: Mapped[str | None] = mapped_column(Text)
    volumen_cinco_min: Mapped[str | None] = mapped_column(String(40))
    observacion_diez_min: Mapped[str | None] = mapped_column(Text)
    volumen_diez_min: Mapped[str | None] = mapped_column(String(40))

    suspensibilidad: Mapped[str | None] = mapped_column(String(120))

    # Granulometría: el Excel la mide por rango de micras, no como un valor único.
    granulometria: Mapped[str | None] = mapped_column(String(120))
    granulometria_300: Mapped[float | None] = mapped_column(Numeric(10, 4))
    granulometria_180: Mapped[float | None] = mapped_column(Numeric(10, 4))
    granulometria_150: Mapped[float | None] = mapped_column(Numeric(10, 4))
    granulometria_106: Mapped[float | None] = mapped_column(Numeric(10, 4))
    granulometria_53: Mapped[float | None] = mapped_column(Numeric(10, 4))

    dosis_compatibilizador: Mapped[str | None] = mapped_column(String(120))
    estado_resultado: Mapped[str | None] = mapped_column(String(60), index=True)

    def __repr__(self) -> str:
        return f"<Resultado trat={self.tratamiento_id}>"


class HistorialEstado(db.Model):
    """Cada paso del circuito, con su autor y su motivo.

    Es lo que hace auditable la aprobación: sin esto, "quién aprobó esta mezcla"
    solo se sabe mirando el último valor, y se pierde apenas hay una corrección.
    """

    __tablename__ = "historial_estado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tratamiento_id: Mapped[int] = mapped_column(
        ForeignKey("tratamiento.id"), nullable=False, index=True
    )
    tratamiento = relationship("Tratamiento", back_populates="historial")

    desde: Mapped[EstadoFlujo | None] = mapped_column(Enum(EstadoFlujo))
    hacia: Mapped[EstadoFlujo] = mapped_column(Enum(EstadoFlujo), nullable=False)
    accion: Mapped[str] = mapped_column(String(40), nullable=False)

    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), index=True)
    usuario = relationship("Usuario")

    motivo: Mapped[str | None] = mapped_column(Text)
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<HistorialEstado {self.accion} → {self.hacia.value}>"
