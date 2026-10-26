"""Administración: usuarios y bitácora de auditoría.

Las dos pantallas viven juntas porque se usan juntas: cuando algo no cierra, la
pregunta es «quién lo hizo» y la respuesta pasa por cruzar un usuario con lo que
quedó registrado.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from ...extensions import db
from ...models import EstadoUsuario, Usuario
from ...security import requiere
from ...services import auditoria, roles, usuarios

bp = Blueprint("admin", __name__, url_prefix="/administracion")


class FormularioVacio(FlaskForm):
    """Aporta el token CSRF a los formularios que se arman a mano."""


def _campo(nombre: str, largo: int = 180) -> str:
    return (request.form.get(nombre) or "").strip()[:largo]


def _usuario(usuario_id: int) -> Usuario:
    usuario = db.session.get(Usuario, usuario_id)
    if usuario is None:
        abort(404)
    return usuario


# ------------------------------------------------------------------ usuarios


@bp.get("/usuarios")
@login_required
@requiere("gestionar_usuarios")
def listar_usuarios():
    contexto = {
        "usuarios": usuarios.listar(
            texto=request.args.get("texto", "").strip()[:120],
            rol=request.args.get("rol", "").strip(),
            estado=request.args.get("estado", "").strip(),
        ),
        "texto": request.args.get("texto", "").strip()[:120],
        "rol": request.args.get("rol", "").strip(),
        "estado_filtro": request.args.get("estado", "").strip(),
        "roles": [fila.rol for fila in roles.listar()],
        "estados": list(EstadoUsuario),
        "resumen": usuarios.resumen(),
        "formulario": FormularioVacio(),
    }

    if request.headers.get("HX-Request"):
        return render_template("admin/_usuarios.html", **contexto)
    return render_template("admin/usuarios.html", **contexto)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@login_required
@requiere("gestionar_usuarios")
def nuevo_usuario():
    formulario = FormularioVacio()

    if formulario.validate_on_submit():
        try:
            alta = usuarios.crear(
                nombre=_campo("nombre", 120),
                email=_campo("email"),
                rol=_campo("rol", 40),
                autor=current_user,
            )
        except usuarios.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "admin/formulario_usuario.html",
                formulario=formulario, usuario=None, enviado=request.form,
                roles=[f.rol for f in roles.listar()],
            ), 400

        # La clave se muestra una sola vez, en su propia pantalla. No queda en un
        # mensaje flash que reaparezca en la próxima página ni en el historial de
        # navegación con la URL.
        return render_template(
            "admin/clave_generada.html", usuario=alta.usuario, clave=alta.clave_inicial
        )

    return render_template(
        "admin/formulario_usuario.html",
        formulario=formulario, usuario=None, enviado={},
        roles=[f.rol for f in roles.listar()],
    )


@bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@requiere("gestionar_usuarios")
def editar_usuario(usuario_id: int):
    usuario = _usuario(usuario_id)
    formulario = FormularioVacio()

    if formulario.validate_on_submit():
        try:
            usuarios.editar(
                usuario,
                nombre=_campo("nombre", 120),
                email=_campo("email"),
                rol=_campo("rol", 40),
                autor=current_user,
            )
        except usuarios.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "admin/formulario_usuario.html",
                formulario=formulario, usuario=usuario, enviado=request.form,
                roles=[f.rol for f in roles.listar()],
            ), 400

        flash(f"{usuario.nombre} actualizado.", "ok")
        return redirect(url_for("admin.ver_usuario", usuario_id=usuario.id))

    return render_template(
        "admin/formulario_usuario.html",
        formulario=formulario, usuario=usuario, enviado={},
        roles=[f.rol for f in roles.listar()],
    )


@bp.get("/usuarios/<int:usuario_id>")
@login_required
@requiere("gestionar_usuarios")
def ver_usuario(usuario_id: int):
    usuario = _usuario(usuario_id)
    # Dos historias distintas y las dos hacen falta: lo que hizo esta persona, y lo
    # que se le hizo a su cuenta. Un administrador que investiga un acceso raro
    # necesita la segunda —quién le restableció la contraseña y cuándo— y esa
    # información no está en la primera.
    return render_template(
        "admin/ficha_usuario.html",
        usuario=usuario,
        actividad=usuarios.actividad(usuario),
        historia_cuenta=auditoria.historia_de("usuario", usuario.id),
        etiqueta=auditoria.etiqueta,
        formulario=FormularioVacio(),
    )


@bp.post("/usuarios/<int:usuario_id>/estado")
@login_required
@requiere("gestionar_usuarios")
def estado_usuario(usuario_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    usuario = _usuario(usuario_id)

    try:
        estado = EstadoUsuario(_campo("estado", 40))
    except ValueError:
        abort(400)

    # Nadie se da de baja a sí mismo: quedaría afuera del sistema en el acto y sin
    # nadie del otro lado para volver a habilitarlo.
    if usuario.id == current_user.id and estado is not EstadoUsuario.ACTIVO:
        flash("No podés cambiar tu propio estado. Pedíselo a otro administrador.", "error")
        return redirect(url_for("admin.ver_usuario", usuario_id=usuario.id))

    try:
        usuarios.cambiar_estado(usuario, estado, autor=current_user)
    except usuarios.DatosInvalidos as error:
        for mensaje in error.errores:
            flash(mensaje, "error")
        return redirect(url_for("admin.ver_usuario", usuario_id=usuario.id))

    flash(f"{usuario.nombre} quedó en estado {estado.value}.", "ok")
    return redirect(url_for("admin.ver_usuario", usuario_id=usuario.id))


@bp.post("/usuarios/<int:usuario_id>/restablecer")
@login_required
@requiere("gestionar_usuarios")
def restablecer(usuario_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    usuario = _usuario(usuario_id)
    clave = usuarios.restablecer_password(usuario, autor=current_user)
    return render_template("admin/clave_generada.html", usuario=usuario, clave=clave)


@bp.post("/usuarios/<int:usuario_id>/desbloquear")
@login_required
@requiere("gestionar_usuarios")
def desbloquear(usuario_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    usuario = _usuario(usuario_id)
    usuarios.desbloquear(usuario, autor=current_user)
    flash(f"Se levantó el bloqueo de {usuario.nombre}.", "ok")
    return redirect(url_for("admin.ver_usuario", usuario_id=usuario.id))


# ----------------------------------------------------------------- auditoría


@bp.get("/auditoria")
@login_required
@requiere("ver_auditoria")
def bitacora():
    filtros = auditoria.leer_filtros(request.args)
    pagina = auditoria.buscar(filtros, pagina=request.args.get("pagina", type=int, default=1))

    contexto = {
        "filtros": filtros,
        "pagina": pagina,
        "acciones": auditoria.acciones_registradas(),
        "usuarios_disponibles": auditoria.usuarios_con_actividad(),
        "resumen": auditoria.resumen(),
        "etiqueta": auditoria.etiqueta,
        "argumentos": {k: v for k, v in request.args.items() if v and k != "pagina"},
    }

    if request.headers.get("HX-Request"):
        return render_template("admin/_bitacora.html", **contexto)
    return render_template("admin/auditoria.html", **contexto)


# --------------------------------------------------------------------- roles


def _rol(rol_id: int):
    from ...models import Rol

    rol = db.session.get(Rol, rol_id)
    if rol is None:
        abort(404)
    return rol


def _capacidades_marcadas() -> list[str]:
    """Las casillas tildadas. Lo que no venga en el formulario, se quita.

    Un formulario de casillas no manda las desmarcadas, así que la ausencia **es**
    la orden de quitar el permiso. Por eso el servicio recibe la lista completa de
    lo que queda y no un delta: un delta obligaría a comparar contra un estado que
    pudo haber cambiado mientras la pantalla estaba abierta.
    """
    return request.form.getlist("capacidades")


@bp.get("/roles")
@login_required
@requiere("gestionar_roles")
def listar_roles():
    from ...security.roles import CAPACIDADES, GRUPOS

    return render_template(
        "admin/roles.html",
        filas=roles.listar(),
        capacidades=CAPACIDADES,
        grupos=GRUPOS,
        formulario=FormularioVacio(),
    )


@bp.route("/roles/nuevo", methods=["GET", "POST"])
@login_required
@requiere("gestionar_roles")
def nuevo_rol():
    from ...security.roles import CAPACIDADES, GRUPOS

    formulario = FormularioVacio()
    contexto = {
        "formulario": formulario, "rol": None, "capacidades": CAPACIDADES, "grupos": GRUPOS,
    }

    if formulario.validate_on_submit():
        try:
            rol = roles.crear(
                nombre=_campo("nombre", 120),
                descripcion=_campo("descripcion", 400),
                capacidades=_capacidades_marcadas(),
                autor=current_user,
            )
        except roles.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "admin/formulario_rol.html", enviado=request.form,
                marcadas=set(_capacidades_marcadas()), **contexto,
            ), 400

        flash(f"Se creó el rol «{rol.nombre}».", "ok")
        return redirect(url_for("admin.editar_rol", rol_id=rol.id))

    return render_template(
        "admin/formulario_rol.html", enviado={}, marcadas=set(), **contexto
    )


@bp.route("/roles/<int:rol_id>", methods=["GET", "POST"])
@login_required
@requiere("gestionar_roles")
def editar_rol(rol_id: int):
    from ...security.roles import CAPACIDADES, GRUPOS

    rol = _rol(rol_id)
    formulario = FormularioVacio()
    contexto = {
        "formulario": formulario, "rol": rol, "capacidades": CAPACIDADES, "grupos": GRUPOS,
    }

    if formulario.validate_on_submit():
        try:
            roles.editar(
                rol,
                nombre=_campo("nombre", 120),
                descripcion=_campo("descripcion", 400),
                capacidades=_capacidades_marcadas(),
                autor=current_user,
            )
        except roles.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "admin/formulario_rol.html", enviado=request.form,
                marcadas=set(_capacidades_marcadas()), **contexto,
            ), 400

        # Si el administrador acaba de cambiar su propio rol, la pantalla siguiente
        # tiene que dibujarse con los permisos nuevos y no con los de esta petición.
        flash(f"Permisos de «{rol.nombre}» actualizados.", "ok")
        return redirect(url_for("admin.listar_roles"))

    return render_template(
        "admin/formulario_rol.html", enviado={}, marcadas=rol.capacidades, **contexto
    )


@bp.post("/roles/<int:rol_id>/eliminar")
@login_required
@requiere("gestionar_roles")
def eliminar_rol(rol_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    rol = _rol(rol_id)
    nombre = rol.nombre

    try:
        roles.eliminar(rol, autor=current_user)
    except roles.DatosInvalidos as error:
        for mensaje in error.errores:
            flash(mensaje, "error")
        return redirect(url_for("admin.editar_rol", rol_id=rol_id))

    flash(f"Se eliminó el rol «{nombre}».", "ok")
    return redirect(url_for("admin.listar_roles"))
