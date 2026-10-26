"""Administración de usuarios.

Tres reglas que ordenan este módulo, todas por el mismo motivo: en un sistema con
auditoría, **el usuario es la clave de todo lo que quedó registrado**. Borrarlo o
reciclarlo rompe el histórico.

* **No se borra.** Un usuario que aprobó ensayos en 2024 sigue explicando esas
  aprobaciones. Se le da de baja: no puede entrar y su nombre sigue en la bitácora.
* **No se recicla el email.** Ni siquiera después de una baja. Si mañana entra otra
  persona con la misma dirección, el histórico diría que ella aprobó cosas que
  aprobó otra.
* **La contraseña la elige su dueño.** El administrador da de alta con una clave
  inicial de un solo uso y el sistema obliga a cambiarla en el primer ingreso. Un
  administrador que conoce la contraseña de otro puede actuar en su nombre, y eso
  vacía de sentido a toda la auditoría.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import EstadoUsuario, RegistroAuditoria, Rol, Usuario
from ..security.roles import CAPACIDADES_CRITICAS
from ..security import registrar
from ..unidad_de_trabajo import transaccion

LARGO_CLAVE_INICIAL = 14
# Sin caracteres que se confunden al dictarlos por teléfono: O/0, l/1/I.
ALFABETO = "".join(c for c in string.ascii_letters + string.digits if c not in "O0lI1")


class DatosInvalidos(Exception):
    def __init__(self, errores: list[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


def clave_inicial() -> str:
    """Una contraseña de un solo uso, legible para dictarla.

    Se usa `secrets` y no `random`: el generador de `random` es predecible a partir
    de unas pocas salidas, y esto es material de autenticación.
    """
    return "".join(secrets.choice(ALFABETO) for _ in range(LARGO_CLAVE_INICIAL))


def _validar(nombre: str, email: str, rol: str, actual: Usuario | None = None) -> tuple[str, str, Rol]:
    errores: list[str] = []

    nombre = (nombre or "").strip()[:120]
    email = (email or "").strip().lower()[:180]

    if not nombre:
        errores.append("El nombre es obligatorio.")
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        errores.append("El email no parece válido.")

    # El rol se busca en la tabla y no en un enum: la organización puede haber
    # creado roles propios desde la pantalla de administración.
    rol_elegido = db.session.scalar(select(Rol).where(Rol.clave == str(rol or "").strip()))
    if rol_elegido is None:
        errores.append("El rol elegido no existe.")

    if email:
        # La búsqueda incluye a los dados de baja a propósito: el email no se recicla.
        existente = db.session.scalar(select(Usuario).where(func.lower(Usuario.email) == email))
        if existente is not None and (actual is None or existente.id != actual.id):
            errores.append(
                "Ya hay un usuario con ese email, activo o dado de baja. "
                "Los emails no se reutilizan para no romper el histórico de auditoría."
            )

    if errores:
        raise DatosInvalidos(errores)
    return nombre, email, rol_elegido


@dataclass(frozen=True)
class Alta:
    usuario: Usuario
    clave_inicial: str


def crear(nombre: str, email: str, rol: str, autor) -> Alta:
    """Da de alta y devuelve la clave inicial **una sola vez**.

    La clave no se guarda en claro ni se vuelve a mostrar: si se pierde, el
    administrador la restablece, y ese restablecimiento queda auditado. Mostrarla
    de nuevo en una pantalla sería dejarla al alcance de cualquiera que abra esa
    pantalla después.
    """
    nombre, email, rol_elegido = _validar(nombre, email, rol)
    clave = clave_inicial()

    with transaccion():
        usuario = Usuario(
            nombre=nombre,
            email=email,
            rol=rol_elegido,
            estado=EstadoUsuario.ACTIVO,
            debe_cambiar_password=True,
        )
        usuario.definir_password(clave)
        db.session.add(usuario)
        db.session.flush()

        registrar(
            "usuario_creado",
            entidad="usuario",
            entidad_id=usuario.id,
            usuario_id=getattr(autor, "id", None),
            datos={"email": email, "rol": rol_elegido.clave},
            confirmar=False,
        )

    return Alta(usuario=usuario, clave_inicial=clave)


def editar(usuario: Usuario, nombre: str, email: str, rol: str, autor) -> Usuario:
    nombre, email, rol_elegido = _validar(nombre, email, rol, actual=usuario)

    if usuario.rol_id != rol_elegido.id:
        _verificar_que_quede_quien_administre(usuario, rol_nuevo=rol_elegido)

    with transaccion():
        cambios = []
        if usuario.rol_id != rol_elegido.id:
            cambios.append(f"rol: {usuario.rol.clave} → {rol_elegido.clave}")
        if usuario.email != email:
            cambios.append("email")

        usuario.nombre = nombre
        usuario.email = email
        usuario.rol = rol_elegido

        registrar(
            "usuario_editado",
            entidad="usuario",
            entidad_id=usuario.id,
            usuario_id=getattr(autor, "id", None),
            datos={"cambios": cambios},
            confirmar=False,
        )
    return usuario


def cambiar_estado(usuario: Usuario, estado: EstadoUsuario, autor) -> Usuario:
    """Suspende, da de baja o reactiva. Nunca borra.

    Suspendido y baja se distinguen a propósito: la suspensión es temporal (alguien
    de licencia, una cuenta bajo sospecha) y la baja es definitiva. Tratarlas igual
    obligaría a adivinar después por qué se cerró una cuenta.
    """
    if estado is not EstadoUsuario.ACTIVO:
        _verificar_que_quede_quien_administre(usuario)

    with transaccion():
        usuario.estado = estado
        if estado is not EstadoUsuario.ACTIVO:
            # Cerrarle la puerta también del lado del bloqueo por intentos: si vuelve
            # a estar activo, que arranque limpio.
            usuario.intentos_fallidos = 0
            usuario.bloqueado_hasta = None

        registrar(
            "usuario_estado",
            entidad="usuario",
            entidad_id=usuario.id,
            usuario_id=getattr(autor, "id", None),
            datos={"estado": estado.value, "email": usuario.email},
            confirmar=False,
        )
    return usuario


def restablecer_password(usuario: Usuario, autor) -> str:
    """Genera una clave nueva de un solo uso y obliga a cambiarla al entrar."""
    clave = clave_inicial()
    with transaccion():
        usuario.definir_password(clave)
        usuario.debe_cambiar_password = True
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None

        registrar(
            "password_restablecida",
            entidad="usuario",
            entidad_id=usuario.id,
            usuario_id=getattr(autor, "id", None),
            datos={"email": usuario.email},
            confirmar=False,
        )
    return clave


def desbloquear(usuario: Usuario, autor) -> Usuario:
    """Levanta el bloqueo por intentos fallidos sin tocar la contraseña.

    Separado del restablecimiento porque son cosas distintas: alguien que se
    equivocó cinco veces y ya recordó su clave no tiene por qué elegir una nueva.
    """
    with transaccion():
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        registrar(
            "usuario_desbloqueado",
            entidad="usuario",
            entidad_id=usuario.id,
            usuario_id=getattr(autor, "id", None),
            datos={"email": usuario.email},
            confirmar=False,
        )
    return usuario


def _verificar_que_quede_quien_administre(usuario: Usuario, rol_nuevo: Rol | None = None) -> None:
    """Impide quedarse sin nadie que pueda administrar el sistema.

    Antes la regla era «no dejar sin administradores», mirando el rol por su nombre.
    Con roles editables eso ya no alcanza: puede haber un rol «Coordinación» que
    tenga la capacidad de gestionar usuarios y ningún «administrador» activo, y sería
    una instalación perfectamente sana. Lo que importa no es cómo se llama el rol
    sino **si queda alguien activo con las capacidades críticas**.
    """
    faltantes = []
    for capacidad in sorted(CAPACIDADES_CRITICAS):
        # Se cuenta a los demás usuarios activos cuyo rol tenga la capacidad; y si
        # este usuario sigue activo con un rol nuevo que la conserva, también cuenta.
        otros = db.session.scalars(
            select(Usuario).where(
                Usuario.estado == EstadoUsuario.ACTIVO, Usuario.id != usuario.id
            )
        ).all()
        queda = any(u.rol is not None and u.rol.tiene(capacidad) for u in otros)
        if not queda and rol_nuevo is not None and rol_nuevo.tiene(capacidad):
            queda = True
        if not queda:
            faltantes.append(capacidad)

    if faltantes:
        raise DatosInvalidos(
            [
                "Es el único usuario activo que puede administrar el sistema "
                f"({', '.join(faltantes)}). Designá a otro antes de hacer este cambio."
            ]
        )


# ------------------------------------------------------------------- consulta


def listar(texto: str = "", rol: str = "", estado: str = "") -> list[Usuario]:
    consulta = select(Usuario).order_by(Usuario.estado, Usuario.nombre)

    texto = (texto or "").strip()
    if texto:
        patron = f"%{texto}%"
        consulta = consulta.where(
            db.or_(Usuario.nombre.ilike(patron), Usuario.email.ilike(patron))
        )
    if rol:
        consulta = consulta.where(Usuario.rol.has(Rol.clave == rol))
    if estado:
        try:
            consulta = consulta.where(Usuario.estado == EstadoUsuario(estado))
        except ValueError:
            pass

    return list(db.session.scalars(consulta))


def resumen() -> dict:
    filas = db.session.execute(
        select(Usuario.estado, func.count(Usuario.id)).group_by(Usuario.estado)
    ).all()
    conteo = {estado.value: cantidad for estado, cantidad in filas}
    return {
        "activos": conteo.get("activo", 0),
        "suspendidos": conteo.get("suspendido", 0),
        "bajas": conteo.get("baja", 0),
        "bloqueados": db.session.scalar(
            select(func.count(Usuario.id)).where(Usuario.bloqueado_hasta.is_not(None))
        ) or 0,
    }


def actividad(usuario: Usuario, limite: int = 15) -> list[RegistroAuditoria]:
    """Lo último que hizo esta persona, para poder responder «quién tocó esto»."""
    return list(
        db.session.scalars(
            select(RegistroAuditoria)
            .where(RegistroAuditoria.usuario_id == usuario.id)
            .order_by(RegistroAuditoria.ocurrido_en.desc())
            .limit(limite)
        )
    )
