"""Pantalla del asistente.

La conversación no se guarda en la sesión ni se encadena entre preguntas: cada
consulta se resuelve sola. Es una decisión, no una simplificación pendiente. Con
memoria conversacional, la respuesta a la pregunta 5 depende de lo que el modelo
entendió en la 2, y verificar una cifra obliga a releer todo el hilo. Acá cada
respuesta se sostiene sola con sus propias fuentes, que es lo que hace falta cuando
alguien va a apoyar una decisión técnica en ella.
"""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from ...extensions import limiter
from ...security import requiere
from ...services import asistente

bp = Blueprint("asistente", __name__, url_prefix="/asistente")

SUGERENCIAS = [
    "¿Cuántos ensayos hay de soja?",
    "¿Cuáles son los productos más usados?",
    "¿Cómo está la normalización del repositorio?",
    "Repartí los ensayos por observación",
    "¿Cuál es el pH de mezcla promedio?",
]


class FormularioPregunta(FlaskForm):
    """Solo aporta el token CSRF: el campo se lee a mano y se recorta en el servicio."""


def _contexto_base() -> dict:
    return {
        "formulario": FormularioPregunta(),
        "sugerencias": SUGERENCIAS,
        "historial": asistente.historial(current_user),
        "modo": current_app.config.get("IA_PROVEEDOR", "ninguno"),
    }


@bp.get("/")
@login_required
@requiere("consultar_ia")
def inicio():
    # `pregunta` puede venir por la URL desde una sugerencia: se deja escrita en el
    # campo, no se ejecuta. Una consulta que se dispara sola al abrir un enlace
    # sería una acción con efecto (queda registrada, puede costar dinero) hecha por
    # un GET, que es justamente lo que un GET no debe hacer.
    return render_template(
        "asistente/inicio.html",
        pregunta=(request.args.get("pregunta") or "").strip()[:500],
        **_contexto_base(),
    )


@bp.post("/preguntar")
@login_required
@requiere("consultar_ia")
# El asistente puede costar dinero por consulta y siempre cuesta tiempo de base:
# el límite protege el presupuesto y evita que una pestaña con recarga automática
# tire abajo el servidor.
@limiter.limit("12 per minute; 120 per hour")
def preguntar():
    formulario = FormularioPregunta()
    if not formulario.validate_on_submit():
        return render_template(
            "asistente/_error.html",
            mensaje="Se venció el formulario. Recargá la pantalla y volvé a preguntar.",
        ), 400

    pregunta = (request.form.get("pregunta") or "").strip()
    respuesta = asistente.responder(pregunta, current_user)

    plantilla = "asistente/_respuesta.html" if request.headers.get("HX-Request") else "asistente/inicio.html"
    return render_template(
        plantilla, pregunta=pregunta, respuesta=respuesta, **_contexto_base()
    )


@bp.get("/consulta/<int:consulta_id>")
@login_required
@requiere("consultar_ia")
def ver(consulta_id: int):
    """Vuelve a mostrar una consulta anterior, con las fuentes que tuvo entonces.

    Se muestra lo que se guardó y no se vuelve a preguntar: el sentido de revisar
    una consulta vieja es ver qué se respondió, no obtener una respuesta nueva que
    quizás diga otra cosa.
    """
    from ...extensions import db
    from ...models import ConsultaIA

    consulta = db.session.get(ConsultaIA, consulta_id)
    if consulta is None or consulta.usuario_id != current_user.id:
        from flask import abort

        abort(404)

    return render_template("asistente/ver.html", consulta=consulta, **_contexto_base())
