"""Maestro de productos: consulta para todos, ABM para el rol técnico.

La pantalla es la misma para los dos públicos y las acciones de escritura aparecen
solo si el usuario tiene la capacidad `administrar_maestro`. Separar en dos
secciones distintas ("productos" y "administrar productos") duplicaría la búsqueda
y obligaría a saltar de una a otra para hacer el trabajo real, que es mirar un
producto y corregirlo.
"""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from ...extensions import db
from ...models import EstadoProducto, Producto
from ...security import requiere
from ...services import consultas, maestro

bp = Blueprint("productos", __name__, url_prefix="/productos")


class FormularioVacio(FlaskForm):
    """Aporta el token CSRF a los formularios que se arman a mano."""


def _campos() -> dict:
    return {
        campo: (request.form.get(campo) or "").strip()
        for campo in maestro.CAMPOS
    }


def _obtener(producto_id: int) -> Producto:
    producto = db.session.get(Producto, producto_id)
    if producto is None:
        abort(404)
    return producto


@bp.get("/")
@login_required
@requiere("consultar_tratamientos")
def listar():
    texto = request.args.get("texto", "").strip()[:120]
    categoria = request.args.get("categoria", "").strip()

    contexto = {
        "resultados": consultas.buscar_productos(texto=texto, categoria=categoria),
        "texto": texto,
        "categoria": categoria,
    }

    if request.headers.get("HX-Request"):
        return render_template("productos/_resultados.html", **contexto)

    return render_template(
        "productos/listar.html", categorias=consultas.categorias_de_producto(), **contexto
    )


@bp.get("/<int:producto_id>")
@login_required
@requiere("consultar_tratamientos")
def ficha(producto_id: int):
    datos = maestro.ficha(producto_id)
    if datos is None:
        abort(404)
    return render_template(
        "productos/ficha.html",
        ficha=datos,
        duplicados=maestro.candidatos_a_fusionar(datos.producto),
        formulario=FormularioVacio(),
    )


# ---------------------------------------------------------------------- ABM


@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@requiere("administrar_maestro")
def nuevo():
    formulario = FormularioVacio()
    if formulario.validate_on_submit():
        try:
            producto = maestro.crear(_campos(), usuario=current_user)
        except maestro.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "productos/formulario.html",
                formulario=formulario, producto=None, enviado=request.form,
            ), 400
        flash(f"{producto.nombre_comercial} quedó en el maestro.", "ok")
        return redirect(url_for("productos.ficha", producto_id=producto.id))

    return render_template(
        "productos/formulario.html", formulario=formulario, producto=None, enviado={}
    )


@bp.route("/<int:producto_id>/editar", methods=["GET", "POST"])
@login_required
@requiere("administrar_maestro")
def editar(producto_id: int):
    producto = _obtener(producto_id)
    formulario = FormularioVacio()

    if formulario.validate_on_submit():
        try:
            maestro.editar(producto, _campos(), usuario=current_user)
        except maestro.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "productos/formulario.html",
                formulario=formulario, producto=producto, enviado=request.form,
            ), 400
        flash(f"{producto.nombre_comercial} actualizado.", "ok")
        return redirect(url_for("productos.ficha", producto_id=producto.id))

    return render_template(
        "productos/formulario.html", formulario=formulario, producto=producto, enviado={}
    )


@bp.post("/<int:producto_id>/estado")
@login_required
@requiere("administrar_maestro")
def estado(producto_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    producto = _obtener(producto_id)
    nuevo_estado = (
        EstadoProducto.DISCONTINUADO
        if producto.estado is EstadoProducto.VIGENTE
        else EstadoProducto.VIGENTE
    )
    maestro.cambiar_estado(producto, nuevo_estado, usuario=current_user)
    flash(
        f"{producto.nombre_comercial} quedó {nuevo_estado.value}. "
        "Los tratamientos que lo usan no se tocaron.",
        "ok",
    )
    return redirect(url_for("productos.ficha", producto_id=producto.id))


@bp.post("/<int:producto_id>/fusionar")
@login_required
@requiere("administrar_maestro")
def fusionar(producto_id: int):
    if not FormularioVacio().validate_on_submit():
        abort(400)
    origen = _obtener(producto_id)
    destino = _obtener(request.form.get("destino_id", type=int) or 0)

    try:
        usos = maestro.fusionar(origen=origen, destino=destino, usuario=current_user)
    except maestro.DatosInvalidos as error:
        for mensaje in error.errores:
            flash(mensaje, "error")
        return redirect(url_for("productos.ficha", producto_id=producto_id))

    flash(
        f"Se movieron {usos} uso(s) de {origen.nombre_comercial} a "
        f"{destino.nombre_comercial}, que quedó como el único vigente.",
        "ok",
    )
    return redirect(url_for("productos.ficha", producto_id=destino.id))
