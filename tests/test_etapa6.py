"""Pruebas de la etapa 6: administración de usuarios y bitácora de auditoría.

El hilo de toda la etapa es el mismo: **el usuario es la clave de la auditoría**.
Por eso lo que se protege acá no son formularios, sino tres invariantes que, si se
rompen, dejan al histórico sin poder responder «quién hizo esto»:

* un usuario no se borra ni se recicla su email;
* el administrador no conoce la contraseña de nadie;
* el sistema no puede quedarse sin administradores.
"""

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import EstadoUsuario, RegistroAuditoria, ClaveRol, Usuario
from app.services import auditoria, usuarios

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
        ("Diego Admin", "admin@bahnsa.com.ar", ClaveRol.ADMINISTRADOR),
        ("Ana Técnica", "tecnica@bahnsa.com.ar", ClaveRol.TECNICO),
        ("Bruno Comercial", "comercial@bahnsa.com.ar", ClaveRol.COMERCIAL),
    ]:
        usuario = Usuario(nombre=nombre, email=email, rol=rol_de(rol), estado=EstadoUsuario.ACTIVO)
        usuario.definir_password(PASSWORD)
        db.session.add(usuario)
    db.session.commit()


def _usuario(email="admin@bahnsa.com.ar"):
    return db.session.scalar(db.select(Usuario).filter_by(email=email))


def _ingresar(app, email="admin@bahnsa.com.ar"):
    cliente = app.test_client()
    cliente.post("/acceso/", data={"email": email, "password": PASSWORD})
    return cliente


# ---------------------------------------------------------------- altas y bajas


def test_el_alta_devuelve_una_clave_de_un_solo_uso(app):
    """El administrador no elige la contraseña de otro.

    Si la eligiera, podría entrar como esa persona, y toda la bitácora dejaría de
    probar quién hizo cada cosa.
    """
    alta = usuarios.crear("Carla Nueva", "carla@bahnsa.com.ar", "tecnico", _usuario())

    assert len(alta.clave_inicial) == usuarios.LARGO_CLAVE_INICIAL
    assert alta.usuario.debe_cambiar_password is True
    assert alta.usuario.password_es_correcta(alta.clave_inicial)


def test_la_clave_inicial_no_repite_caracteres_confundibles(app):
    """Se dicta por teléfono: una O y un 0 en el mismo alfabeto garantizan errores."""
    for _ in range(50):
        assert not set(usuarios.clave_inicial()) & set("O0lI1")


def test_dos_altas_no_generan_la_misma_clave(app):
    claves = {usuarios.clave_inicial() for _ in range(200)}
    assert len(claves) == 200


@pytest.mark.parametrize(
    "nombre, email, rol",
    [
        ("", "a@b.com", "tecnico"),
        ("Sin arroba", "no-es-un-email", "tecnico"),
        ("Rol raro", "a@b.com", "presidente"),
    ],
)
def test_el_alta_rechaza_datos_invalidos(app, nombre, email, rol):
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.crear(nombre, email, rol, _usuario())


def test_el_email_no_se_reutiliza_ni_despues_de_la_baja(app):
    """Si entrara otra persona con el mismo email, el histórico mentiría."""
    alta = usuarios.crear("Carla Nueva", "carla@bahnsa.com.ar", "tecnico", _usuario())
    usuarios.cambiar_estado(alta.usuario, EstadoUsuario.BAJA, _usuario())

    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.crear("Otra Persona", "carla@bahnsa.com.ar", "comercial", _usuario())


def test_el_email_duplicado_se_detecta_sin_importar_mayusculas(app):
    usuarios.crear("Carla Nueva", "carla@bahnsa.com.ar", "tecnico", _usuario())
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.crear("Otra", "CARLA@BAHNSA.COM.AR", "comercial", _usuario())


def test_dar_de_baja_no_borra_al_usuario(app):
    """Un usuario que aprobó ensayos en 2024 sigue explicando esas aprobaciones."""
    tecnica = _usuario("tecnica@bahnsa.com.ar")
    identificador = tecnica.id

    usuarios.cambiar_estado(tecnica, EstadoUsuario.BAJA, _usuario())

    assert db.session.get(Usuario, identificador) is not None
    assert tecnica.estado is EstadoUsuario.BAJA


