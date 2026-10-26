"""Gráficos en SVG generados en el servidor.

Por qué no una librería de JavaScript
-------------------------------------
La alternativa habitual (Chart.js, ApexCharts) exigía tres concesiones que este
proyecto no necesita hacer:

* **Abrir la CSP.** La política actual solo admite scripts propios y dos CDN
  concretos. Meter un motor de gráficos que evalúa configuración en tiempo de
  ejecución agranda la superficie por una función decorativa.
* **Dejar la pantalla vacía sin JavaScript.** Todo el resto del sistema degrada a
  formulario común; el tablero sería la única pantalla que no muestra nada.
* **Perder la impresión y el PDF.** Un `<canvas>` no se imprime bien y no se puede
  reutilizar del lado del servidor. Con SVG, la misma serie alimenta la pantalla, la
  impresión y —recalculada con primitivas— el PDF.

El costo es que hay que dibujar a mano. Para barras y columnas eso son treinta
líneas de geometría, testeables como cualquier otra función.

Accesibilidad: cada gráfico lleva `<title>` y `role="img"`, y la plantilla que lo
usa muestra además la tabla de datos. El gráfico ayuda a ver; la tabla es la que
realmente informa.
"""

from __future__ import annotations

import math

from markupsafe import Markup, escape

from .analitica import Serie

ALTO_BARRA = 22
SEPARACION = 8

# El texto de un SVG escala junto con el `viewBox`. Si el viewBox fuera siempre de
# 640 y la tarjeta midiera 400 px, un `font-size: 11` terminaría dibujado a 7 px y
# las etiquetas serían ilegibles. Por eso cada tipo de gráfico declara un ancho
# parecido al del hueco donde se muestra: las barras viven en tarjetas de media
# columna y las columnas ocupan el ancho completo.
ANCHO_BARRAS = 420
ANCHO_ETIQUETA = 118
ANCHO_VALOR = 32
ANCHO_TOTAL = 640


def _sin_datos(titulo: str, alto: int = 90, ancho: int = ANCHO_BARRAS) -> Markup:
    return Markup(
        f'<svg class="grafico grafico--vacio" viewBox="0 0 {ancho} {alto}" '
        f'role="img" aria-label="{escape(titulo)}: sin datos">'
        f'<title>{escape(titulo)}: sin datos</title>'
        f'<text x="{ancho // 2}" y="{alto // 2}" text-anchor="middle" '
        f'class="grafico__vacio">Sin datos para este recorte</text></svg>'
    )


def barras(serie: Serie, maximo_barras: int = 10, ancho: int = ANCHO_BARRAS) -> Markup:
    """Barras horizontales. Es el formato correcto cuando la etiqueta es texto.

    Con barras verticales, «Cooperativa Rosario» obliga a rotar el texto o a
    recortarlo; en horizontal la etiqueta se lee de corrido y la comparación entre
    largos sigue siendo inmediata.
    """
    puntos = serie.puntos[:maximo_barras]
    if not puntos or not serie.hay_datos:
        return _sin_datos(serie.titulo, ancho=ancho)

    alto = len(puntos) * (ALTO_BARRA + SEPARACION) + SEPARACION
    ancho_pista = ancho - ANCHO_ETIQUETA - ANCHO_VALOR

    partes = [
        f'<svg class="grafico" viewBox="0 0 {ancho} {alto}" role="img" '
        f'aria-label="{escape(serie.titulo)}"><title>{escape(serie.titulo)}</title>'
    ]

    for indice, punto in enumerate(puntos):
        y = SEPARACION + indice * (ALTO_BARRA + SEPARACION)
        largo = max(round(ancho_pista * serie.porcentaje(punto) / 100, 1), 1 if punto.valor else 0)
        etiqueta = escape(punto.etiqueta)

        partes.append(
            f'<text x="0" y="{y + ALTO_BARRA - 6}" class="grafico__etiqueta">'
            f'{_recortar(etiqueta, 21)}</text>'
        )
        # Pista de fondo: da la referencia de cuánto sería el 100 %. Sin ella, una
        # barra corta no se distingue de un gráfico mal renderizado.
        partes.append(
            f'<rect x="{ANCHO_ETIQUETA}" y="{y}" width="{ancho_pista}" height="{ALTO_BARRA}" '
            f'rx="3" class="grafico__pista"/>'
        )
        cuerpo = (
            f'<rect x="{ANCHO_ETIQUETA}" y="{y}" width="{largo}" height="{ALTO_BARRA}" '
            f'rx="3" class="grafico__barra"><title>{etiqueta}: {_numero(punto.valor)} '
            f'{escape(serie.unidad)}</title></rect>'
        )
        if punto.enlace:
            cuerpo = f'<a href="{escape(punto.enlace)}">{cuerpo}</a>'
        partes.append(cuerpo)
        partes.append(
            f'<text x="{ancho}" y="{y + ALTO_BARRA - 6}" text-anchor="end" '
            f'class="grafico__valor">{_numero(punto.valor)}</text>'
        )

    partes.append("</svg>")
    return Markup("".join(partes))


