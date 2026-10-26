"""Pruebas de la etapa 3: normalización asistida y ABM del maestro.

Lo que se protege acá es la propiedad más cara del sistema: que una decisión tomada
una vez valga para todas las filas que comparten el nombre, que quede escrita como
regla (y por lo tanto sobreviva a una reimportación) y que se pueda deshacer.
"""

from datetime import date

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import (
    Cliente,
    DetalleMezcla,
    EstadoMapeo,
    EstadoNormalizacion,
    EstadoProducto,
    EstadoUsuario,
    MapeoProducto,
    Mezcla,
    Producto,
    ClaveRol,
    Tratamiento,
    Usuario,
)
from app.services import importacion, indicadores, maestro, mapeo
from app.services import normalizacion as n

PASSWORD = "clave-de-prueba-larga"

# El mismo nombre mal escrito repetido: es el caso que justifica toda la etapa.
CRUDO_REPETIDO = "Coadyubante Silic."
CRUDO_UNICO = "Ciper 25 (s/d)"
CRUDO_BASURA = "s/n"


@pytest.fixture
def app():
    aplicacion = aislar_sesion_entre_clientes(crear_app("pruebas"))
    with aplicacion.app_context():
        db.create_all()
        _sembrar()
        yield aplicacion
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_con_csrf(app):
    """La misma app, con la validación de CSRF prendida.

    `CSRFProtect` lee la config en cada petición, así que alcanza con cambiar la
    bandera después de crear la aplicación: no hace falta una fábrica aparte.
    """
    app.config["WTF_CSRF_ENABLED"] = True
    yield app
    app.config["WTF_CSRF_ENABLED"] = False


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

    for nombre, activo, categoria in [
        ("Coadyuvante Silic", "Siliconado no iónico", "Coadyuvante"),
        ("Cipermetrina 25", "Cipermetrina 25%", "Insecticida"),
        ("Round Up Full", "Glifosato 66,2%", "Herbicida"),
    ]:
        db.session.add(
            Producto(
                nombre_comercial=nombre,
                clave_normalizada=n.clave(nombre),
                activo=activo,
                categoria=categoria,
            )
        )
    db.session.flush()

    # Seis tratamientos. El crudo repetido aparece en cinco; el único, en uno.
    for indice in range(1, 7):
        tratamiento = Tratamiento(
            numero_tratamiento=f"T-{indice:04d}",
            fecha=date(2025, 3, indice),
            cliente_id=cliente.id,
            cultivo="Soja",
        )
        db.session.add(tratamiento)
        db.session.flush()
        mezcla = Mezcla(tratamiento_id=tratamiento.id, nombre_mezcla=f"Mezcla {indice}")
        db.session.add(mezcla)
        db.session.flush()

        crudos = [CRUDO_REPETIDO] if indice <= 5 else [CRUDO_UNICO, CRUDO_BASURA]
        for orden, crudo in enumerate(crudos, start=1):
            db.session.add(
                DetalleMezcla(
                    mezcla_id=mezcla.id,
                    orden_producto=orden,
                    nombre_original=crudo,
                    clave_normalizada=n.clave(crudo),
                    estado_normalizacion=EstadoNormalizacion.PENDIENTE,
                )
            )
    db.session.commit()


def _tecnica():
    return db.session.scalar(db.select(Usuario).filter_by(email="tecnica@bahnsa.com.ar"))


def _producto(nombre):
    return db.session.scalar(
        db.select(Producto).filter_by(clave_normalizada=n.clave(nombre))
    )


def _ingresar(app, email):
    cliente = app.test_client()
    cliente.post("/acceso/", data={"email": email, "password": PASSWORD})
    return cliente


# ------------------------------------------------------- puntaje de parecido


@pytest.mark.parametrize(
    "escrito, maestro_nombre",
    [
        ("Coadyubante Silic.", "Coadyuvante Silic"),   # error de tipeo
        ("Ciper 25", "Cipermetrina 25"),               # abreviatura
        ("Round-up FULL x20", "Round Up Full"),        # ruido de envase
        ("ROUND UP FÜLL", "Round Up Full"),            # acentos y mayúsculas
    ],
)
def test_el_puntaje_reconoce_las_formas_tipicas_de_escribir_mal(escrito, maestro_nombre):
    assert n.puntaje(n.clave(escrito), n.clave(maestro_nombre)) >= n.UMBRAL_MEDIA


