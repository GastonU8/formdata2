"""Administración de roles y permisos.

Todo lo de acá gira alrededor de una pregunta: **¿este cambio deja al sistema sin
forma de volver atrás?** Poder editar permisos desde la aplicación es cómodo hasta
el minuto en que alguien se quita a sí mismo la capacidad de editarlos.

Las cuatro salvaguardas:

1. Ningún cambio puede dejar la instalación sin un rol activo que tenga las
   capacidades críticas (gestionar usuarios y gestionar roles).
2. Un rol del sistema no se borra ni se le cambia la clave: hay reglas del código
   que la nombran.
3. Un rol con usuarios asignados no se borra: habría que decidir por ellos a qué
   rol pasan, y esa es una decisión de la organización, no del sistema.
4. Todo cambio de permisos se audita con el antes y el después. Un permiso que se
   puede cambiar desde una pantalla es un objetivo; sin el registro, un cambio
   silencioso es indetectable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import ClaveRol, EstadoUsuario, PermisoRol, Rol, Usuario
from ..security import registrar
from ..security.roles import (
    ASIGNACION_DE_FABRICA,
    CAPACIDADES,
    CAPACIDADES_CRITICAS,
    olvidar_cache,
)
from ..models.rol import NOMBRES_DE_FABRICA
from ..unidad_de_trabajo import transaccion

LARGO_CLAVE = 60


class DatosInvalidos(Exception):
    def __init__(self, errores: list[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


# --------------------------------------------------------------------- siembra


def sincronizar_roles() -> int:
    """Crea los roles de fábrica que falten. **No toca los que ya existen.**

    Esa distinción es la que hace que el comando sea seguro de correr siempre: si
    reasignara las capacidades de fábrica en cada arranque, desharía en silencio
    cada permiso que un administrador hubiera quitado a propósito.
    """
    creados = 0
    for clave, capacidades in ASIGNACION_DE_FABRICA.items():
        if db.session.scalar(select(Rol).where(Rol.clave == clave)) is not None:
            continue
        rol = Rol(
            clave=str(clave),
            nombre=NOMBRES_DE_FABRICA[clave],
            es_del_sistema=True,
            descripcion="Rol que trae el sistema de fábrica.",
        )
        rol.permisos = [PermisoRol(capacidad=c) for c in capacidades]
        db.session.add(rol)
        creados += 1

    if creados:
        db.session.commit()
    return creados


def obtener(clave: str | ClaveRol) -> Rol | None:
    return db.session.scalar(select(Rol).where(Rol.clave == str(clave)))


# --------------------------------------------------------------------- lectura


@dataclass(frozen=True)
class FilaDeRol:
    rol: Rol
    usuarios_activos: int
    usuarios_totales: int


def listar() -> list[FilaDeRol]:
    """Todos los roles, con cuánta gente los usa. Los del sistema primero."""
    conteos = dict(
        db.session.execute(
            select(Usuario.rol_id, func.count(Usuario.id)).group_by(Usuario.rol_id)
        ).all()
    )
    activos = dict(
        db.session.execute(
            select(Usuario.rol_id, func.count(Usuario.id))
            .where(Usuario.estado == EstadoUsuario.ACTIVO)
            .group_by(Usuario.rol_id)
        ).all()
    )
    roles = db.session.scalars(
        select(Rol).order_by(Rol.es_del_sistema.desc(), Rol.nombre)
    ).all()
    return [
        FilaDeRol(
            rol=rol,
            usuarios_activos=activos.get(rol.id, 0),
            usuarios_totales=conteos.get(rol.id, 0),
        )
        for rol in roles
    ]


def matriz() -> dict[str, dict[str, bool]]:
    """La tabla completa de quién puede qué, para verla de un vistazo.

    Compensa lo que se perdió al sacar los permisos del código: antes se abría un
    archivo y estaba todo; ahora está acá.
    """
    return {
        rol.clave: {capacidad: rol.tiene(capacidad) for capacidad in CAPACIDADES}
        for rol in db.session.scalars(select(Rol).order_by(Rol.nombre))
    }


def roles_con(capacidad: str, excepto: Rol | None = None) -> list[Rol]:
    consulta = (
        select(Rol)
        .join(PermisoRol, PermisoRol.rol_id == Rol.id)
        .where(PermisoRol.capacidad == capacidad)
    )
    if excepto is not None:
        consulta = consulta.where(Rol.id != excepto.id)
    return list(db.session.scalars(consulta))


# ---------------------------------------------------------------- salvaguardas


def _clave_desde(nombre: str) -> str:
    """Convierte «Auditor de calidad» en «auditor-de-calidad»."""
    limpio = unicodedata.normalize("NFKD", nombre.lower())
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    limpio = re.sub(r"[^a-z0-9]+", "-", limpio).strip("-")
    return limpio[:LARGO_CLAVE]


def _validar_capacidades(capacidades: list[str]) -> set[str]:
    """Descarta lo que no esté en el catálogo. Llega de un formulario: no es confiable."""
    return {c for c in capacidades if c in CAPACIDADES}


def _verificar_que_alguien_pueda_administrar(rol: Rol, capacidades_nuevas: set[str]) -> None:
    """Impide el cambio que deja al sistema sin quien lo administre.

    Se mira si **algún otro rol con usuarios activos** conserva cada capacidad
    crítica. Un rol que tiene el permiso pero no tiene a nadie asignado no sirve
    para destrabar nada: nadie puede entrar con él.
    """
    faltantes = []
    for capacidad in sorted(CAPACIDADES_CRITICAS):
        if capacidad in capacidades_nuevas:
            continue
        otros = [
            otro
            for otro in roles_con(capacidad, excepto=rol)
            if any(u.estado is EstadoUsuario.ACTIVO for u in otro.usuarios)
        ]
        if not otros:
            faltantes.append(CAPACIDADES[capacidad])

    if faltantes:
        raise DatosInvalidos(
            [
                "Este es el único rol con usuarios activos que puede: "
                + "; ".join(faltantes)
                + ". Dale esa capacidad a otro rol antes de quitársela a este, o el "
                "sistema queda sin nadie que pueda administrarlo."
            ]
        )


# ---------------------------------------------------------------------- altas


def crear(nombre: str, descripcion: str, capacidades: list[str], autor) -> Rol:
    nombre = (nombre or "").strip()[:120]
    errores: list[str] = []

    if not nombre:
        errores.append("El nombre del rol es obligatorio.")
    clave = _clave_desde(nombre)
    if not clave:
        errores.append("El nombre tiene que tener al menos una letra o número.")
    elif obtener(clave) is not None:
        errores.append(f"Ya existe un rol que se normaliza como «{clave}».")
    if errores:
        raise DatosInvalidos(errores)

    elegidas = _validar_capacidades(capacidades)

    with transaccion():
        rol = Rol(
            clave=clave,
            nombre=nombre,
            descripcion=(descripcion or "").strip()[:400] or None,
            es_del_sistema=False,
        )
        rol.permisos = [PermisoRol(capacidad=c) for c in sorted(elegidas)]
        db.session.add(rol)
        db.session.flush()

        registrar(
            "rol_creado",
            entidad="rol",
            entidad_id=rol.id,
            usuario_id=getattr(autor, "id", None),
            datos={"clave": clave, "capacidades": sorted(elegidas)},
            confirmar=False,
        )
    olvidar_cache()
    return rol


def editar(rol: Rol, nombre: str, descripcion: str, capacidades: list[str], autor) -> Rol:
    """Renombra y reasigna capacidades. La clave nunca cambia.

    La clave es lo que el código nombra y lo que quedó escrito en la auditoría; si
    cambiara, los registros viejos apuntarían a un rol que ya no existe con ese
    nombre. El nombre visible sí se puede cambiar cuando se quiera.
    """
    nombre = (nombre or "").strip()[:120]
    if not nombre:
        raise DatosInvalidos(["El nombre del rol es obligatorio."])

    elegidas = _validar_capacidades(capacidades)
    anteriores = rol.capacidades

    if elegidas != anteriores:
        _verificar_que_alguien_pueda_administrar(rol, elegidas)

    with transaccion():
        rol.nombre = nombre
        rol.descripcion = (descripcion or "").strip()[:400] or None

        for permiso in list(rol.permisos):
            if permiso.capacidad not in elegidas:
                rol.permisos.remove(permiso)
        for capacidad in sorted(elegidas - anteriores):
            rol.permisos.append(PermisoRol(capacidad=capacidad))

        registrar(
            "rol_permisos",
            entidad="rol",
            entidad_id=rol.id,
            usuario_id=getattr(autor, "id", None),
            # El antes y el después, no solo el después: sin el estado previo, el
            # registro no permite reconstruir qué se concedió y qué se quitó.
            datos={
                "clave": rol.clave,
                "agregadas": sorted(elegidas - anteriores),
                "quitadas": sorted(anteriores - elegidas),
            },
            confirmar=False,
        )
    olvidar_cache()
    return rol


def eliminar(rol: Rol, autor) -> None:
    errores = []
    if rol.es_del_sistema:
        errores.append(
            "Los roles que trae el sistema no se borran. Si no lo usás, quitale las "
            "capacidades y no le asignes usuarios."
        )
    if rol.usuarios:
        errores.append(
            f"Hay {len(rol.usuarios)} usuario(s) con este rol. Cambiáselos antes de borrarlo."
        )
    if errores:
        raise DatosInvalidos(errores)

    _verificar_que_alguien_pueda_administrar(rol, set())

    clave, identificador = rol.clave, rol.id
    with transaccion():
        db.session.delete(rol)
        registrar(
            "rol_eliminado",
            entidad="rol",
            entidad_id=identificador,
            usuario_id=getattr(autor, "id", None),
            datos={"clave": clave},
            confirmar=False,
        )
    olvidar_cache()
