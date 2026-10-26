"""Circuito de solicitudes de mezcla."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm

from ...models import EstadoFlujo, Tratamiento
from ...security import requiere
from ...services import consultas, flujo, solicitudes

bp = Blueprint("solicitudes", __name__, url_prefix="/solicitudes")


class FormularioVacio(FlaskForm):
    """Solo aporta el token CSRF a los formularios que se arman a mano."""


def _decimal(nombre: str) -> float | None:
    valor = (request.form.get(nombre) or "").strip().replace(",", ".")
    try:
        return float(valor)
    except ValueError:
        return None


def _texto(nombre: str, largo: int = 500) -> str:
    return (request.form.get(nombre) or "").strip()[:largo]


def _obtener(tratamiento_id: int) -> Tratamiento:
    tratamiento = consultas.obtener_detalle(tratamiento_id)
    if tratamiento is None:
        abort(404)
    return tratamiento


@bp.get("/")
@login_required
@requiere("ver_bandeja")
def bandeja():
    filtro = request.args.get("estado", "")
    estado = next((e for e in EstadoFlujo if e.value == filtro), None)
    return render_template(
        "solicitudes/bandeja.html",
        pendientes=solicitudes.bandeja(current_user, estado),
        conteos=solicitudes.conteo_por_estado(current_user),
        estado_activo=estado,
        acciones_de=lambda t: flujo.acciones_disponibles(t, current_user),
    )


# ------------------------------------------------------------------ alta


@bp.route("/nueva", methods=["GET", "POST"])
@login_required
@requiere("registrar_solicitud")
def nueva():
    formulario = FormularioVacio()
    catalogo = solicitudes.catalogo_para_formulario()

    if formulario.validate_on_submit():
        nombres = request.form.getlist("producto_nombre")
        dosis = request.form.getlist("producto_dosis")
        unidades = request.form.getlist("producto_unidad")

        lineas = []
        for indice, nombre in enumerate(nombres):
            valor = (dosis[indice] if indice < len(dosis) else "").strip().replace(",", ".")
            try:
                cantidad = float(valor) if valor else None
            except ValueError:
                cantidad = None
            lineas.append(
                solicitudes.LineaProducto(
                    nombre=nombre[:220],
                    dosis=cantidad,
                    unidad=(unidades[indice] if indice < len(unidades) else "")[:20],
                )
            )

        try:
            fecha = datetime.strptime(request.form.get("fecha", ""), "%Y-%m-%d").date()
        except ValueError:
            fecha = None

        try:
            tratamiento = solicitudes.crear_solicitud(
                autor=current_user,
                cliente_id=int(request.form["cliente"]) if request.form.get("cliente") else None,
                cultivo=_texto("cultivo", 120),
                tipo_tratamiento=_texto("tipo", 120),
                target=_texto("target", 180),
                observaciones=_texto("observaciones", 2000),
                fecha=fecha,
                lineas=lineas,
                enviar=request.form.get("accion") == "enviar",
            )
        except solicitudes.DatosInvalidos as error:
            for mensaje in error.errores:
                flash(mensaje, "error")
            return render_template(
                "solicitudes/nueva.html", formulario=formulario, enviado=request.form, **catalogo
            ), 400

        if tratamiento.estado_flujo is EstadoFlujo.BORRADOR:
            flash(f"Solicitud {tratamiento.numero_tratamiento} guardada como borrador.", "ok")
        else:
            flash(f"Solicitud {tratamiento.numero_tratamiento} enviada a aprobación.", "ok")
        return redirect(url_for("solicitudes.detalle", tratamiento_id=tratamiento.id))

    return render_template(
        "solicitudes/nueva.html", formulario=formulario, enviado={}, **catalogo
    )


# --------------------------------------------------------------- detalle


@bp.get("/<int:tratamiento_id>")
@login_required
@requiere("ver_bandeja")
def detalle(tratamiento_id: int):
    tratamiento = _obtener(tratamiento_id)
    return render_template(
        "solicitudes/detalle.html",
        tratamiento=tratamiento,
        acciones=flujo.acciones_disponibles(tratamiento, current_user),
        secuencia=flujo.SECUENCIA,
        formulario=FormularioVacio(),
    )


@bp.post("/<int:tratamiento_id>/<accion>")
@login_required
@requiere("ver_bandeja")
def transicionar(tratamiento_id: int, accion: str):
    if not FormularioVacio().validate_on_submit():
        abort(400)

    tratamiento = _obtener(tratamiento_id)
    try:
        flujo.ejecutar(tratamiento, accion, current_user, motivo=_texto("motivo", 1000))
    except flujo.TransicionInvalida as error:
        flash(str(error), "error")
        return redirect(url_for("solicitudes.detalle", tratamiento_id=tratamiento_id))

    flash(
        f"{tratamiento.numero_tratamiento}: {tratamiento.estado_flujo.etiqueta.lower()}.", "ok"
    )
    if accion == "tomar":
        return redirect(url_for("solicitudes.resultado", tratamiento_id=tratamiento_id))
    return redirect(url_for("solicitudes.detalle", tratamiento_id=tratamiento_id))


# ------------------------------------------------------------- resultados


@bp.route("/<int:tratamiento_id>/resultado", methods=["GET", "POST"])
@login_required
@requiere("ejecutar_ensayo")
def resultado(tratamiento_id: int):
    tratamiento = _obtener(tratamiento_id)
    formulario = FormularioVacio()

    if formulario.validate_on_submit():
        datos = {
            "porcentaje_efectividad": _decimal("porcentaje_efectividad"),
            "ph_agua": _decimal("ph_agua"),
            "dureza_agua": _decimal("dureza_agua"),
            "ph_mezcla": _decimal("ph_mezcla"),
            "observacion_cinco_min": _texto("observacion_cinco_min", 2000) or None,
            "observacion_diez_min": _texto("observacion_diez_min", 2000) or None,
            "suspensibilidad": _texto("suspensibilidad", 120) or None,
            "granulometria": _texto("granulometria", 120) or None,
            "estado_resultado": _texto("estado_resultado", 60) or None,
        }
        try:
            solicitudes.cargar_resultado(
                tratamiento=tratamiento, usuario=current_user, datos=datos
            )
        except (solicitudes.DatosInvalidos, flujo.TransicionInvalida) as error:
            for mensaje in getattr(error, "errores", [str(error)]):
                flash(mensaje, "error")
            return render_template(
                "solicitudes/resultado.html",
                tratamiento=tratamiento, formulario=formulario, enviado=request.form,
            ), 400

        flash(f"{tratamiento.numero_tratamiento} finalizado. Ya podés ver el informe.", "ok")
        return redirect(url_for("tratamientos.detalle", tratamiento_id=tratamiento_id))

    if tratamiento.estado_flujo is not EstadoFlujo.EN_ENSAYO:
        flash("Este tratamiento no está en ensayo.", "aviso")
        return redirect(url_for("solicitudes.detalle", tratamiento_id=tratamiento_id))

    return render_template(
        "solicitudes/resultado.html",
        tratamiento=tratamiento, formulario=formulario, enviado={},
    )


@bp.get("/fila-producto")
@login_required
@requiere("registrar_solicitud")
def fila_producto():
    """Fragmento HTMX: una fila más en la tabla de productos de la mezcla."""
    return render_template("solicitudes/_fila_producto.html")