def test_dos_concentraciones_distintas_nunca_llegan_a_confianza_alta():
    """El caso que no se puede errar: mismo texto, distinto número, otro producto."""
    valor = n.puntaje(n.clave("Glifosato 48"), n.clave("Glifosato 62"))
    assert valor <= n.TECHO_CONCENTRACION
    assert n.confianza(valor) != "alta"


def test_productos_sin_relacion_no_aparecen_como_candidatos():
    assert n.puntaje(n.clave("Atrazina 90"), n.clave("Round Up Full")) < n.UMBRAL_SUGERENCIA


# -------------------------------------------------------------- la bandeja


def test_la_bandeja_agrupa_por_clave_y_cuenta_las_filas(app):
    pendientes = mapeo.listar_pendientes()
    claves = {p.clave: p for p in pendientes}

    assert len(pendientes) == 3, "un renglón por nombre distinto, no por fila"
    assert claves[n.clave(CRUDO_REPETIDO)].usos == 5
    # Y el más repetido va primero: resolverlo es lo que más mueve la aguja.
    assert pendientes[0].clave == n.clave(CRUDO_REPETIDO)


def test_cada_pendiente_muestra_donde_se_usa(app):
    """Los ejemplos son los más recientes: es el contexto que sirve para decidir."""
    pendiente = mapeo.listar_pendientes()[0]
    assert pendiente.nombre == CRUDO_REPETIDO
    assert pendiente.tratamientos == ["T-0005", "T-0004", "T-0003", "T-0002"]


def test_la_sugerencia_propone_primero_el_producto_correcto(app):
    pendiente = mapeo.listar_pendientes()[0]
    assert pendiente.mejor is not None
    assert pendiente.mejor.producto.nombre_comercial == "Coadyuvante Silic"
    assert pendiente.mejor.confianza == "alta"


def test_la_sugerencia_tambien_mira_el_principio_activo(app):
    """«Glifosato» no se parece a «Round Up Full», pero es su activo."""
    sugerencias = mapeo.sugerencias_para("Glifosato 66,2%")
    assert sugerencias
    assert sugerencias[0].producto.nombre_comercial == "Round Up Full"
    assert "activo" in sugerencias[0].motivo


def test_los_productos_discontinuados_no_se_sugieren(app):
    producto = _producto("Coadyuvante Silic")
    maestro.cambiar_estado(producto, EstadoProducto.DISCONTINUADO, _tecnica())
    nombres = [s.producto.nombre_comercial for s in mapeo.sugerencias_para(CRUDO_REPETIDO)]
    assert "Coadyuvante Silic" not in nombres


# -------------------------------------------------------------- decisiones


def test_confirmar_resuelve_todas_las_filas_de_una_vez(app):
    producto = _producto("Coadyuvante Silic")
    resultado = mapeo.confirmar(n.clave(CRUDO_REPETIDO), producto.id, _tecnica())

    assert resultado.filas_afectadas == 5
    filas = db.session.scalars(
        db.select(DetalleMezcla).filter_by(clave_normalizada=n.clave(CRUDO_REPETIDO))
    ).all()
    assert all(f.producto_id == producto.id for f in filas)
    assert all(f.estado_normalizacion is EstadoNormalizacion.CONFIRMADA for f in filas)


def test_confirmar_deja_escrita_la_regla_y_no_solo_el_dato(app):
    """Lo que hace que la próxima importación no vuelva a preguntar."""
    producto = _producto("Coadyuvante Silic")
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), producto.id, _tecnica())

    regla = db.session.scalar(
        db.select(MapeoProducto).filter_by(clave_normalizada=n.clave(CRUDO_REPETIDO))
    )
    assert regla is not None
    assert regla.estado is EstadoMapeo.CONFIRMADO
    assert regla.producto_id == producto.id
    assert regla.confirmado_por_id == _tecnica().id


def test_confirmar_contra_un_producto_inexistente_no_toca_nada(app):
    with pytest.raises(mapeo.DecisionInvalida):
        mapeo.confirmar(n.clave(CRUDO_REPETIDO), 9999, _tecnica())
    assert mapeo.contar_pendientes() == 3


