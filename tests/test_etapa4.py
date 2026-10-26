"""Pruebas de la etapa 4: indicadores, gráficos y exportación.

La propiedad más importante que se protege acá no es que un gráfico se dibuje: es
que **el tablero, la búsqueda y el archivo exportado cuenten lo mismo**. Un informe
que no coincide con la pantalla de la que salió destruye la confianza en todo el
sistema, y es una falla silenciosa: nadie la ve hasta que alguien cruza dos números
en una reunión.
"""

import xml.dom.minidom
from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import (
    Cliente,
    DetalleMezcla,
    EstadoNormalizacion,
    EstadoUsuario,
    Mezcla,
    Producto,
    Resultado,
    ClaveRol,
    Tratamiento,
    Usuario,
)
from app.services import analitica, consultas, exportacion, graficos
from app.services import normalizacion as n

PASSWORD = "clave-de-prueba-larga"

# Cuatro cultivos con cantidades distintas y conocidas, para poder afirmar números
# exactos en vez de "mayor que cero".
PLAN = [
    ("Soja", 6, "Compatibilidad"),
    ("Maíz", 4, "Eficacia"),
    ("Trigo", 3, "Eficacia"),
    ("Girasol", 1, "Estabilidad"),
]
TOTAL = sum(cantidad for _, cantidad, _ in PLAN)


@pytest.fixture
def app():
    aplicacion = aislar_sesion_entre_clientes(crear_app("pruebas"))
    with aplicacion.app_context():
        db.create_all()
        _sembrar()
        yield aplicacion
        db.session.remove()
        db.drop_all()


def _sembrar():
    for nombre, email, rol in [
        ("Ana Técnica", "tecnica@bahnsa.com.ar", ClaveRol.TECNICO),
        ("Bruno Comercial", "comercial@bahnsa.com.ar", ClaveRol.COMERCIAL),
    ]:
        usuario = Usuario(nombre=nombre, email=email, rol=rol_de(rol), estado=EstadoUsuario.ACTIVO)
        usuario.definir_password(PASSWORD)
        db.session.add(usuario)

    clientes = []
    for nombre in ["Agro Sur SA", "Cooperativa Rosario"]:
        cliente = Cliente(nombre=nombre, clave_normalizada=n.clave(nombre))
        db.session.add(cliente)
        clientes.append(cliente)

    productos = []
    for nombre, activo in [("Round Up Full", "Glifosato 66,2%"), ("Atrazina 90", "Atrazina 90%")]:
        producto = Producto(
            nombre_comercial=nombre, clave_normalizada=n.clave(nombre), activo=activo
        )
        db.session.add(producto)
        productos.append(producto)
    db.session.flush()

    indice = 0
    base = date(2025, 1, 15)
    for cultivo, cantidad, tipo in PLAN:
        for _ in range(cantidad):
            indice += 1
            tratamiento = Tratamiento(
                numero_tratamiento=f"T-{indice:04d}",
                # Un ensayo por mes: así el gráfico temporal tiene meses con dato y
                # meses vacíos, que es lo que hace falta para probar el relleno.
                fecha=base + timedelta(days=62 * indice),
                cliente_id=clientes[indice % 2].id,
                cultivo=cultivo,
                tipo_tratamiento=tipo,
            )
            db.session.add(tratamiento)
            db.session.flush()

            mezcla = Mezcla(tratamiento_id=tratamiento.id, nombre_mezcla="Mezcla")
            db.session.add(mezcla)
            db.session.flush()

            # Dos productos resueltos y —en un ensayo de cada tres— uno sin mapear.
            for orden, producto in enumerate(productos, start=1):
                db.session.add(
                    DetalleMezcla(
                        mezcla_id=mezcla.id,
                        orden_producto=orden,
                        nombre_original=producto.nombre_comercial,
                        clave_normalizada=producto.clave_normalizada,
                        producto_id=producto.id,
                        estado_normalizacion=EstadoNormalizacion.AUTOMATICA,
                    )
                )
            if indice % 3 == 0:
                db.session.add(
                    DetalleMezcla(
                        mezcla_id=mezcla.id,
                        orden_producto=9,
                        nombre_original="Coadyubante Silic.",
                        clave_normalizada=n.clave("Coadyubante Silic."),
                        estado_normalizacion=EstadoNormalizacion.PENDIENTE,
                    )
                )

            db.session.add(
                Resultado(
                    tratamiento_id=tratamiento.id,
                    ph_agua=Decimal("7.00"),
                    ph_mezcla=Decimal("5.50") if indice % 2 else Decimal("6.50"),
                    dureza_agua=Decimal("100.0"),
                    # Códigos del laboratorio, no una clasificación cerrada.
                    estado_resultado="OK" if indice % 2 else "PPDO",
                )
            )
    db.session.commit()


