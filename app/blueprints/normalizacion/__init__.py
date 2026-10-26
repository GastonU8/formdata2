"""Bandeja de normalización: resolver los nombres que la importación dejó pendientes.

Sobre el diseño de las rutas: las acciones van por POST con la clave en el cuerpo
del formulario y no en la URL. La clave es texto libre venido de una planilla
("s/d", "ciper 25 %"), y meterla en el path obliga a escaparla y desescaparla en
cada lado. En el cuerpo viaja tal cual, y el POST además es lo correcto
semánticamente: cada una de estas acciones modifica decenas de filas.
"""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from ...security import requiere
from ...services import maestro, mapeo

bp = Blueprint("normalizacion", __name__, url_prefix="/normalizacion")


class FormularioVacio(FlaskForm):
    """Aporta el token CSRF a los formularios que se arman a mano."""


def _texto(nombre: str, largo: int = 220) -> str:
    return (request.form.get(nombre) or "").strip()[:largo]


def _es_htmx() -> bool:
    return bool(request.headers.get("HX-Request"))


def _responder(resultado: mapeo.Resultado, mensaje: str):
    """Una acción, dos formas de contestar.

    Con HTMX se devuelve solo la tarjeta ya resuelta y la pantalla no se mueve: el
    técnico sigue en el mismo lugar de la lista. Sin JavaScript, se vuelve a la
    bandeja con un aviso. La misma vista sirve a los dos casos y no hay una versión
    "degradada" que nadie prueba.
    """
    if _es_htmx():
        return render_template(
            "normalizacion/_resuelto.html",
            resultado=resultado,
            mensaje=mensaje,
            formulario=FormularioVacio(),
        )
    flash(mensaje, "ok")
    return redirect(url_for("normalizacion.bandeja"))


@bp.get("/")
@login_required
@requiere("normalizar_productos")
def bandeja():
    texto = request.args.get("texto", "").strip()[:120]
    pestana = request.args.get("pestana", "pendientes")
    pagina = request.args.get("pagina", type=int, default=1)

    contexto = {
        "texto": texto,
        "pestana": pestana,
        "pagina": pagina,
        "total_pendientes": mapeo.contar_pendientes(),
        "total_resueltos": mapeo.contar_resueltos(),
        "formulario": FormularioVacio(),
    }

    if pestana == "resueltos":
        contexto["resueltos"] = mapeo.listar_resueltos(texto)
        contexto["pendientes"] = []
    else:
        contexto["pendientes"] = mapeo.listar_pendientes(texto=texto, pagina=pagina)
        contexto["resueltos"] = []
        contexto["hay_mas"] = mapeo.contar_pendientes(texto) > pagina * mapeo.POR_PAGINA

    if _es_htmx() and request.args.get("fragmento"):
        return render_template("normalizacion/_lista.html", **contexto)

    return render_template("normalizacion/bandeja.html", **contexto)


@bp.post("/confirmar")
@login_required
@requiere("normalizar_productos")
def confirmar():
    if not FormularioVacio().validate_on_submit():
        return _error("Se venció el formulario. Recargá la bandeja y probá de nuevo.")

    try:
        resultado = mapeo.confirmar(
            clave=_texto("clave"),
            producto_id=request.form.get("producto_id", type=int) or 0,
            usuario=current_user,
        )
    except mapeo.DecisionInvalida as error:
        return _error(str(error))

    return _responder(
        resultado,
        f"«{resultado.clave}» quedó asociado a {resultado.producto.nombre_comercial}: "
        f"{resultado.filas_afectadas} fila(s) actualizada(s).",
    )


@bp.post("/descartar")
@login_required
@requiere("normalizar_productos")
def descartar():
    if not FormularioVacio().validate_on_submit():
        return _error("Se venció el formulario. Recargá la bandeja y probá de nuevo.")

    try:
        resultado = mapeo.descartar(
            clave=_texto("clave"), usuario=current_user, motivo=_texto("motivo", 500)
        )
    except mapeo.DecisionInvalida as error:
        return _error(str(error))

    return _responder(
        resultado,
        f"«{resultado.clave}» quedó marcado como texto que no es un producto: "
        f"{resultado.filas_afectadas} fila(s) actualizada(s).",
    )


@bp.post("/deshacer")
@login_required
@requiere("normalizar_productos")
def deshacer():
    if not FormularioVacio().validate_on_submit():
        return _error("Se venció el formulario. Recargá la bandeja y probá de nuevo.")

    clave = _texto("clave")
    try:
        resultado = mapeo.deshacer(clave=clave, usuario=current_user)
    except mapeo.DecisionInvalida as error:
        return _error(str(error))

    if _es_htmx():
        pendiente = mapeo.obtener_pendiente(clave)
        if pendiente is None:
            return "", 200
        return render_template(
            "normalizacion/_pendiente.html", p=pendiente, formulario=FormularioVacio()
        )

    flash(f"Se deshizo la decisión sobre «{resultado.clave}».", "ok")
    return redirect(url_for("normalizacion.bandeja"))


@bp.post("/crear-producto")
@login_required
@requiere("administrar_maestro")
def crear_producto():
    """Alta en el maestro desde el pendiente, sin perder el lugar en la bandeja.

    Es el caso más común después de las sugerencias: el producto existe en la
    realidad pero nunca entró al maestro. Obligar a irse a otra pantalla, darlo de
    alta y volver a buscar el pendiente es el tipo de fricción que hace que la
    normalización quede a medias.
    """
    if not FormularioVacio().validate_on_submit():
        return _error("Se venció el formulario. Recargá la bandeja y probá de nuevo.")

    clave = _texto("clave")
    try:
        producto = maestro.crear(
            {
                "nombre_comercial": _texto("nombre_comercial", 180),
                "activo": _texto("activo", 180),
                "categoria": _texto("categoria", 120),
                "marca": _texto("marca", 120),
                "numero_registro": _texto("numero_registro", 60),
            },
            usuario=current_user,
        )
        resultado = mapeo.confirmar(clave=clave, producto_id=producto.id, usuario=current_user)
    except (maestro.DatosInvalidos, mapeo.DecisionInvalida) as error:
        return _error(getattr(error, "errores", [str(error)])[0])

    return _responder(
        resultado,
        f"Se creó {producto.nombre_comercial} en el maestro y se asoció a "
        f"{resultado.filas_afectadas} fila(s).",
    )


@bp.get("/buscar")
@login_required
@requiere("normalizar_productos")
def buscar():
    """Fragmento HTMX: buscar un producto a mano cuando ninguna sugerencia sirve."""
    return render_template(
        "normalizacion/_buscador.html",
        clave=request.args.get("clave", "").strip()[:220],
        ancla=request.args.get("ancla", "").strip()[:32],
        texto=request.args.get("q", "").strip()[:120],
        resultados=mapeo.buscar_para_asignar(request.args.get("q", "")),
        formulario=FormularioVacio(),
    )


@bp.get("/alta")
@login_required
@requiere("administrar_maestro")
def alta():
    """Fragmento HTMX: el formulario de alta rápida dentro de la tarjeta."""
    return render_template(
        "normalizacion/_alta.html",
        clave=request.args.get("clave", "").strip()[:220],
        ancla=request.args.get("ancla", "").strip()[:32],
        nombre=request.args.get("nombre", "").strip()[:180],
        formulario=FormularioVacio(),
    )


def _error(mensaje: str):
    if _es_htmx():
        return render_template("normalizacion/_error.html", mensaje=mensaje), 400
    flash(mensaje, "error")
    return redirect(url_for("normalizacion.bandeja"))
