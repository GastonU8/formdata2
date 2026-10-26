"""Piezas comunes a todos los modelos."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def ahora() -> datetime:
    """Momento actual en UTC. Guardar siempre en UTC y formatear al mostrar."""
    return datetime.now(timezone.utc)


def como_utc(valor: datetime | None) -> datetime | None:
    """Devuelve el valor con zona horaria.

    SQLite no guarda el offset: lo que se escribió como UTC vuelve como naive y
    no se puede comparar con `ahora()`. PostgreSQL sí lo conserva. Esta función
    hace que el código de dominio se comporte igual en los dos motores.
    """
    if valor is None or valor.tzinfo is not None:
        return valor
    return valor.replace(tzinfo=timezone.utc)


class MarcasDeTiempo:
    """Mixin de auditoría mínima: cuándo se creó y cuándo se tocó por última vez."""

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