def test_suspender_y_dar_de_baja_son_estados_distintos(app):
    """Tratarlos igual obligaría a adivinar después por qué se cerró una cuenta."""
    tecnica = _usuario("tecnica@bahnsa.com.ar")

    usuarios.cambiar_estado(tecnica, EstadoUsuario.SUSPENDIDO, _usuario())
    assert tecnica.estado is EstadoUsuario.SUSPENDIDO

    usuarios.cambiar_estado(tecnica, EstadoUsuario.ACTIVO, _usuario())
    assert tecnica.estado is EstadoUsuario.ACTIVO


# ------------------------------------------------- no quedarse sin administrador


def test_no_se_puede_dar_de_baja_al_unico_administrador(app):
    """Sin esto se sale tocando la base a mano. Se comete una vez y cuesta una tarde."""
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.cambiar_estado(_usuario(), EstadoUsuario.BAJA, _usuario())


def test_no_se_puede_quitarle_el_rol_al_unico_administrador(app):
    admin = _usuario()
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.editar(admin, admin.nombre, admin.email, "comercial", admin)


def test_con_dos_administradores_si_se_puede_degradar_a_uno(app):
    otro = usuarios.crear("Segunda Admin", "admin2@bahnsa.com.ar", "administrador", _usuario())
    admin = _usuario()

    usuarios.editar(admin, admin.nombre, admin.email, "tecnico", otro.usuario)

    assert admin.rol.clave == ClaveRol.TECNICO


def test_un_administrador_suspendido_no_cuenta_como_activo(app):
    """El que ya no puede entrar no sirve para destrabar el sistema."""
    otro = usuarios.crear("Segunda Admin", "admin2@bahnsa.com.ar", "administrador", _usuario())
    usuarios.cambiar_estado(otro.usuario, EstadoUsuario.SUSPENDIDO, _usuario())

    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.cambiar_estado(_usuario(), EstadoUsuario.BAJA, _usuario())


# ------------------------------------------------- contraseñas y desbloqueo


def test_restablecer_genera_clave_nueva_y_obliga_a_cambiarla(app):
    tecnica = _usuario("tecnica@bahnsa.com.ar")
    clave = usuarios.restablecer_password(tecnica, _usuario())

    assert tecnica.password_es_correcta(clave)
    assert not tecnica.password_es_correcta(PASSWORD)
    assert tecnica.debe_cambiar_password is True


def test_desbloquear_no_toca_la_contrasena(app):
    """Quien se equivocó cinco veces y ya recordó su clave no tiene que elegir otra."""
    tecnica = _usuario("tecnica@bahnsa.com.ar")
    tecnica.intentos_fallidos = 5
    db.session.commit()

    usuarios.desbloquear(tecnica, _usuario())

    assert tecnica.intentos_fallidos == 0
    assert tecnica.bloqueado_hasta is None
    assert tecnica.password_es_correcta(PASSWORD)


def test_suspender_limpia_el_bloqueo_por_intentos(app):
    tecnica = _usuario("tecnica@bahnsa.com.ar")
    tecnica.intentos_fallidos = 5
    db.session.commit()

    usuarios.cambiar_estado(tecnica, EstadoUsuario.SUSPENDIDO, _usuario())
    assert tecnica.intentos_fallidos == 0


# ------------------------------------------------------------------ auditoría


@pytest.mark.parametrize(
    "accion, operacion",
    [
        ("usuario_creado", lambda: usuarios.crear("X", "x@b.com", "tecnico", _usuario())),
        ("usuario_estado", lambda: usuarios.cambiar_estado(
            _usuario("tecnica@bahnsa.com.ar"), EstadoUsuario.SUSPENDIDO, _usuario())),
        ("password_restablecida", lambda: usuarios.restablecer_password(
            _usuario("tecnica@bahnsa.com.ar"), _usuario())),
        ("usuario_desbloqueado", lambda: usuarios.desbloquear(
            _usuario("tecnica@bahnsa.com.ar"), _usuario())),
    ],
)
def test_toda_accion_sobre_una_cuenta_queda_registrada(app, accion, operacion):
    operacion()
    registro = db.session.scalar(db.select(RegistroAuditoria).filter_by(accion=accion))
    assert registro is not None
    assert registro.usuario_id == _usuario().id


