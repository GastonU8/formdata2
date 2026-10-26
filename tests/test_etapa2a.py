"""Pruebas de la etapa 2A: búsqueda y detalle de tratamientos."""

from datetime import date

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
from app.services import consultas
from app.services import normalizacion as n

PASSWORD = "clave-de-prueba-larga"


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

    cliente = Cliente(nombre="Agro Sur SA", clave_normalizada=n.clave("Agro Sur SA"))
    db.session.add(cliente)

    glifo = Producto(
        nombre_comercial="Round Up Full", clave_normalizada=n.clave("Round Up Full"),
        activo="Glifosato 66,2%", categoria="Herbicida",
    )
    ciper = Producto(
        nombre_comercial="Cipermetrina 25", clave_normalizada=n.clave("Cipermetrina 25"),
        activo="Cipermetrina 25%", categoria="Insecticida",
    )
    db.session.add_all([glifo, ciper])
    db.session.flush()

    # 30 tratamientos: los pares con glifosato en soja, los impares con
    # cipermetrina en maíz. El T-0006 arrastra un producto sin mapear.
    for i in range(1, 31):
        par = i % 2 == 0
        tratamiento = Tratamiento(
            numero_tratamiento=f"T-{i:04d}",
            fecha=date(2025, 1 + (i % 12), 1 + (i % 27)),
            cliente_id=cliente.id,
            cultivo="Soja" if par else "Maíz",
            tipo_tratamiento="Compatibilidad" if par else "Eficacia",
            target="Malezas" if par else "Chinche",
        )
        db.session.add(tratamiento)
        db.session.flush()

        mezcla = Mezcla(tratamiento_id=tratamiento.id, nombre_mezcla=f"Mezcla {i}")
        db.session.add(mezcla)
        db.session.flush()

        producto = glifo if par else ciper
        db.session.add(
            DetalleMezcla(
                mezcla_id=mezcla.id, orden_producto=1,
                nombre_original=producto.nombre_comercial,
                clave_normalizada=producto.clave_normalizada,
                producto_id=producto.id,
                estado_normalizacion=EstadoNormalizacion.AUTOMATICA,
                dosis=2.0, unidad="l/ha",
            )
        )
        if i == 6:
            db.session.add(
                DetalleMezcla(
                    mezcla_id=mezcla.id, orden_producto=2,
                    nombre_original="Coadyubante XR",
                    clave_normalizada=n.clave("Coadyubante XR"),
                    estado_normalizacion=EstadoNormalizacion.PENDIENTE,
                )
            )
        db.session.add(
            Resultado(
                tratamiento_id=tratamiento.id,
                porcentaje_efectividad=50 + i,
                estado_resultado="Compatible" if par else "Incompatible",
            )
        )
    db.session.commit()


@pytest.fixture
def cliente_http(app):
    cli = app.test_client()
    cli.post("/acceso/", data={"email": "tecnica@bahnsa.com.ar", "password": PASSWORD})
    return cli


# ------------------------------------------------------------------- filtros

def test_sin_filtros_trae_todo_paginado(app):
    pagina = consultas.buscar(consultas.Filtros())
    assert pagina.total == 30
    assert len(pagina.items) == consultas.POR_PAGINA


def test_filtro_por_cultivo(app):
    pagina = consultas.buscar(consultas.Filtros(cultivo="Soja"))
    assert pagina.total == 15
    assert all(t.cultivo == "Soja" for t in pagina.items)


def test_filtro_por_activo_del_producto(app):
    pagina = consultas.buscar(consultas.Filtros(producto_texto="Glifosato"))
    assert pagina.total == 15
    assert all(t.cultivo == "Soja" for t in pagina.items)


def test_el_filtro_por_producto_no_duplica_filas(app):
    """Un tratamiento con varios productos coincidentes debe aparecer una sola vez."""
    pagina = consultas.buscar(consultas.Filtros(producto_texto="a"))
    numeros = [t.numero_tratamiento for t in pagina.items]
    assert len(numeros) == len(set(numeros))


def test_filtro_por_nombre_crudo_encuentra_lo_no_mapeado(app):
    """Busca en el texto del Excel, no solo en el maestro: si no, lo sin mapear se pierde."""
    pagina = consultas.buscar(consultas.Filtros(producto_texto="Coadyubante"))
    assert pagina.total == 1
    assert pagina.items[0].numero_tratamiento == "T-0006"


def test_filtro_de_pendientes_de_mapeo(app):
    pagina = consultas.buscar(consultas.Filtros(solo_sin_mapear=True))
    assert [t.numero_tratamiento for t in pagina.items] == ["T-0006"]


