"""Indicadores calculados, consultas al asistente y registro de auditoría."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import MarcasDeTiempo


class TipoIndicador(enum.Enum):
    CONTEO = "conteo"
    PORCENTAJE = "porcentaje"
    PROMEDIO = "promedio"
    RANKING = "ranking"


class Indicador(MarcasDeTiempo, db.Model):
    """Snapshot de una métrica del tablero.

    Agregado respecto del diagrama: el período y los filtros con los que se calculó.
    Un valor sin su ventana de cálculo no es comparable ni auditable.
    """

    __tablename__ = "indicador"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(180), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)

    tipo: Mapped[TipoIndicador] = mapped_column(Enum(TipoIndicador), nullable=False)
    valor: Mapped[float | None] = mapped_column(Numeric(16, 4))
    unidad: Mapped[str | None] = mapped_column(String(20))

    periodo_desde: Mapped[date | None] = mapped_column(Date)
    periodo_hasta: Mapped[date | None] = mapped_column(Date)
    filtros: Mapped[dict | None] = mapped_column(JSON)
    detalle: Mapped[dict | None] = mapped_column(JSON)

    fecha_generacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Indicador {self.codigo}={self.valor}>"


class EstadoRespuestaIA(enum.Enum):
    GENERADA = "generada"
    SIN_CONTEXTO = "sin_contexto"
    REQUIERE_REVISION = "requiere_revision"
    ERROR = "error"


class ConsultaIA(MarcasDeTiempo, db.Model):
    """Cada pregunta en lenguaje natural y lo que el sistema respondió.

    Agregados respecto del diagrama: modelo usado, latencia, y sobre todo `fuentes`
    (qué registros sustentaron la respuesta). Sin fuentes la respuesta no es
    verificable, y una respuesta técnica no verificable no sirve para decidir.
    """

    __tablename__ = "consulta_ia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    usuario = relationship("Usuario", back_populates="consultas_ia")

    pregunta: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[EstadoRespuestaIA] = mapped_column(
        Enum(EstadoRespuestaIA), default=EstadoRespuestaIA.GENERADA, nullable=False, index=True
    )

    herramientas_usadas: Mapped[list | None] = mapped_column(JSON)
    fuentes: Mapped[list | None] = mapped_column(JSON)

    proveedor: Mapped[str | None] = mapped_column(String(60))
    modelo: Mapped[str | None] = mapped_column(String(120))
    latencia_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_entrada: Mapped[int | None] = mapped_column(Integer)
    tokens_salida: Mapped[int | None] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<ConsultaIA {self.id} {self.estado.value}>"


class RegistroAuditoria(db.Model):
    """Bitácora append-only de acciones sensibles.

    Entidad nueva: el plan de negocio pide "mecanismos de auditoría" pero el modelo
    de datos no tenía dónde escribirlos. Esta tabla no se edita ni se borra desde
    la aplicación.
    """

    __tablename__ = "registro_auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ocurrido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), index=True)
    # Relación de solo lectura, cargada en lote. Sin ella la bitácora muestra «#5» en
    # lugar de un nombre, y una bitácora que hay que traducir a mano no se usa.
    # `selectin` trae todos los usuarios de la página en una sola consulta extra, en
    # vez de una por renglón.
    usuario = relationship("Usuario", lazy="selectin")
    email_intentado: Mapped[str | None] = mapped_column(String(180))

    accion: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entidad: Mapped[str | None] = mapped_column(String(80))
    entidad_id: Mapped[str | None] = mapped_column(String(60))
    exito: Mapped[bool] = mapped_column(default=True, nullable=False)

    ip_origen: Mapped[str | None] = mapped_column(String(60))
    agente_usuario: Mapped[str | None] = mapped_column(String(255))
    datos: Mapped[dict | None] = mapped_column(JSON)

    def __repr__(self) -> str:
        return f"<Auditoria {self.accion} u={self.usuario_id}>"
