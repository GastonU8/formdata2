"""Tablero de indicadores y exportación del recorte visible.

El tablero **es** la búsqueda vista desde arriba: usa el mismo `Filtros` y el mismo
conjunto de ids. Por eso cada barra enlaza a la búsqueda que la explica y por eso el
botón de exportar arrastra los filtros actuales: lo que se descarga es exactamente
lo que se está mirando, no "todo".
"""

from __future__ import annotations

from flask import Blueprint, Response, render_template, request
from flask_login import current_user, login_required

from ...security import requiere
from ...services import analitica, consultas, exportacion, indicadores

bp = Blueprint("panel", __name__)


@bp.get("/")
@login_required
@requiere("ver_indicadores")
def inicio():
    filtros = consultas.leer_filtros(request.args)
    contexto = {
        "filtros": filtros,
        "opciones": consultas.opciones_de_filtro(),
        # La salud del dato es global a propósito, no del recorte: es el estado del
        # repositorio y no puede depender de qué esté mirando cada uno.
        "salud": indicadores.salud_del_dato(),
        "importaciones": indicadores.ultimas_importaciones(),
        "argumentos": {k: v for k, v in request.args.items() if v},
        **analitica.tablero(filtros),
    }

    if request.headers.get("HX-Request"):
        return render_template("panel/_indicadores.html", **contexto)

    return render_template("panel/inicio.html", **contexto)


def _descargar(flujo, nombre: str, tipo: str) -> Response:
    return Response(
        flujo.getvalue(),
        mimetype=tipo,
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            # Un informe con filtros nunca se cachea: la misma URL con otros datos
            # detrás tiene que volver a generarse.
            "Cache-Control": "no-store",
        },
    )


@bp.get("/exportar/excel")
@login_required
@requiere("exportar_reportes")
def exportar_excel():
    flujo, nombre = exportacion.libro_excel(consultas.leer_filtros(request.args), current_user)
    return _descargar(
        flujo, nombre, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@bp.get("/exportar/pdf")
@login_required
@requiere("exportar_reportes")
def exportar_pdf():
    flujo, nombre = exportacion.informe_pdf(consultas.leer_filtros(request.args), current_user)
    return _descargar(flujo, nombre, "application/pdf")