def test_descartar_saca_el_texto_de_pendientes_sin_inventar_un_producto(app):
    resultado = mapeo.descartar(n.clave(CRUDO_BASURA), _tecnica(), motivo="celda vacía")

    assert resultado.filas_afectadas == 1
    fila = db.session.scalar(
        db.select(DetalleMezcla).filter_by(clave_normalizada=n.clave(CRUDO_BASURA))
    )
    assert fila.estado_normalizacion is EstadoNormalizacion.SIN_PRODUCTO
    assert fila.producto_id is None
    assert mapeo.contar_pendientes() == 2


def test_deshacer_devuelve_las_filas_a_pendiente_y_borra_la_regla(app):
    producto = _producto("Coadyuvante Silic")
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), producto.id, _tecnica())

    resultado = mapeo.deshacer(n.clave(CRUDO_REPETIDO), _tecnica())

    assert resultado.filas_afectadas == 5
    assert mapeo.contar_pendientes() == 3
    assert db.session.scalar(
        db.select(MapeoProducto).filter_by(clave_normalizada=n.clave(CRUDO_REPETIDO))
    ) is None


def test_una_decision_mueve_la_salud_del_dato(app):
    """La bandeja existe para mover este número: se verifica de punta a punta."""
    antes = indicadores.salud_del_dato()
    assert antes.pendientes == 7

    mapeo.confirmar(n.clave(CRUDO_REPETIDO), _producto("Coadyuvante Silic").id, _tecnica())

    despues = indicadores.salud_del_dato()
    assert despues.pendientes == 2
    assert despues.porcentaje_resuelto > antes.porcentaje_resuelto


def test_lo_resuelto_aparece_en_la_pestana_de_resueltos(app):
    mapeo.confirmar(n.clave(CRUDO_UNICO), _producto("Cipermetrina 25").id, _tecnica())
    resueltos = mapeo.listar_resueltos()
    assert len(resueltos) == 1
    assert resueltos[0].mapeo.producto.nombre_comercial == "Cipermetrina 25"
    assert resueltos[0].usos == 1


# ------------------------------------------- la decisión sobrevive al reimporte


def test_una_reimportacion_reusa_el_mapeo_confirmado(app):
    """Sin esto, la etapa 3 sería trabajo que se borra solo.

    Se prueba contra `_resolver_producto` porque es la unidad exacta donde el
    importador decide: hacerlo con un archivo completo probaría openpyxl, no la regla.
    """
    producto = _producto("Coadyuvante Silic")
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), producto.id, _tecnica())

    cache = importacion._Cache()
    # Otra grafía del mismo nombre: llega por la clave, no por el texto exacto.
    _, producto_id, estado = importacion._resolver_producto(cache, "COADYUBANTE   silic.")

    assert producto_id == producto.id
    assert estado is EstadoNormalizacion.CONFIRMADA


def test_una_reimportacion_respeta_lo_que_se_descarto(app):
    """«No es un producto» también es una decisión: no puede volver a PENDIENTE."""
    mapeo.descartar(n.clave(CRUDO_BASURA), _tecnica(), motivo="celda sin dato")

    cache = importacion._Cache()
    _, producto_id, estado = importacion._resolver_producto(cache, CRUDO_BASURA)

    assert producto_id is None
    assert estado is EstadoNormalizacion.SIN_PRODUCTO


# ------------------------------------------------------------ ABM del maestro


def test_no_se_puede_dar_de_alta_un_duplicado_del_maestro(app):
    with pytest.raises(maestro.DatosInvalidos):
        maestro.crear({"nombre_comercial": "ROUND-UP  full"}, _tecnica())


def test_el_alta_exige_nombre_comercial(app):
    with pytest.raises(maestro.DatosInvalidos):
        maestro.crear({"nombre_comercial": "   ", "activo": "algo"}, _tecnica())


def test_editar_el_nombre_recalcula_la_clave(app):
    producto = _producto("Cipermetrina 25")
    maestro.editar(producto, {"nombre_comercial": "Cipermetrina 25 EC"}, _tecnica())
    assert producto.clave_normalizada == n.clave("Cipermetrina 25 EC")