def _ingresar(app, email="tecnica@bahnsa.com.ar"):
    cliente = app.test_client()
    cliente.post("/acceso/", data={"email": email, "password": PASSWORD})
    return cliente


def _usuario():
    return db.session.scalar(db.select(Usuario).filter_by(email="tecnica@bahnsa.com.ar"))


# ------------------------------------- el tablero cuenta lo mismo que la búsqueda


@pytest.mark.parametrize(
    "argumentos, esperado",
    [
        ({}, TOTAL),
        ({"cultivo": "Soja"}, 6),
        ({"cultivo": "Girasol"}, 1),
        ({"tipo": "Eficacia"}, 7),
        ({"cultivo": "Maíz", "tipo": "Eficacia"}, 4),
        ({"resultado": "OK"}, 7),
        ({"sin_mapear": "1"}, 4),
        ({"cultivo": "Soja", "resultado": "PPDO"}, 3),
    ],
)
def test_el_tablero_y_la_busqueda_cuentan_lo_mismo(app, argumentos, esperado):
    """La propiedad central de la etapa. Si esto falla, ningún informe vale nada."""
    with app.test_request_context("/"):
        filtros = consultas.leer_filtros(argumentos)
        assert analitica.resumen(filtros).tratamientos == esperado
        assert consultas.buscar(filtros, por_pagina=100).total == esperado


def test_las_barras_suman_el_total_del_recorte(app):
    with app.test_request_context("/"):
        filtros = consultas.leer_filtros({})
        assert analitica.por_cultivo(filtros).total == TOTAL
        assert analitica.por_tipo(filtros).total == TOTAL
        assert analitica.por_cliente(filtros).total == TOTAL
        assert analitica.por_periodo(filtros, meses=120).total == TOTAL


def test_cada_barra_enlaza_a_la_busqueda_que_la_explica(app):
    """De punta a punta: se sigue el enlace de la barra y tiene que dar su valor.

    Es la prueba de que el enlace arrastra bien los filtros vigentes. Un enlace que
    pierde el filtro activo lleva a una lista más grande que la barra, y quien la
    abre concluye que el gráfico está mal.
    """
    from urllib.parse import parse_qs, urlparse

    cliente = _ingresar(app)
    with app.test_request_context("/"):
        serie = analitica.por_cultivo(consultas.leer_filtros({"tipo": "Eficacia"}))

    assert serie.puntos, "el recorte de prueba tiene que dar al menos una barra"
    for punto in serie.puntos:
        # La página abre…
        assert cliente.get(punto.enlace).status_code == 200
        # …y lo que lista es exactamente lo que dice la barra.
        argumentos = {k: v[0] for k, v in parse_qs(urlparse(punto.enlace).query).items()}
        with app.test_request_context("/"):
            encontrados = consultas.buscar(consultas.leer_filtros(argumentos), por_pagina=100).total
        assert encontrados == punto.valor, f"la barra {punto.etiqueta} no coincide con su enlace"
        # Y el filtro que ya estaba puesto no se perdió por el camino.
        assert argumentos.get("tipo") == "Eficacia"