def test_el_registro_se_escribe_en_la_misma_transaccion_que_la_accion(app):
    """Un registro de algo que después se deshizo volvería inservible a la bitácora."""
    from app.unidad_de_trabajo import transaccion

    antes = db.session.scalar(db.select(db.func.count(RegistroAuditoria.id)))

    with pytest.raises(usuarios.DatosInvalidos):
        with transaccion():
            usuarios.crear("Primera", "primera@bahnsa.com.ar", "tecnico", _usuario())
            usuarios.crear("Repetida", "primera@bahnsa.com.ar", "tecnico", _usuario())

    assert db.session.scalar(db.select(db.func.count(RegistroAuditoria.id))) == antes
    assert db.session.scalar(db.select(Usuario).filter_by(email="primera@bahnsa.com.ar")) is None


def test_la_bitacora_filtra_por_accion_usuario_y_fallidos(app):
    usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    cliente = app.test_client()
    cliente.post("/acceso/", data={"email": "admin@bahnsa.com.ar", "password": "mal"})

    todas = auditoria.buscar(auditoria.Filtros()).total
    altas = auditoria.buscar(auditoria.Filtros(accion="usuario_creado")).total
    fallidas = auditoria.buscar(auditoria.Filtros(solo_fallidos=True)).total

    assert todas > altas >= 1
    assert fallidas >= 1
    # Un ingreso fallido no tiene usuario asociado, pero sí el email que se intentó.
    fallida = auditoria.buscar(auditoria.Filtros(solo_fallidos=True)).items[0]
    assert fallida.email_intentado


def test_la_bitacora_ordena_de_lo_mas_nuevo_a_lo_mas_viejo(app):
    usuarios.crear("Primera", "p1@bahnsa.com.ar", "tecnico", _usuario())
    usuarios.crear("Segunda", "p2@bahnsa.com.ar", "tecnico", _usuario())

    items = auditoria.buscar(auditoria.Filtros(accion="usuario_creado")).items
    assert [r.datos["email"] for r in items] == ["p2@bahnsa.com.ar", "p1@bahnsa.com.ar"]


def test_el_filtro_hasta_incluye_el_dia_indicado(app):
    """Quien filtra «hasta el 20» espera ver lo del 20, no lo anterior a su medianoche."""
    from datetime import date

    usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    hoy = date.today()

    assert auditoria.buscar(auditoria.Filtros(hasta=hoy)).total >= 1


def test_la_historia_de_una_entidad_responde_quien_la_toco(app):
    alta = usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    usuarios.cambiar_estado(alta.usuario, EstadoUsuario.SUSPENDIDO, _usuario())

    historia = auditoria.historia_de("usuario", alta.usuario.id)

    assert len(historia) == 2
    assert {r.accion for r in historia} == {"usuario_creado", "usuario_estado"}


def test_las_acciones_del_desplegable_salen_de_los_datos(app):
    """Si alguien agrega una acción y olvida traducirla, igual se puede filtrar por ella."""
    usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    acciones = dict(auditoria.acciones_registradas())

    assert "usuario_creado" in acciones
    assert acciones["usuario_creado"] == "Alta de usuario"
    assert auditoria.etiqueta("accion_sin_traducir") == "Accion sin traducir"


# --------------------------------------------------------------------- vistas


def test_solo_el_administrador_entra_a_las_pantallas_de_administracion(app):
    for email, esperado in [
        ("admin@bahnsa.com.ar", 200),
        ("tecnica@bahnsa.com.ar", 403),
        ("comercial@bahnsa.com.ar", 403),
    ]:
        cliente = _ingresar(app, email)
        assert cliente.get("/administracion/usuarios").status_code == esperado
        assert cliente.get("/administracion/auditoria").status_code == esperado


