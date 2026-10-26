"""Escritura de la bitácora y endurecimiento de las respuestas HTTP."""

from __future__ import annotations

from flask import Flask, has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import RegistroAuditoria, ahora


def registrar(
    accion: str,
    *,
    exito: bool = True,
    usuario_id: int | None = None,
    entidad: str | None = None,
    entidad_id: str | int | None = None,
    email_intentado: str | None = None,
    datos: dict | None = None,
    confirmar: bool = True,
) -> None:
    """Deja constancia de una acción sensible.

    Nunca guarda contraseñas ni tokens: `datos` es para contexto de negocio
    (cuántas filas se importaron, qué producto se mapeó), no para credenciales.
    """
    # La auditoría también tiene que funcionar fuera de una petición HTTP: desde un
    # comando de consola o desde el procesamiento de una importación en segundo
    # plano. En esos casos simplemente no hay IP ni navegador que registrar.
    if usuario_id is None and has_request_context():
        usuario_id = current_user.id if getattr(current_user, "is_authenticated", False) else None
    if has_request_context():
        ip_origen = (request.headers.get("X-Forwarded-For", request.remote_addr) or "")[:60]
        agente = (request.headers.get("User-Agent") or "")[:255]
    else:
        ip_origen, agente = None, "proceso interno"

    entrada = RegistroAuditoria(
        ocurrido_en=ahora(),
        usuario_id=usuario_id,
        email_intentado=email_intentado,
        accion=accion,
        entidad=entidad,
        entidad_id=str(entidad_id) if entidad_id is not None else None,
        exito=exito,
        ip_origen=ip_origen,
        agente_usuario=agente,
        datos=datos,
    )
    db.session.add(entrada)
    if confirmar:
        db.session.commit()


CSP = "; ".join(
    [
        "default-src 'self'",
        # unsafe-inline solo para estilos: los scripts propios van en archivos.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        # Todo el JavaScript se sirve desde acá: la política puede cerrarse a 'self'
        # y ninguna CDN queda en la cadena de confianza.
        "script-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)


def instalar_cabeceras(app: Flask) -> None:
    """Cabeceras que cierran clases enteras de ataque sin tocar el código de negocio."""

    @app.after_request
    def _cabeceras(respuesta):
        respuesta.headers.setdefault("Content-Security-Policy", CSP)
        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("X-Frame-Options", "DENY")
        respuesta.headers.setdefault("Referrer-Policy", "same-origin")
        respuesta.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if app.config.get("SESSION_COOKIE_SECURE"):
            respuesta.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return respuesta