def test_filtros_combinados_se_acumulan(app):
    pagina = consultas.buscar(
        consultas.Filtros(cultivo="Soja", estado_resultado="Compatible", producto_texto="Glifosato")
    )
    assert pagina.total == 15
    assert consultas.buscar(
        consultas.Filtros(cultivo="Soja", estado_resultado="Incompatible")
    ).total == 0


def test_filtro_por_rango_de_fechas(app):
    pagina = consultas.buscar(consultas.Filtros(desde=date(2025, 6, 1), hasta=date(2025, 6, 30)))
    assert pagina.total > 0
    assert all(date(2025, 6, 1) <= t.fecha <= date(2025, 6, 30) for t in pagina.items)


def test_orden_por_efectividad(app):
    pagina = consultas.buscar(consultas.Filtros(orden="efectividad"))
    valores = [float(t.resultado.porcentaje_efectividad) for t in pagina.items]
    assert valores == sorted(valores, reverse=True)


def test_busqueda_sin_coincidencias(app):
    pagina = consultas.buscar(consultas.Filtros(texto="no-existe-este-texto"))
    assert pagina.total == 0 and pagina.items == []


def test_las_opciones_salen_de_los_datos(app):
    opciones = consultas.opciones_de_filtro()
    assert set(opciones.cultivos) == {"Soja", "Maíz"}
    assert set(opciones.estados_resultado) == {"Compatible", "Incompatible"}


# -------------------------------------------------------------------- detalle

def test_el_detalle_trae_la_composicion(app):
    tratamiento = consultas.buscar(consultas.Filtros(texto="T-0006")).items[0]
    detalle = consultas.obtener_detalle(tratamiento.id)
    assert len(detalle.mezclas[0].detalles) == 2
    assert detalle.resultado is not None


def test_los_relacionados_comparten_producto_y_cultivo(app):
    tratamiento = consultas.buscar(consultas.Filtros(texto="T-0002")).items[0]
    detalle = consultas.obtener_detalle(tratamiento.id)
    relacionados = consultas.tratamientos_relacionados(detalle)
    assert relacionados
    assert all(r.cultivo == "Soja" and r.id != detalle.id for r in relacionados)


# ---------------------------------------------------------------------- HTTP

def test_la_busqueda_requiere_sesion(app):
    respuesta = app.test_client().get("/tratamientos/")
    assert respuesta.status_code == 302


def test_el_comercial_puede_consultar(app):
    cli = app.test_client()
    cli.post("/acceso/", data={"email": "comercial@bahnsa.com.ar", "password": PASSWORD})
    assert cli.get("/tratamientos/").status_code == 200


def test_la_pantalla_de_busqueda_renderiza(cliente_http):
    respuesta = cliente_http.get("/tratamientos/?cultivo=Soja")
    assert respuesta.status_code == 200
    assert "Soja".encode() in respuesta.data


def test_htmx_devuelve_solo_el_fragmento(cliente_http):
    completa = cliente_http.get("/tratamientos/")
    fragmento = cliente_http.get("/tratamientos/", headers={"HX-Request": "true"})
    assert fragmento.status_code == 200
    assert b"<html" not in fragmento.data
    assert len(fragmento.data) < len(completa.data)


def test_el_detalle_inexistente_da_404(cliente_http):
    assert cliente_http.get("/tratamientos/99999").status_code == 404


def test_el_detalle_muestra_el_producto_y_la_dosis(app, cliente_http):
    tratamiento = consultas.buscar(consultas.Filtros(texto="T-0002")).items[0]
    respuesta = cliente_http.get(f"/tratamientos/{tratamiento.id}")
    assert respuesta.status_code == 200
    html = respuesta.data.decode()
    assert "Round Up Full" in html and "2.00" in html


def test_los_parametros_invalidos_no_rompen(cliente_http):
    respuesta = cliente_http.get(
        "/tratamientos/?pagina=abc&cliente=xx&desde=no-es-fecha&orden=inventado"
    )
    assert respuesta.status_code == 200


# ------------------------------------------------------------------ productos

def test_el_maestro_ordena_por_uso(app):
    resultados = consultas.buscar_productos()
    assert [f["producto"].nombre_comercial for f in resultados][0] in {
        "Round Up Full", "Cipermetrina 25"
    }
    assert resultados[0]["usos"] == 15


def test_busqueda_de_producto_por_categoria(app):
    resultados = consultas.buscar_productos(categoria="Insecticida")
    assert len(resultados) == 1
    assert resultados[0]["producto"].nombre_comercial == "Cipermetrina 25"


def test_la_pantalla_de_productos_renderiza(cliente_http):
    respuesta = cliente_http.get("/productos/?texto=glifosato")
    assert respuesta.status_code == 200
    assert b"Round Up Full" in respuesta.data