def test_el_alta_por_pantalla_muestra_la_clave_una_sola_vez(app):
    cliente = _ingresar(app)
    respuesta = cliente.post(
        "/administracion/usuarios/nuevo",
        data={"nombre": "Carla Nueva", "email": "carla@bahnsa.com.ar", "rol": "tecnico"},
    )
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Contraseña inicial" in cuerpo
    creado = db.session.scalar(db.select(Usuario).filter_by(email="carla@bahnsa.com.ar"))
    assert creado is not None and creado.debe_cambiar_password


def test_un_administrador_no_puede_darse_de_baja_a_si_mismo(app):
    """Quedaría afuera del sistema en el acto y sin nadie del otro lado."""
    cliente = _ingresar(app)
    admin = _usuario()

    cliente.post(
        f"/administracion/usuarios/{admin.id}/estado",
        data={"estado": "baja"},
        follow_redirects=True,
    )

    assert _usuario().estado is EstadoUsuario.ACTIVO


def test_las_acciones_sobre_cuentas_exigen_token_csrf(app):
    """Sin esto, un enlace externo podría dar de baja a alguien con la sesión abierta."""
    aplicacion = app
    aplicacion.config["WTF_CSRF_ENABLED"] = True
    try:
        cliente = app.test_client()
        import re

        cuerpo = cliente.get("/acceso/").get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', cuerpo).group(1)
        cliente.post(
            "/acceso/",
            data={"email": "admin@bahnsa.com.ar", "password": PASSWORD, "csrf_token": token},
        )

        tecnica = _usuario("tecnica@bahnsa.com.ar")
        respuesta = cliente.post(
            f"/administracion/usuarios/{tecnica.id}/estado", data={"estado": "baja"}
        )

        assert respuesta.status_code == 400
        assert _usuario("tecnica@bahnsa.com.ar").estado is EstadoUsuario.ACTIVO
    finally:
        aplicacion.config["WTF_CSRF_ENABLED"] = False


def test_la_bitacora_pagina(app):
    for indice in range(3):
        usuarios.crear(f"Persona {indice}", f"p{indice}@bahnsa.com.ar", "tecnico", _usuario())

    pagina = auditoria.buscar(auditoria.Filtros(), pagina=1, por_pagina=2)
    assert len(pagina.items) == 2
    assert pagina.hay_siguiente
    assert not pagina.hay_anterior


def test_la_ficha_de_usuario_muestra_su_actividad(app):
    alta = usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    cliente = _ingresar(app)

    respuesta = cliente.get(f"/administracion/usuarios/{alta.usuario.id}")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    assert "Alta de usuario" in cuerpo


def test_las_etiquetas_traducen_acciones_que_el_sistema_realmente_emite(app):
    """La tabla de etiquetas se escribió una vez de memoria y quedó desalineada.

    Cinco acciones no tenían traducción y otras cinco traducían nombres que el
    código nunca emite. Este test lee las llamadas reales a `registrar()` del código
    fuente y las cruza contra la tabla, para que la próxima vez salte solo.
    """
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "app"
    emitidas = set()
    for archivo in raiz.rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        # registrar("accion", ...) y también f"solicitud_{accion}"
        emitidas.update(re.findall(r'registrar\(\s*\n?\s*"([a-z_]+)"', texto))

    assert emitidas, "no se encontró ninguna llamada a registrar()"
    sin_traducir = sorted(emitidas - set(auditoria.ETIQUETAS))
    assert not sin_traducir, f"acciones sin etiqueta: {sin_traducir}"


def test_la_bitacora_muestra_el_nombre_de_quien_actuo(app):
    """Una bitácora que dice «#5» en vez de un nombre hay que traducirla a mano."""
    usuarios.crear("Carla", "carla@bahnsa.com.ar", "tecnico", _usuario())
    registro = auditoria.buscar(auditoria.Filtros(accion="usuario_creado")).items[0]

    assert registro.usuario is not None
    assert registro.usuario.nombre == "Diego Admin"