def test_la_normalizacion_que_muestra_es_la_del_recorte(app):
    """Un recorte limpio no puede quedar tapado por la suciedad de otro."""
    with app.test_request_context("/"):
        girasol = analitica.resumen(consultas.leer_filtros({"cultivo": "Girasol"}))
        global_ = analitica.resumen(consultas.leer_filtros({}))

    assert girasol.filas_sin_mapear == 0
    assert girasol.porcentaje_normalizado == 100.0
    assert global_.filas_sin_mapear == 4
    assert global_.porcentaje_normalizado < 100.0


def test_sin_filas_de_producto_la_normalizacion_no_divide_por_cero(app):
    with app.test_request_context("/"):
        vacio = analitica.resumen(consultas.leer_filtros({"cultivo": "Zanahoria"}))
    assert vacio.tratamientos == 0
    assert vacio.porcentaje_normalizado == 100.0


# ------------------------------------------------------------------- las series


def test_el_grafico_temporal_rellena_los_meses_sin_actividad(app):
    """Sin el relleno, dos meses vacíos se ven como un intervalo corto.

    Es decir: la caída de actividad desaparecería del gráfico, que es justamente
    lo que un gráfico temporal existe para mostrar.
    """
    with app.test_request_context("/"):
        serie = analitica.por_periodo(consultas.leer_filtros({}), meses=120)

    assert any(p.valor == 0 for p in serie.puntos), "faltan los meses en cero"
    # Y el eje va de más viejo a más nuevo, aunque se calcule hacia atrás.
    assert serie.puntos[0].etiqueta != serie.puntos[-1].etiqueta
    assert serie.total == TOTAL


def test_el_grafico_temporal_recorta_y_lo_avisa(app):
    with app.test_request_context("/"):
        serie = analitica.por_periodo(consultas.leer_filtros({}), meses=6)
    assert len(serie.puntos) == 6
    assert "últimos 6 meses" in serie.nota


def test_el_ranking_de_productos_cuenta_filas_de_mezcla(app):
    with app.test_request_context("/"):
        serie = analitica.productos_mas_usados(consultas.leer_filtros({}))
    # Los dos productos del maestro están en todos los ensayos.
    assert {p.etiqueta for p in serie.puntos} == {"Round Up Full", "Atrazina 90"}
    assert all(p.valor == TOTAL for p in serie.puntos)


def test_las_observaciones_se_muestran_como_las_escribio_el_laboratorio(app):
    """No se traducen a «Compatible / Incompatible»: el dato no dice eso."""
    with app.test_request_context("/"):
        serie = analitica.observaciones_de_ensayo(consultas.leer_filtros({}))
    assert {p.etiqueta for p in serie.puntos} == {"OK", "PPDO"}
    assert serie.total == TOTAL


def test_el_histograma_de_ph_usa_tramos_fijos(app):
    """Tramos automáticos cambiarían de forma con cada dato nuevo.

    Y entonces dos informes del mismo laboratorio dejarían de ser comparables, que
    es para lo único que se mira un histograma de pH.
    """
    with app.test_request_context("/"):
        serie = analitica.distribucion_ph(consultas.leer_filtros({}))

    assert [p.etiqueta for p in serie.puntos] == [t[0] for t in analitica.TRAMOS_PH]
    tramos = {p.etiqueta: p.valor for p in serie.puntos}
    assert tramos["5 – 6"] == 7   # los pH 5,50
    assert tramos["6 – 7"] == 7   # los pH 6,50
    assert tramos["< 4"] == 0


def test_los_promedios_vienen_con_su_n(app):
    with app.test_request_context("/"):
        promedios = analitica.promedios(consultas.leer_filtros({}))
    assert promedios.ph_agua == 7.0
    assert promedios.medidos_ph_agua == TOTAL


# ------------------------------------------------------------------- gráficos