def test_discontinuar_no_borra_ni_desarma_el_historico(app):
    producto = _producto("Coadyuvante Silic")
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), producto.id, _tecnica())

    maestro.cambiar_estado(producto, EstadoProducto.DISCONTINUADO, _tecnica())

    assert db.session.get(Producto, producto.id) is not None
    assert maestro.ficha(producto.id).usos == 5


def test_fusionar_mueve_usos_y_reglas_al_producto_que_queda(app):
    """El caso real: el Excel trajo el mismo producto escrito de dos formas."""
    duplicado = maestro.crear({"nombre_comercial": "Coadyuvante Silicona"}, _tecnica())
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), duplicado.id, _tecnica())
    bueno = _producto("Coadyuvante Silic")

    movidos = maestro.fusionar(origen=duplicado, destino=bueno, usuario=_tecnica())

    assert movidos == 5
    assert duplicado.estado is EstadoProducto.DISCONTINUADO
    assert maestro.ficha(bueno.id).usos == 5
    # La regla también se mudó: si no, la próxima importación revive el duplicado.
    regla = db.session.scalar(
        db.select(MapeoProducto).filter_by(clave_normalizada=n.clave(CRUDO_REPETIDO))
    )
    assert regla.producto_id == bueno.id


def test_no_se_puede_fusionar_un_producto_consigo_mismo(app):
    producto = _producto("Round Up Full")
    with pytest.raises(maestro.DatosInvalidos):
        maestro.fusionar(origen=producto, destino=producto, usuario=_tecnica())


# ------------------------------------------------------------------ vistas


def test_la_bandeja_se_ve_y_muestra_los_pendientes(app):
    cliente = _ingresar(app, "tecnica@bahnsa.com.ar")
    respuesta = cliente.get("/normalizacion/")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert CRUDO_REPETIDO in cuerpo
    assert "Coadyuvante Silic" in cuerpo  # la sugerencia


def test_el_rol_comercial_no_entra_a_la_bandeja(app):
    cliente = _ingresar(app, "comercial@bahnsa.com.ar")
    assert cliente.get("/normalizacion/").status_code == 403


def test_el_rol_comercial_tampoco_puede_confirmar_por_post(app):
    """La pantalla se esconde y además la acción se rechaza: las dos capas."""
    cliente = _ingresar(app, "comercial@bahnsa.com.ar")
    respuesta = cliente.post(
        "/normalizacion/confirmar",
        data={"clave": n.clave(CRUDO_REPETIDO), "producto_id": _producto("Coadyuvante Silic").id},
    )
    assert respuesta.status_code == 403
    assert mapeo.contar_pendientes() == 3


def test_confirmar_desde_afuera_sin_token_csrf_no_hace_nada(app_con_csrf):
    """Cada acción de la bandeja modifica decenas de filas de un solo POST.

    La config de pruebas desactiva la validación de CSRF para que el resto de los
    tests no tenga que arrastrar tokens, así que esta protección hay que probarla
    con la validación prendida a propósito: si no, nadie se entera el día que un
    formulario se quede sin `hidden_tag()`.
    """
    cliente = app_con_csrf.test_client()
    cliente.post("/acceso/", data={"email": "tecnica@bahnsa.com.ar", "password": PASSWORD,
                                   "csrf_token": _token(cliente, "/acceso/")})

    respuesta = cliente.post(
        "/normalizacion/confirmar",
        data={"clave": n.clave(CRUDO_REPETIDO), "producto_id": _producto("Coadyuvante Silic").id},
        headers={"HX-Request": "true"},
    )

    assert respuesta.status_code == 400
    assert mapeo.contar_pendientes() == 3, "ninguna fila se tocó"


def test_la_bandeja_emite_el_token_en_todos_sus_formularios(app_con_csrf):
    """La otra mitad de lo mismo: con la validación prendida, la pantalla tiene que
    seguir siendo usable. Un formulario sin token es un botón que siempre falla."""
    cliente = app_con_csrf.test_client()
    cliente.post("/acceso/", data={"email": "tecnica@bahnsa.com.ar", "password": PASSWORD,
                                   "csrf_token": _token(cliente, "/acceso/")})
    cuerpo = cliente.get("/normalizacion/").get_data(as_text=True)

    # Solo los POST necesitan token: el formulario de búsqueda es un GET.
    formularios = cuerpo.count('method="post"')
    tokens = cuerpo.count('name="csrf_token"')
    assert formularios >= 3
    assert tokens >= formularios


