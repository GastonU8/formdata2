"""Roles y permisos, ahora editables desde la aplicación.

La decisión que ordena todo el módulo
-------------------------------------
Se separa en dos lo que antes era una sola tabla en el código:

* **El catálogo de capacidades sigue en el código** (`security/roles.py`). Cada
  capacidad existe porque hay una vista que la exige con `@requiere(...)`. Dejar
  que alguien invente capacidades desde una pantalla produciría permisos que no
  gobiernan nada, y —peor— daría la impresión de que sí.
* **La asignación de capacidades a roles vive en la base.** Eso es lo que cambia
  con la organización: que el supervisor también pueda importar, que exista un rol
  «Auditor» que solo lee. Eso no debería exigir tocar código ni redesplegar.

Qué se gana y qué se paga
-------------------------
Se gana flexibilidad real. Se paga con tres riesgos que hay que atajar
explícitamente, y que están atajados en `services/roles.py`:

1. **Quedarse afuera.** Si alguien le quita a todos los roles la capacidad de
   gestionar usuarios, no hay forma de volver desde la aplicación.
2. **Los permisos pasan a ser un objetivo.** Antes cambiarlos exigía acceso al
   código; ahora basta una sesión de administrador. Por eso cada cambio se audita
   con el antes y el después.
3. **Se pierde el «leer un archivo y saber quién puede qué».** Se compensa con la
   pantalla de roles, que muestra la matriz completa de un vistazo.
"""

from __future__ import annotations

import enum

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import MarcasDeTiempo


class ClaveRol(enum.StrEnum):
    """Los roles que trae el sistema de fábrica.

    Es `StrEnum` y no `Enum` a propósito: `ClaveRol.ADMINISTRADOR == "administrador"`
    da verdadero, así que el código puede comparar contra la clave guardada sin
    convertir de un lado a otro en cada comparación.

    Existe para dos cosas: sembrar la instalación y sostener las reglas que no
    dependen de la configuración (un rol del sistema no se borra). Los roles que
    cree la organización **no** están acá y no tienen por qué estarlo.
    """

    COMERCIAL = "comercial"
    GERENCIA = "gerencia"
    SUPERVISOR = "supervisor"
    TECNICO = "tecnico"
    ADMINISTRADOR = "administrador"


NOMBRES_DE_FABRICA = {
    ClaveRol.COMERCIAL: "Usuario comercial",
    ClaveRol.GERENCIA: "Gerencia / Dirección",
    ClaveRol.SUPERVISOR: "Supervisor",
    ClaveRol.TECNICO: "Usuario técnico",
    ClaveRol.ADMINISTRADOR: "Administrador del sistema",
}


class Rol(MarcasDeTiempo, db.Model):
    __tablename__ = "rol"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clave: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(400))

    # Un rol del sistema no se puede borrar ni renombrarle la clave: hay reglas del
    # código que lo nombran (por ejemplo, que el administrador exista al instalar).
    # Sus capacidades sí se pueden editar, como las de cualquier otro.
    es_del_sistema: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permisos = relationship(
        "PermisoRol",
        back_populates="rol",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    usuarios = relationship("Usuario", back_populates="rol")

    @property
    def etiqueta(self) -> str:
        """Cómo se muestra en pantalla. El nombre lo elige quien crea el rol."""
        return self.nombre

    @property
    def capacidades(self) -> set[str]:
        return {permiso.capacidad for permiso in self.permisos}

    def tiene(self, capacidad: str) -> bool:
        return capacidad in self.capacidades

    def __repr__(self) -> str:
        return f"<Rol {self.clave}>"


class PermisoRol(db.Model):
    """Una capacidad concedida a un rol.

    Tabla y no una columna JSON con la lista: la pregunta «¿qué roles pueden
    gestionar usuarios?» hay que hacerla en cada cambio de permisos para no dejar
    al sistema sin nadie que administre, y esa consulta sobre JSON no es portable
    entre SQLite y PostgreSQL.
    """

    __tablename__ = "permiso_rol"
    __table_args__ = (UniqueConstraint("rol_id", "capacidad", name="uq_permiso_rol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rol_id: Mapped[int] = mapped_column(ForeignKey("rol.id", ondelete="CASCADE"), index=True)
    capacidad: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    rol = relationship("Rol", back_populates="permisos")

    def __repr__(self) -> str:
        return f"<PermisoRol {self.rol_id}:{self.capacidad}>"
