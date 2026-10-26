"""Pruebas de la etapa 1. Se ejecutan con `pytest` desde la raíz del proyecto."""

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import EstadoUsuario, RegistroAuditoria, ClaveRol, Usuario
from app.security.roles import puede
from app.services import normalizacion

PASSWORD = "clave-de-prueba-larga"


@pytest.fixture
def app():
    aplicacion = aislar_sesion_entre_clientes(crear_app("pruebas"))
    with aplicacion.app_context():
        db.create_all()
        for nombre, email, rol in [
            ("Ana Técnica", "tecnica@bahnsa.com.ar", ClaveRol.TECNICO),
            ("Bruno Comercial", "comercial@bahnsa.com.ar", ClaveRol.COMERCIAL),
        ]:
            usuario = Usuario(nombre=nombre, email=email, rol=rol_de(rol), estado=EstadoUsuario.ACTIVO)
            usuario.definir_password(PASSWORD)
            db.session.add(usuario)
        db.session.commit()
        yield aplicacion
        db.session.remove()
        db.drop_all()


@pytest.fixture
def cliente(app):
    return app.test_client()


def ingresar(cliente, email, password=PASSWORD):
    return cliente.post(
        "/acceso/", data={"email": email, "password": password}, follow_redirects=False
    )


# --------------------------------------------------------------- credenciales

def test_la_password_no_se_guarda_en_claro(app):
    usuario = db.session.scalar(db.select(Usuario).filter_by(email="tecnica@bahnsa.com.ar"))
    assert PASSWORD not in usuario.password_hash
    assert usuario.password_hash.startswith("$argon2")
    assert usuario.password_es_correcta(PASSWORD)
    assert not usuario.password_es_correcta("otra-cosa")


def test_el_login_correcto_redirige_al_tablero(cliente):
    respuesta = ingresar(cliente, "tecnica@bahnsa.com.ar")
    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == "/"


def test_el_login_no_revela_si_el_email_existe(cliente):
    inexistente = ingresar(cliente, "nadie@bahnsa.com.ar")
    existente = ingresar(cliente, "tecnica@bahnsa.com.ar", "password-equivocada")
    assert inexistente.status_code == existente.status_code == 401
    assert b"incorrectos" in inexistente.data and b"incorrectos" in existente.data


def test_la_cuenta_se_bloquea_tras_intentos_fallidos(app, cliente):
    for _ in range(app.config["MAX_INTENTOS_LOGIN"]):
        ingresar(cliente, "tecnica@bahnsa.com.ar", "mal")
    respuesta = ingresar(cliente, "tecnica@bahnsa.com.ar")
    assert respuesta.status_code == 429
    assert b"bloqueada" in respuesta.data


# ------------------------------------------------------------------- permisos

def test_el_tablero_exige_sesion(cliente):
    respuesta = cliente.get("/", follow_redirects=False)
    assert respuesta.status_code == 302
    assert "/acceso/" in respuesta.headers["Location"]


def test_el_comercial_no_puede_importar(app):
    comercial = db.session.scalar(db.select(Usuario).filter_by(email="comercial@bahnsa.com.ar"))
    tecnica = db.session.scalar(db.select(Usuario).filter_by(email="tecnica@bahnsa.com.ar"))
    assert not puede("importar_excel", comercial)
    assert puede("importar_excel", tecnica)
    assert puede("consultar_tratamientos", comercial)
    assert not puede("gestionar_usuarios", tecnica)


def test_el_destino_externo_no_se_respeta(cliente):
    respuesta = cliente.post(
        "/acceso/?next=https://sitio-malicioso.example.org",
        data={"email": "tecnica@bahnsa.com.ar", "password": PASSWORD},
    )
    assert respuesta.headers["Location"] == "/"


# ------------------------------------------------------------------ auditoría

def test_el_login_queda_auditado(app, cliente):
    ingresar(cliente, "tecnica@bahnsa.com.ar", "mal")
    ingresar(cliente, "tecnica@bahnsa.com.ar")
    entradas = db.session.scalars(db.select(RegistroAuditoria)).all()
    assert [e.exito for e in entradas] == [False, True]
    assert all(PASSWORD not in str(e.datos) for e in entradas)


# ---------------------------------------------------------------- cabeceras

def test_las_cabeceras_de_seguridad_estan_presentes(cliente):
    cabeceras = cliente.get("/acceso/").headers
    assert cabeceras["X-Frame-Options"] == "DENY"
    assert cabeceras["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in cabeceras["Content-Security-Policy"]


# ------------------------------------------------------------- normalización

@pytest.mark.parametrize(
    "escrito",
    ["Round Up Full", "  round-up   full ", "ROUND UP FÜLL", "Round Up Full x 20 Lt"],
)
def test_las_variantes_colapsan_en_la_misma_clave(escrito):
    assert normalizacion.clave(escrito) == "round up full"


def test_productos_distintos_no_colapsan():
    assert normalizacion.clave("Glifosato 48") != normalizacion.clave("Glifosato 62")


def test_la_sugerencia_encuentra_el_producto_parecido():
    candidatos = {"round up full": 1, "atrazina 90": 2}
    sugeridos = normalizacion.sugerir("Roundup Ful", candidatos)
    assert sugeridos and sugeridos[0][0] == 1


def test_el_hash_rapido_de_pruebas_no_se_filtra_a_produccion():
    """Un proceso que configura las dos veces no puede quedarse con el hash débil.

    `configurar_hasher` asigna en las dos ramas justamente por esto: con un `if` sin
    `else`, el orden de configuración decidiría la fuerza del hash de producción.

    Se prueba la función con una configuración mínima y no construyendo una
    aplicación de producción entera: eso exigiría variables de entorno que se leen
    al importar el módulo de configuración, y terminaría probando el arranque en
    lugar de la regla.
    """
    from types import SimpleNamespace

    from app import extensions

    def configurar(rapido: bool):
        extensions.configurar_hasher(SimpleNamespace(config={"ARGON2_RAPIDO": rapido}))
        return extensions.hasher.memory_cost

    assert configurar(True) == extensions.PARAMETROS_RAPIDOS["memory_cost"]
    assert configurar(False) == extensions.PARAMETROS_FUERTES["memory_cost"]
    assert configurar(True) == extensions.PARAMETROS_RAPIDOS["memory_cost"]
