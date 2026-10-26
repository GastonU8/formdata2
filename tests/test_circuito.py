"""Pruebas del circuito de solicitudes.

El foco está en lo que no se puede permitir: saltear etapas, aprobar la propia
solicitud sin ser supervisor, rechazar sin motivo, o finalizar sin resultado.
"""

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import Cliente, EstadoFlujo, EstadoNormalizacion, EstadoUsuario, Producto, ClaveRol, Usuario
from app.services import flujo
from app.services import normalizacion as norm
from app.services import solicitudes

PASSWORD = "clave-de-prueba-larga"

CUENTAS = [
    ("Bruno Comercial", "comercial@bahnsa.com.ar", ClaveRol.COMERCIAL),
    ("Marcela Supervisora", "supervisora@bahnsa.com.ar", ClaveRol.SUPERVISOR),
    ("Ana Técnica", "tecnica@bahnsa.com.ar", ClaveRol.TECNICO),
    ("Otro Comercial", "comercial2@bahnsa.com.ar", ClaveRol.COMERCIAL),
]


@pytest.fixture
def app():
    aplicacion = aislar_sesion_entre_clientes(crear_app("pruebas"))
    with aplicacion.app_context():
        db.create_all()
        for nombre, email, rol in CUENTAS:
            usuario = Usuario(nombre=nombre, email=email, rol=rol_de(rol), estado=EstadoUsuario.ACTIVO)
            usuario.definir_password(PASSWORD)
            db.session.add(usuario)
        db.session.add(Cliente(nombre="Agro Sur SA", clave_normalizada=norm.clave("Agro Sur SA")))
        db.session.add(
            Producto(
                nombre_comercial="Round Up Full",
                clave_normalizada=norm.clave("Round Up Full"),
                activo="Glifosato 66,2%", categoria="Herbicida",
            )
        )
        db.session.commit()
        yield aplicacion
        db.session.remove()
        db.drop_all()


def usuario(email):
    return db.session.scalar(db.select(Usuario).filter_by(email=email))


def crear(autor_email="comercial@bahnsa.com.ar", enviar=False, **extra):
    datos = dict(
        autor=usuario(autor_email),
        cliente_id=None,
        cultivo="Soja",
        tipo_tratamiento="Compatibilidad",
        target="Malezas",
        observaciones="",
        fecha=None,
        lineas=[
            solicitudes.LineaProducto("Round Up Full", 2.0, "l/ha"),
            solicitudes.LineaProducto("Coadyubante XR", 0.5, "l/ha"),
        ],
        enviar=enviar,
    )
    datos.update(extra)
    return solicitudes.crear_solicitud(**datos)


# ----------------------------------------------------------------------- alta

def test_la_solicitud_nace_en_borrador(app):
    solicitud = crear()
    assert solicitud.estado_flujo is EstadoFlujo.BORRADOR
    assert solicitud.origen.value == "solicitud"
    assert solicitud.solicitado_por.email == "comercial@bahnsa.com.ar"


def test_la_numeracion_es_correlativa_por_anio(app):
    assert crear().numero_tratamiento.endswith("-0001")
    assert crear().numero_tratamiento.endswith("-0002")


def test_los_productos_conocidos_se_resuelven_solos(app):
    detalles = crear().mezclas[0].detalles
    conocido, desconocido = detalles[0], detalles[1]
    assert conocido.producto is not None
    assert conocido.estado_normalizacion is EstadoNormalizacion.AUTOMATICA
    # El que no está en el maestro no se inventa: queda pendiente, como una fila importada.
    assert desconocido.producto is None
    assert desconocido.estado_normalizacion is EstadoNormalizacion.PENDIENTE


def test_no_se_admite_una_mezcla_sin_productos(app):
    with pytest.raises(solicitudes.DatosInvalidos):
        crear(lineas=[])


def test_no_se_admite_sin_cultivo(app):
    with pytest.raises(solicitudes.DatosInvalidos):
        crear(cultivo="   ")


# ----------------------------------------------------------------- circuito

