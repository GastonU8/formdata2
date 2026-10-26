"""Búsqueda y detalle de tratamientos."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from ...security import requiere
from ...services import consultas

bp = Blueprint("tratamientos", __name__, url_prefix="/tratamientos")


def _entero(nombre: str) -> int | None:
    valor = request.args.get(nombre, "").strip()
    return int(valor) if valor.isdigit() else None


@bp.get("/")
@login_required
@requiere("consultar_tratamientos")
def buscar():
    filtros = consultas.leer_filtros(request.args)
    pagina = consultas.buscar(filtros, pagina=_entero("pagina") or 1)
    sin_mapear = consultas.contar_sin_mapear_por_tratamiento([t.id for t in pagina.items])

    contexto = {
        "filtros": filtros,
        "pagina": pagina,
        "sin_mapear": sin_mapear,
        "argumentos": {k: v for k, v in request.args.items() if k != "pagina"},
    }

    # HTMX pide solo la tabla; el navegador sin JS recibe la página entera.
    if request.headers.get("HX-Request"):
        return render_template("tratamientos/_resultados.html", **contexto)

    return render_template(
        "tratamientos/buscar.html", opciones=consultas.opciones_de_filtro(), **contexto
    )


@bp.get("/<int:tratamiento_id>")
@login_required
@requiere("consultar_tratamientos")
def detalle(tratamiento_id: int):
    tratamiento = consultas.obtener_detalle(tratamiento_id)
    if tratamiento is None:
        abort(404)
    return render_template(
        "tratamientos/detalle.html",
        tratamiento=tratamiento,
        relacionados=consultas.tratamientos_relacionados(tratamiento),
    )
