"""Carga de archivos Excel de tratamientos."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from sqlalchemy import select

from ...extensions import db
from ...models import ArchivoImportado, EstadoImportacion
from ...security import registrar, requiere
from ...services import importacion

bp = Blueprint("importacion", __name__, url_prefix="/importacion")


class FormularioCarga(FlaskForm):
    archivo = FileField(
        "Archivo de tratamientos",
        validators=[
            FileRequired("Elegí un archivo."),
            FileAllowed(["xlsx", "xlsm"], "Solo se admiten archivos .xlsx o .xlsm."),
        ],
    )


@bp.get("/")
@login_required
@requiere("importar_excel")
def historial():
    return render_template(
        "importacion/historial.html",
        formulario=FormularioCarga(),
        archivos=list(
            db.session.scalars(
                select(ArchivoImportado).order_by(ArchivoImportado.creado_en.desc()).limit(25)
            )
        ),
    )


@bp.post("/")
@login_required
@requiere("importar_excel")
def cargar():
    formulario = FormularioCarga()
    if not formulario.validate_on_submit():
        for errores in formulario.errors.values():
            for mensaje in errores:
                flash(mensaje, "error")
        return redirect(url_for("importacion.historial"))

    subido = formulario.archivo.data
    extension = Path(subido.filename or "").suffix.lower()
    if extension not in current_app.config["EXTENSIONES_PERMITIDAS"]:
        flash("Solo se admiten archivos .xlsx o .xlsm.", "error")
        return redirect(url_for("importacion.historial"))

    # El nombre del archivo lo elige el sistema, no el usuario: un nombre subido
    # nunca toca el sistema de archivos.
    carpeta = Path(current_app.config["CARPETA_IMPORTACIONES"])
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{uuid.uuid4().hex}{extension}"
    subido.save(ruta)

    huella, tamano = importacion.calcular_hash(str(ruta))

    previo = db.session.scalar(
        select(ArchivoImportado)
        .where(ArchivoImportado.hash_sha256 == huella)
        .order_by(ArchivoImportado.creado_en.desc())
    )
    if previo is not None:
        flash(
            f"Este archivo ya se importó el {previo.creado_en:%d/%m/%Y a las %H:%M}. "
            "Se procesa igual y los tratamientos existentes se actualizan.",
            "aviso",
        )

    archivo = ArchivoImportado(
        nombre_archivo=(subido.filename or "sin-nombre")[:255],
        hash_sha256=huella,
        tamano_bytes=tamano,
        usuario_id=current_user.id,
        estado=EstadoImportacion.PENDIENTE,
    )
    db.session.add(archivo)
    db.session.commit()

    registrar(
        "importacion_iniciada",
        entidad="archivo_importado",
        entidad_id=archivo.id,
        datos={"nombre": archivo.nombre_archivo, "bytes": tamano},
    )

    if current_app.config["IMPORTACION_EN_SEGUNDO_PLANO"]:
        importacion.procesar_en_segundo_plano(
            current_app._get_current_object(), archivo.id, str(ruta)
        )
    else:
        importacion.procesar(archivo, str(ruta))

    return redirect(url_for("importacion.detalle", archivo_id=archivo.id))


@bp.get("/<int:archivo_id>")
@login_required
@requiere("importar_excel")
def detalle(archivo_id: int):
    archivo = db.session.get(ArchivoImportado, archivo_id)
    if archivo is None:
        abort(404)

    plantilla = (
        "importacion/_estado.html" if request.headers.get("HX-Request")
        else "importacion/detalle.html"
    )
    return render_template(plantilla, archivo=archivo)