def _svg_valido(marcado: str):
    """Un SVG mal formado no rompe la página: el navegador lo descarta en silencio."""
    xml.dom.minidom.parseString(marcado)


def test_los_graficos_son_svg_bien_formado(app):
    with app.test_request_context("/"):
        datos = analitica.tablero(consultas.leer_filtros({}))
    _svg_valido(str(graficos.barras(datos["cultivos"])))
    _svg_valido(str(graficos.columnas(datos["periodo"])))
    _svg_valido(str(graficos.apilada(datos["observaciones"])))


def test_una_serie_vacia_dibuja_el_cartel_y_no_un_grafico_roto(app):
    marcado = str(graficos.barras(analitica.Serie(titulo="Nada")))
    _svg_valido(marcado)
    assert "Sin datos" in marcado
    assert "<rect" not in marcado


def test_la_barra_mas_alta_ocupa_la_pista_completa(app):
    """La geometría es lo único que un gráfico no puede equivocar."""
    import re

    serie = analitica.Serie(
        titulo="Prueba",
        puntos=[analitica.Punto("a", 10), analitica.Punto("b", 5), analitica.Punto("c", 0)],
    )
    marcado = str(graficos.barras(serie))
    largos = [
        float(x)
        for x in re.findall(r'width="([\d.]+)" height="22" rx="3" class="grafico__barra"', marcado)
    ]
    pista = graficos.ANCHO_BARRAS - graficos.ANCHO_ETIQUETA - graficos.ANCHO_VALOR

    assert largos[0] == pista
    assert largos[1] == pytest.approx(pista / 2, abs=0.1)
    assert largos[2] == 0


def test_la_barra_apilada_reparte_exactamente_el_ancho(app):
    import re

    serie = analitica.Serie(
        titulo="Prueba", puntos=[analitica.Punto("a", 3), analitica.Punto("b", 1)]
    )
    marcado = str(graficos.apilada(serie))
    anchos = [float(x) for x in re.findall(r'width="([\d.]+)" height="34"', marcado)]
    assert sum(anchos) == pytest.approx(graficos.ANCHO_TOTAL, abs=0.05)


def test_una_etiqueta_con_html_no_se_inyecta_en_el_svg(app):
    """Los nombres salen de un Excel que carga cualquiera: son entrada no confiable."""
    serie = analitica.Serie(
        titulo='<script>alert(1)</script>',
        puntos=[analitica.Punto('<img src=x onerror=alert(1)>', 5)],
    )
    marcado = str(graficos.barras(serie))
    _svg_valido(marcado)
    assert "<script>" not in marcado
    assert "<img" not in marcado
    assert "&lt;" in marcado


# ----------------------------------------------------------------- exportación


def test_el_excel_trae_las_cuatro_hojas_y_los_criterios_primero(app):
    import openpyxl

    with app.test_request_context("/"):
        flujo, nombre = exportacion.libro_excel(
            consultas.leer_filtros({"cultivo": "Soja"}), _usuario()
        )

    libro = openpyxl.load_workbook(flujo)
    assert libro.sheetnames == ["Criterios", "Resumen", "Tratamientos", "Productos por ensayo"]
    assert nombre.endswith(".xlsx")

    # Los criterios van en la primera hoja: sin ellos el archivo no es reproducible.
    texto = " ".join(
        str(celda.value) for fila in libro["Criterios"].iter_rows() for celda in fila
    )
    assert "cultivo Soja" in texto
    assert "Ana Técnica" in texto


def test_el_excel_exporta_exactamente_el_recorte_filtrado(app):
    import openpyxl

    with app.test_request_context("/"):
        flujo, _ = exportacion.libro_excel(consultas.leer_filtros({"cultivo": "Soja"}), _usuario())

    hoja = openpyxl.load_workbook(flujo)["Tratamientos"]
    filas = list(hoja.iter_rows(min_row=2, values_only=True))
    assert len(filas) == 6
    assert {fila[3] for fila in filas} == {"Soja"}