ALTO_COLUMNAS = 170
ANCHO_COLUMNA_MINIMO = 14
ANCHO_ETIQUETA_MES = 38  # lo que ocupa «ago 25» a 11 px en el sistema de coordenadas


def columnas(serie: Serie) -> Markup:
    """Columnas verticales para series temporales.

    El eje del tiempo se lee de izquierda a derecha; en horizontal habría que
    girar la cabeza. Es el único caso donde el formato vertical gana.
    """
    puntos = serie.puntos
    if not puntos or not serie.hay_datos:
        return _sin_datos(serie.titulo, alto=120, ancho=ANCHO_TOTAL)

    margen_inferior = 26
    alto_util = ALTO_COLUMNAS - margen_inferior - 6
    paso = ANCHO_TOTAL / len(puntos)
    ancho = max(min(paso * 0.62, 34), ANCHO_COLUMNA_MINIMO)

    partes = [
        f'<svg class="grafico" viewBox="0 0 {ANCHO_TOTAL} {ALTO_COLUMNAS}" role="img" '
        f'aria-label="{escape(serie.titulo)}"><title>{escape(serie.titulo)}</title>'
    ]
    partes.append(
        f'<line x1="0" y1="{ALTO_COLUMNAS - margen_inferior}" x2="{ANCHO_TOTAL}" '
        f'y2="{ALTO_COLUMNAS - margen_inferior}" class="grafico__eje"/>'
    )

    # Cuántas etiquetas entran es cuestión de espacio, no de cantidad de barras:
    # el salto sale de comparar el ancho de una etiqueta («ago 25») con el paso
    # entre columnas. Calcularlo sobre `len(puntos)` dejaba los meses superpuestos
    # apenas la serie pasaba de un año y medio.
    salto = max(1, math.ceil(ANCHO_ETIQUETA_MES / paso))

    for indice, punto in enumerate(puntos):
        alto_columna = round(alto_util * serie.porcentaje(punto) / 100, 1)
        x = indice * paso + (paso - ancho) / 2
        y = ALTO_COLUMNAS - margen_inferior - alto_columna
        etiqueta = escape(punto.etiqueta)

        cuerpo = (
            f'<rect x="{round(x, 1)}" y="{y}" width="{round(ancho, 1)}" '
            f'height="{alto_columna}" rx="2" class="grafico__barra">'
            f'<title>{etiqueta}: {_numero(punto.valor)} {escape(serie.unidad)}</title></rect>'
        )
        if punto.enlace:
            cuerpo = f'<a href="{escape(punto.enlace)}">{cuerpo}</a>'
        partes.append(cuerpo)

        if indice % salto == 0 or indice == len(puntos) - 1:
            partes.append(
                f'<text x="{round(x + ancho / 2, 1)}" y="{ALTO_COLUMNAS - 8}" '
                f'text-anchor="middle" class="grafico__etiqueta">{etiqueta}</text>'
            )

    partes.append("</svg>")
    return Markup("".join(partes))


ALTO_APILADA = 34


def apilada(serie: Serie) -> Markup:
    """Una sola barra repartida en tramos: para composiciones que suman un todo.

    Se usa donde la pregunta es «qué proporción», no «cuánto»: ahí una barra por
    categoría obliga a sumar mentalmente para entender el reparto.
    """
    puntos = [p for p in serie.puntos if p.valor]
    if not puntos or not serie.total:
        return _sin_datos(serie.titulo, alto=60, ancho=ANCHO_TOTAL)

    partes = [
        f'<svg class="grafico grafico--apilada" viewBox="0 0 {ANCHO_TOTAL} {ALTO_APILADA}" '
        f'role="img" aria-label="{escape(serie.titulo)}">'
        f'<title>{escape(serie.titulo)}</title>'
    ]
    x = 0.0
    for indice, punto in enumerate(puntos):
        ancho = round(ANCHO_TOTAL * serie.proporcion(punto) / 100, 2)
        etiqueta = escape(punto.etiqueta)
        cuerpo = (
            f'<rect x="{round(x, 2)}" y="0" width="{ancho}" height="{ALTO_APILADA}" '
            f'class="grafico__tramo grafico__tramo--{indice % 5}">'
            f'<title>{etiqueta}: {_numero(punto.valor)} '
            f'({serie.proporcion(punto):.1f} %)</title></rect>'
        )
        if punto.enlace:
            cuerpo = f'<a href="{escape(punto.enlace)}">{cuerpo}</a>'
        partes.append(cuerpo)
        x += ancho

    partes.append("</svg>")
    return Markup("".join(partes))


def _recortar(etiqueta, limite: int = 24) -> str:
    texto = str(etiqueta)
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def _numero(valor: float) -> str:
    """Formato local: punto para los miles, coma para los decimales."""
    if float(valor).is_integer():
        return f"{int(valor):,}".replace(",", ".")
    return f"{valor:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")
