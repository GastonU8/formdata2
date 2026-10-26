"""Usuario del sistema.

Diferencia respecto del diagrama de clases original: la entidad Usuario no tenía
ningún atributo de credencial. Sin hash de contraseña, estado de bloqueo y
registro de último acceso no hay forma de sostener el requisito de "sistema
seguro" ni el caso de uso "Gestionar usuarios y permisos".
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta

from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import extensions
from ..extensions import db
from .base import MarcasDeTiempo, ahora, como_utc


class EstadoUsuario(enum.Enum):
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    BAJA = "baja"


class Usuario(UserMixin, MarcasDeTiempo, db.Model):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # El rol dejó de ser un enum fijo: ahora es una fila de `rol`, editable desde la
    # aplicación. Se carga junto con el usuario porque `puede()` lo necesita en cada
    # petición y traerlo aparte sería una consulta más por pantalla.
    rol_id: Mapped[int] = mapped_column(ForeignKey("rol.id"), nullable=False, index=True)
    rol = relationship("Rol", back_populates="usuarios", lazy="joined")
    estado: Mapped[EstadoUsuario] = mapped_column(
        Enum(EstadoUsuario), nullable=False, default=EstadoUsuario.ACTIVO
    )

    debe_cambiar_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    archivos = relationship("ArchivoImportado", back_populates="usuario")
    consultas_ia = relationship("ConsultaIA", back_populates="usuario")

    # ------------------------------------------------------------------ auth

    def definir_password(self, password: str) -> None:
        # Se accede por el módulo y no por el nombre importado: `configurar_hasher`
        # reemplaza el objeto en `extensions`, y un `from ... import hasher` habría
        # dejado atado el original en el momento de importar este archivo.
        self.password_hash = extensions.hasher.hash(password)

    def password_es_correcta(self, password: str) -> bool:
        try:
            extensions.hasher.verify(self.password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        # Si los parámetros de costo cambiaron, se rehashea de forma transparente.
        if extensions.hasher.check_needs_rehash(self.password_hash):
            self.definir_password(password)
        return True

    @property
    def esta_bloqueado(self) -> bool:
        limite = como_utc(self.bloqueado_hasta)
        return limite is not None and limite > ahora()

    def registrar_intento_fallido(self, maximo: int, minutos_bloqueo: int) -> None:
        self.intentos_fallidos += 1
        if self.intentos_fallidos >= maximo:
            self.bloqueado_hasta = ahora() + timedelta(minutes=minutos_bloqueo)
            self.intentos_fallidos = 0

    def registrar_ingreso_exitoso(self) -> None:
        self.intentos_fallidos = 0
        self.bloqueado_hasta = None
        self.ultimo_acceso = ahora()

    # --------------------------------------------------------------- permisos

    @property
    def is_active(self) -> bool:  # lo consulta Flask-Login
        return self.estado is EstadoUsuario.ACTIVO and not self.esta_bloqueado

    @property
    def iniciales(self) -> str:
        partes = [p for p in self.nombre.split() if p]
        return "".join(p[0] for p in partes[:2]).upper() or "??"

    def __repr__(self) -> str:
        return f"<Usuario {self.email} rol={self.rol.clave if self.rol else '?'}>"
