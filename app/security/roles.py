"""Autorización basada en capacidades.

La autenticación dice quién sos; esto dice qué podés tocar. Los permisos se
declaran como capacidades y no como roles sueltos desperdigados por las vistas.

Qué está en el código y qué en la base
--------------------------------------
* **El catálogo de capacidades está acá** (`CAPACIDADES`). Cada entrada existe
  porque hay una vista que la exige con `@requiere(...)`. Dejar que se inventen
  capacidades desde una pantalla produciría permisos que no gobiernan nada y, peor,
  darían la impresión de que sí.
* **Qué rol tiene cada capacidad está en la base**, editable desde
  `/administracion/roles`. `ASIGNACION_DE_FABRICA` es solo la semilla de una
  instalación nueva: una vez sembrada, la verdad está en la tabla.

Costo de rendimiento y cómo se paga
-----------------------------------
`puede()` se llama muchas veces por petición —cada bloque de plantilla que muestra
u oculta un botón—. Leer la base en cada una sería absurdo, así que las capacidades
del rol se resuelven una vez por petición y quedan en `g`. Fuera de una petición
(comandos de consola, pruebas) se consulta directo: ahí no hay un ciclo donde
cachear sin arriesgar datos viejos.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, g, has_request_context
from flask_login import current_user

from ..models import ClaveRol

# ------------------------------------------------------ catálogo de capacidades

# Capacidad → para qué sirve. El texto se muestra en la pantalla de roles: quien
# edita permisos no tiene por qué deducir qué hace «anular_solicitud».
CAPACIDADES: dict[str, str] = {
    # Consulta
    "consultar_tratamientos": "Buscar y ver ensayos y el maestro de productos",
    "ver_indicadores": "Ver el tablero con las métricas",
    "exportar_reportes": "Descargar Excel y PDF de lo que está viendo",
    "consultar_ia": "Preguntarle al asistente",
    # Circuito de solicitudes
    "ver_bandeja": "Ver la bandeja de solicitudes",
    "registrar_solicitud": "Pedir ensayos nuevos",
    "aprobar_solicitud": "Aprobar o rechazar solicitudes",
    "ejecutar_ensayo": "Tomar ensayos y cargar resultados",
    "anular_solicitud": "Anular una solicitud",
    # Datos maestros
    "importar_excel": "Importar el Excel histórico",
    "normalizar_productos": "Resolver los productos sin mapear",
    "administrar_maestro": "Dar de alta y editar productos del maestro",
    # Administración
    "gestionar_usuarios": "Crear, editar, suspender y dar de baja usuarios",
    "gestionar_roles": "Crear roles y cambiar qué puede hacer cada uno",
    "ver_auditoria": "Consultar la bitácora de auditoría",
}

# Agrupación solo para la pantalla: quince casillas sin orden no se leen.
GRUPOS = {
    "Consulta": ["consultar_tratamientos", "ver_indicadores", "exportar_reportes", "consultar_ia"],
    "Circuito de solicitudes": [
        "ver_bandeja", "registrar_solicitud", "aprobar_solicitud", "ejecutar_ensayo",
        "anular_solicitud",
    ],
    "Datos maestros": ["importar_excel", "normalizar_productos", "administrar_maestro"],
    "Administración": ["gestionar_usuarios", "gestionar_roles", "ver_auditoria"],
}

# Las capacidades sin las que el sistema no se puede administrar. Ningún cambio de
# permisos puede dejar a la instalación sin al menos un rol activo que las tenga:
# no habría manera de volver desde la aplicación.
CAPACIDADES_CRITICAS = frozenset({"gestionar_usuarios", "gestionar_roles"})

_CONSULTA = [
    "consultar_tratamientos", "ver_indicadores", "exportar_reportes", "consultar_ia",
    "ver_bandeja",
]

# Semilla de una instalación nueva. Después de sembrar, la verdad está en la base.
ASIGNACION_DE_FABRICA: dict[ClaveRol, list[str]] = {
    ClaveRol.COMERCIAL: [*_CONSULTA, "registrar_solicitud"],
    ClaveRol.GERENCIA: [*_CONSULTA],
    ClaveRol.SUPERVISOR: [*_CONSULTA, "aprobar_solicitud", "anular_solicitud"],
    ClaveRol.TECNICO: [
        *_CONSULTA, "ejecutar_ensayo", "importar_excel", "normalizar_productos",
        "administrar_maestro",
    ],
    ClaveRol.ADMINISTRADOR: list(CAPACIDADES),
}


# ------------------------------------------------------------------ resolución


def capacidades_de(rol) -> set[str]:
    """Las capacidades de un rol, leídas de la base."""
    return rol.capacidades if rol is not None else set()


def _capacidades_del_usuario(usuario) -> set[str]:
    rol = getattr(usuario, "rol", None)
    if rol is None:
        return set()

    if not has_request_context():
        return capacidades_de(rol)

    # Una vez por petición y por rol. La clave incluye el id del rol para que dos
    # usuarios distintos dentro de la misma petición no se pisen el resultado.
    if not hasattr(g, "_capacidades"):
        g._capacidades = {}
    if rol.id not in g._capacidades:
        g._capacidades[rol.id] = capacidades_de(rol)
    return g._capacidades[rol.id]


def puede(capacidad: str, usuario=None) -> bool:
    """¿Este usuario tiene esta capacidad? Se expone también a las plantillas."""
    if capacidad not in CAPACIDADES:
        # Sigue siendo un error de programación y no de configuración: una vista que
        # exige una capacidad inexistente quedaría abierta o cerrada por accidente.
        raise KeyError(f"Capacidad no declarada: {capacidad}")

    usuario = usuario or current_user
    if not getattr(usuario, "is_authenticated", False):
        return False

    return capacidad in _capacidades_del_usuario(usuario)


def requiere(capacidad: str):
    """Decorador de vista. Va siempre después de @login_required."""

    def envoltorio(vista):
        @wraps(vista)
        def interna(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not puede(capacidad):
                abort(403)
            return vista(*args, **kwargs)

        return interna

    return envoltorio


def olvidar_cache() -> None:
    """Descarta las capacidades cacheadas de esta petición.

    Se llama después de guardar un cambio de permisos: sin esto, la pantalla que se
    dibuja inmediatamente después mostraría el estado anterior y parecería que no se
    guardó.
    """
    if has_request_context() and hasattr(g, "_capacidades"):
        del g._capacidades