def _token(cliente, ruta: str) -> str:
    """Saca el token de un formulario ya renderizado, como haría el navegador."""
    import re

    encontrado = re.search(
        r'name="csrf_token"[^>]*value="([^"]+)"', cliente.get(ruta).get_data(as_text=True)
    )
    assert encontrado, f"{ruta} debería traer un token CSRF"
    return encontrado.group(1)


def test_confirmar_por_htmx_devuelve_la_tarjeta_ya_resuelta(app):
    cliente = _ingresar(app, "tecnica@bahnsa.com.ar")

    respuesta = cliente.post(
        "/normalizacion/confirmar",
        data={
            "clave": n.clave(CRUDO_REPETIDO),
            "producto_id": _producto("Coadyuvante Silic").id,
        },
        headers={"HX-Request": "true"},
    )
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Coadyuvante Silic" in cuerpo
    assert "Deshacer" in cuerpo
    assert "<html" not in cuerpo, "es un fragmento, no una página entera"
    assert mapeo.contar_pendientes() == 2


def test_el_alta_desde_la_bandeja_crea_y_asocia_en_un_solo_paso(app):
    cliente = _ingresar(app, "tecnica@bahnsa.com.ar")

    respuesta = cliente.post(
        "/normalizacion/crear-producto",
        data={
            "clave": n.clave(CRUDO_BASURA),
            "nombre_comercial": "Producto Nuevo XL",
            "activo": "Activo nuevo",
        },
        headers={"HX-Request": "true"},
    )

    assert respuesta.status_code == 200
    creado = _producto("Producto Nuevo XL")
    assert creado is not None
    assert maestro.ficha(creado.id).usos == 1


def test_la_ficha_del_producto_muestra_como_se_lo_escribio(app):
    mapeo.confirmar(n.clave(CRUDO_REPETIDO), _producto("Coadyuvante Silic").id, _tecnica())
    cliente = _ingresar(app, "tecnica@bahnsa.com.ar")

    respuesta = cliente.get(f"/productos/{_producto('Coadyuvante Silic').id}")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert CRUDO_REPETIDO in cuerpo


def test_el_comercial_ve_la_ficha_pero_no_los_botones_de_mantenimiento(app):
    cliente = _ingresar(app, "comercial@bahnsa.com.ar")
    respuesta = cliente.get(f"/productos/{_producto('Round Up Full').id}")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Discontinuar" not in cuerpo
    assert cliente.get(f"/productos/{_producto('Round Up Full').id}/editar").status_code == 403


# ------------------------------------------------- la unidad de trabajo es atómica


def test_una_operacion_compuesta_entra_entera_o_no_entra(app):
    """El alta desde la bandeja crea el producto y lo asocia: son un solo movimiento.

    Si el segundo paso falla, el producto no puede quedar en el maestro. Antes esto
    dependía de que la vista se acordara de llamar a `rollback()`; ahora lo garantiza
    la transacción.
    """
    from app.unidad_de_trabajo import transaccion

    antes = db.session.scalar(db.select(db.func.count(Producto.id)))

    with pytest.raises(mapeo.DecisionInvalida):
        with transaccion():
            maestro.crear({"nombre_comercial": "Producto A Medias"}, _tecnica())
            # Producto inexistente: la segunda mitad de la operación falla.
            mapeo.confirmar(n.clave(CRUDO_REPETIDO), 99999, _tecnica())

    assert db.session.scalar(db.select(db.func.count(Producto.id))) == antes
    assert _producto("Producto A Medias") is None


def test_las_transacciones_anidadas_confirman_una_sola_vez(app):
    """Un servicio no tiene que saber si es la operación entera o parte de otra."""
    from app.unidad_de_trabajo import en_transaccion, transaccion

    assert not en_transaccion()
    with transaccion():
        assert en_transaccion()
        with transaccion():
            producto = maestro.crear({"nombre_comercial": "Producto Anidado"}, _tecnica())
            assert en_transaccion()
        # Todavía adentro de la externa: nada se confirmó aún.
    assert not en_transaccion()
    assert _producto("Producto Anidado") is not None
    assert producto.id is not None