def test_el_camino_feliz_completo(app):
    comercial, supervisora, tecnica = (
        usuario("comercial@bahnsa.com.ar"),
        usuario("supervisora@bahnsa.com.ar"),
        usuario("tecnica@bahnsa.com.ar"),
    )
    solicitud = crear()

    flujo.ejecutar(solicitud, "enviar", comercial)
    assert solicitud.estado_flujo is EstadoFlujo.PENDIENTE_APROBACION

    flujo.ejecutar(solicitud, "aprobar", supervisora)
    assert solicitud.estado_flujo is EstadoFlujo.APROBADA
    assert solicitud.aprobado_por_id == supervisora.id

    flujo.ejecutar(solicitud, "tomar", tecnica)
    assert solicitud.estado_flujo is EstadoFlujo.EN_ENSAYO
    assert solicitud.tomado_por_id == tecnica.id

    solicitudes.cargar_resultado(
        tratamiento=solicitud, usuario=tecnica,
        datos={"porcentaje_efectividad": 91.0, "estado_resultado": "Compatible"},
    )
    assert solicitud.estado_flujo is EstadoFlujo.FINALIZADA
    assert solicitud.resultado.estado_resultado == "Compatible"
    assert [h.accion for h in solicitud.historial] == ["enviar", "aprobar", "tomar", "finalizar"]


def test_no_se_puede_saltear_la_aprobacion(app):
    solicitud = crear()
    with pytest.raises(flujo.TransicionInvalida):
        flujo.ejecutar(solicitud, "tomar", usuario("tecnica@bahnsa.com.ar"))


def test_el_comercial_no_puede_aprobar_su_propia_solicitud(app):
    comercial = usuario("comercial@bahnsa.com.ar")
    solicitud = crear(enviar=True)
    with pytest.raises(flujo.TransicionInvalida):
        flujo.ejecutar(solicitud, "aprobar", comercial)


def test_el_tecnico_no_puede_aprobar(app):
    solicitud = crear(enviar=True)
    with pytest.raises(flujo.TransicionInvalida):
        flujo.ejecutar(solicitud, "aprobar", usuario("tecnica@bahnsa.com.ar"))


def test_otro_comercial_no_puede_enviar_una_solicitud_ajena(app):
    solicitud = crear()
    with pytest.raises(flujo.TransicionInvalida):
        flujo.ejecutar(solicitud, "enviar", usuario("comercial2@bahnsa.com.ar"))


def test_el_rechazo_exige_motivo(app):
    solicitud = crear(enviar=True)
    supervisora = usuario("supervisora@bahnsa.com.ar")
    with pytest.raises(flujo.TransicionInvalida):
        flujo.ejecutar(solicitud, "rechazar", supervisora, motivo="   ")

    flujo.ejecutar(solicitud, "rechazar", supervisora, motivo="Falta la dosis del coadyuvante")
    assert solicitud.estado_flujo is EstadoFlujo.RECHAZADA
    assert "coadyuvante" in solicitud.motivo_rechazo


def test_el_rechazo_se_corrige_y_se_reenvia(app):
    comercial, supervisora = usuario("comercial@bahnsa.com.ar"), usuario("supervisora@bahnsa.com.ar")
    solicitud = crear(enviar=True)
    flujo.ejecutar(solicitud, "rechazar", supervisora, motivo="Revisar dosis")

    flujo.ejecutar(solicitud, "corregir", comercial)
    assert solicitud.estado_flujo is EstadoFlujo.BORRADOR
    assert solicitud.motivo_rechazo is None

    flujo.ejecutar(solicitud, "enviar", comercial)
    assert solicitud.estado_flujo is EstadoFlujo.PENDIENTE_APROBACION
    assert [h.accion for h in solicitud.historial] == ["enviar", "rechazar", "corregir", "enviar"]


def test_no_se_finaliza_sin_conclusion(app):
    solicitud = crear(enviar=True)
    supervisora, tecnica = usuario("supervisora@bahnsa.com.ar"), usuario("tecnica@bahnsa.com.ar")
    flujo.ejecutar(solicitud, "aprobar", supervisora)
    flujo.ejecutar(solicitud, "tomar", tecnica)

    with pytest.raises(solicitudes.DatosInvalidos):
        solicitudes.cargar_resultado(
            tratamiento=solicitud, usuario=tecnica, datos={"porcentaje_efectividad": 90}
        )
    assert solicitud.estado_flujo is EstadoFlujo.EN_ENSAYO


