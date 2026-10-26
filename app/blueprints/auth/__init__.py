"""Ingreso, salida y cambio de contraseña."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from ...extensions import db, limiter
from ...models import Usuario
from ...security import registrar

bp = Blueprint("auth", __name__, url_prefix="/acceso")

MENSAJE_CREDENCIALES = "Email o contraseña incorrectos."


class FormularioLogin(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Contraseña", validators=[DataRequired()])
    recordarme = BooleanField("Mantener la sesión abierta")


class FormularioCambioPassword(FlaskForm):
    password_actual = PasswordField("Contraseña actual", validators=[DataRequired()])
    password_nueva = PasswordField(
        "Contraseña nueva",
        validators=[DataRequired(), Length(min=12, message="Usá al menos 12 caracteres.")],
    )
    repetir = PasswordField(
        "Repetir contraseña nueva",
        validators=[DataRequired(), EqualTo("password_nueva", message="Las contraseñas no coinciden.")],
    )


def _destino_seguro(destino: str | None) -> str:
    """Evita open redirect: solo se aceptan rutas internas."""
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return destino
    return url_for("panel.inicio")


@bp.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("panel.inicio"))

    formulario = FormularioLogin()

    if formulario.validate_on_submit():
        email = formulario.email.data.strip().lower()
        usuario = db.session.scalar(db.select(Usuario).filter_by(email=email))

        # Un solo mensaje para "no existe", "contraseña mal" y "suspendido":
        # así el formulario no sirve para enumerar cuentas válidas.
        if usuario is None or not usuario.password_es_correcta(formulario.password.data):
            if usuario is not None:
                usuario.registrar_intento_fallido(
                    current_app.config["MAX_INTENTOS_LOGIN"],
                    current_app.config["MINUTOS_BLOQUEO_LOGIN"],
                )
            registrar("login", exito=False, email_intentado=email)
            flash(MENSAJE_CREDENCIALES, "error")
            return render_template("auth/login.html", formulario=formulario), 401

        if usuario.esta_bloqueado:
            registrar("login_bloqueado", exito=False, email_intentado=email)
            flash(
                "La cuenta está bloqueada por intentos fallidos. "
                f"Volvé a probar en {current_app.config['MINUTOS_BLOQUEO_LOGIN']} minutos.",
                "error",
            )
            return render_template("auth/login.html", formulario=formulario), 429

        if not usuario.is_active:
            registrar("login_inactivo", exito=False, email_intentado=email)
            flash(MENSAJE_CREDENCIALES, "error")
            return render_template("auth/login.html", formulario=formulario), 401

        usuario.registrar_ingreso_exitoso()
        login_user(usuario, remember=formulario.recordarme.data)
        session.permanent = True
        registrar("login", exito=True, entidad="usuario", entidad_id=usuario.id)

        if usuario.debe_cambiar_password:
            flash("Cambiá tu contraseña inicial antes de seguir.", "aviso")
            return redirect(url_for("auth.cambiar_password"))

        return redirect(_destino_seguro(request.args.get("next")))

    return render_template("auth/login.html", formulario=formulario)


@bp.post("/salir")
@login_required
def logout():
    registrar("logout", entidad="usuario", entidad_id=current_user.id)
    logout_user()
    session.clear()
    flash("Cerraste la sesión.", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/contrasena", methods=["GET", "POST"])
@login_required
def cambiar_password():
    formulario = FormularioCambioPassword()

    if formulario.validate_on_submit():
        if not current_user.password_es_correcta(formulario.password_actual.data):
            registrar("cambio_password", exito=False, entidad="usuario", entidad_id=current_user.id)
            flash("La contraseña actual no coincide.", "error")
            return render_template("auth/cambiar_password.html", formulario=formulario), 400

        current_user.definir_password(formulario.password_nueva.data)
        current_user.debe_cambiar_password = False
        registrar("cambio_password", entidad="usuario", entidad_id=current_user.id)
        flash("Contraseña actualizada.", "ok")
        return redirect(url_for("panel.inicio"))

    return render_template("auth/cambiar_password.html", formulario=formulario)