def test_el_excel_trae_el_formato_largo_para_tablas_dinamicas(app):
    """Una fila por producto: convertir de largo a ancho en Excel se puede; al revés, no."""
    import openpyxl

    with app.test_request_context("/"):
        flujo, _ = exportacion.libro_excel(consultas.leer_filtros({"cultivo": "Soja"}), _usuario())

    hoja = openpyxl.load_workbook(flujo)["Productos por ensayo"]
    filas = list(hoja.iter_rows(min_row=2, values_only=True))
    # 6 ensayos de soja × 2 productos, más los pendientes de los ensayos múltiplo de 3.
    assert len(filas) >= 12
    assert any(fila[9] == EstadoNormalizacion.PENDIENTE.value for fila in filas)


def test_el_pdf_se_genera_y_es_un_pdf(app):
    with app.test_request_context("/"):
        flujo, nombre = exportacion.informe_pdf(consultas.leer_filtros({}), _usuario())

    datos = flujo.getvalue()
    assert datos.startswith(b"%PDF")
    assert len(datos) > 2000
    assert nombre.endswith(".pdf")


def test_exportar_queda_auditado(app):
    from app.models import RegistroAuditoria

    with app.test_request_context("/"):
        exportacion.libro_excel(consultas.leer_filtros({"cultivo": "Soja"}), _usuario())

    registro = db.session.scalar(
        db.select(RegistroAuditoria).filter_by(accion="exportacion_excel")
    )
    assert registro is not None
    assert registro.usuario_id == _usuario().id
    assert "cultivo Soja" in str(registro.datos)


# --------------------------------------------------------------------- vistas


def test_el_tablero_dibuja_los_graficos(app):
    cliente = _ingresar(app)
    respuesta = cliente.get("/")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert cuerpo.count("<svg") >= 5
    assert "Ensayos por cultivo" in cuerpo
    # Y la tabla de valores acompaña a cada gráfico: el SVG no puede ser la única vía.
    assert cuerpo.count("grafico-tabla") >= 5


def test_el_tablero_filtrado_devuelve_solo_el_fragmento_por_htmx(app):
    cliente = _ingresar(app)
    respuesta = cliente.get("/?cultivo=Soja", headers={"HX-Request": "true"})
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "<html" not in cuerpo
    assert "<svg" in cuerpo


def test_las_descargas_llegan_como_adjunto_con_nombre(app):
    cliente = _ingresar(app)
    for ruta, extension in [("/exportar/excel", ".xlsx"), ("/exportar/pdf", ".pdf")]:
        respuesta = cliente.get(f"{ruta}?cultivo=Soja")
        assert respuesta.status_code == 200
        disposicion = respuesta.headers["Content-Disposition"]
        assert disposicion.startswith("attachment;")
        assert extension in disposicion
        # Un informe con filtros no puede quedar cacheado: la misma URL con otros
        # datos detrás tiene que volver a generarse.
        assert respuesta.headers["Cache-Control"] == "no-store"


def test_el_comercial_tambien_puede_exportar_lo_que_ve(app):
    cliente = _ingresar(app, "comercial@bahnsa.com.ar")
    assert cliente.get("/").status_code == 200
    assert cliente.get("/exportar/excel").status_code == 200


def test_sin_ingresar_no_se_exporta_nada(app):
    cliente = app.test_client()
    respuesta = cliente.get("/exportar/excel")
    assert respuesta.status_code == 302
    assert "/acceso/" in respuesta.headers["Location"]


def test_un_filtro_con_basura_no_rompe_el_tablero(app):
    """La query string la escribe cualquiera; nada que no se entienda debe explotar."""
    cliente = _ingresar(app)
    respuesta = cliente.get("/?desde=no-es-fecha&cliente=abc&orden=inventado&pagina=-5")
    assert respuesta.status_code == 200