def test_las_mediciones_fuera_de_rango_se_rechazan(app):
    solicitud = crear(enviar=True)
    supervisora, tecnica = usuario("supervisora@bahnsa.com.ar"), usuario("tecnica@bahnsa.com.ar")
    flujo.ejecutar(solicitud, "aprobar", supervisora)
    flujo.ejecutar(solicitud, "tomar", tecnica)

    with pytest.raises(solicitudes.DatosInvalidos):
        solicitudes.cargar_resultado(
            tratamiento=solicitud, usuario=tecnica,
            datos={"ph_agua": 22, "estado_resultado": "Compatible"},
        )


def test_las_acciones_disponibles_dependen_del_rol_y_del_estado(app):
    solicitud = crear()
    comercial, supervisora, tecnica = (
        usuario("comercial@bahnsa.com.ar"),
        usuario("supervisora@bahnsa.com.ar"),
        usuario("tecnica@bahnsa.com.ar"),
    )
    acciones = lambda u: {a.accion for a in flujo.acciones_disponibles(solicitud, u)}

    assert acciones(comercial) == {"enviar"}
    assert acciones(tecnica) == set()

    flujo.ejecutar(solicitud, "enviar", comercial)
    assert acciones(supervisora) == {"aprobar", "rechazar", "anular"}
    assert acciones(comercial) == set()


def test_anular_corta_el_circuito(app):
    solicitud = crear(enviar=True)
    supervisora = usuario("supervisora@bahnsa.com.ar")
    flujo.ejecutar(solicitud, "anular", supervisora, motivo="El cliente dio de baja el pedido")
    assert solicitud.estado_flujo is EstadoFlujo.ANULADA
    assert flujo.acciones_disponibles(solicitud, supervisora) == []


# --------------------------------------------------------------------- HTTP

def ingresar(app, email):
    cli = app.test_client()
    cli.post("/acceso/", data={"email": email, "password": PASSWORD})
    return cli


def test_solo_el_comercial_ve_el_alta(app):
    assert ingresar(app, "comercial@bahnsa.com.ar").get("/solicitudes/nueva").status_code == 200
    assert ingresar(app, "tecnica@bahnsa.com.ar").get("/solicitudes/nueva").status_code == 403


def test_la_bandeja_renderiza_para_cada_rol(app):
    crear(enviar=True)
    for email in [c[1] for c in CUENTAS[:3]]:
        assert ingresar(app, email).get("/solicitudes/").status_code == 200


def test_el_alta_por_formulario_crea_y_envia(app):
    cli = ingresar(app, "comercial@bahnsa.com.ar")
    respuesta = cli.post(
        "/solicitudes/nueva",
        data={
            "cultivo": "Maíz", "tipo": "Compatibilidad", "target": "Chinche",
            "producto_nombre": ["Round Up Full", ""],
            "producto_dosis": ["1,5", ""],
            "producto_unidad": ["l/ha", ""],
            "accion": "enviar",
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 302
    creada = db.session.scalar(db.select(solicitudes.Tratamiento))
    assert creada.estado_flujo is EstadoFlujo.PENDIENTE_APROBACION
    assert len(creada.mezclas[0].detalles) == 1   # la fila vacía se descarta
    assert float(creada.mezclas[0].detalles[0].dosis) == 1.5   # coma decimal aceptada


def test_una_transicion_prohibida_por_url_no_pasa(app):
    solicitud = crear(enviar=True)
    cli = ingresar(app, "tecnica@bahnsa.com.ar")
    cli.post(f"/solicitudes/{solicitud.id}/aprobar", follow_redirects=True)
    db.session.refresh(solicitud)
    assert solicitud.estado_flujo is EstadoFlujo.PENDIENTE_APROBACION


def test_el_detalle_muestra_el_historial(app):
    solicitud = crear(enviar=True)
    flujo.ejecutar(solicitud, "rechazar", usuario("supervisora@bahnsa.com.ar"), motivo="Dosis alta")
    html = ingresar(app, "comercial@bahnsa.com.ar").get(f"/solicitudes/{solicitud.id}").data.decode()
    assert "Dosis alta" in html and "Rechazada" in html
