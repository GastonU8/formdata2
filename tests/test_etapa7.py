"""Pruebas de la etapa 7: roles y permisos editables.

Sacar los permisos del código y ponerlos en la base compra flexibilidad y paga con
un riesgo nuevo: **que un cambio de configuración deje al sistema sin forma de
volver atrás**. Casi todo lo que se prueba acá son las salvaguardas contra eso.

Lo otro que se protege es la propiedad que hace útil a la etapa: que un rol creado
después funcione en todo el sistema sin tocar código.
"""

import pytest
from conftest import aislar_sesion_entre_clientes, rol_de

from app import crear_app
from app.extensions import db
from app.models import ClaveRol, EstadoUsuario, PermisoRol, RegistroAuditoria, Rol, Usuario
from app.security import puede
from app.security.roles import ASIGNACION_DE_FABRICA, CAPACIDADES, GRUPOS
from app.services import roles, usuarios

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
    for nombre, email, clave in [
        ("Diego Admin", "admin@bahnsa.com.ar", ClaveRol.ADMINISTRADOR),
        ("Ana Técnica", "tecnica@bahnsa.com.ar", ClaveRol.TECNICO),
        ("Bruno Comercial", "comercial@bahnsa.com.ar", ClaveRol.COMERCIAL),
    ]:
        usuario = Usuario(
            nombre=nombre, email=email, rol=rol_de(clave), estado=EstadoUsuario.ACTIVO
        )
        usuario.definir_password(PASSWORD)
        db.session.add(usuario)
    db.session.commit()


def _usuario(email="admin@bahnsa.com.ar"):
    return db.session.scalar(db.select(Usuario).filter_by(email=email))


def _ingresar(app, email="admin@bahnsa.com.ar"):
    cliente = app.test_client()
    cliente.post("/acceso/", data={"email": email, "password": PASSWORD})
    return cliente


# --------------------------------------------------------------------- siembra


def test_la_siembra_crea_los_cinco_roles_de_fabrica(app):
    claves = {fila.rol.clave for fila in roles.listar()}
    assert claves == {str(c) for c in ASIGNACION_DE_FABRICA}
    assert all(fila.rol.es_del_sistema for fila in roles.listar())


def test_sembrar_dos_veces_no_deshace_los_cambios_hechos(app):
    """Si la siembra reasignara lo de fábrica, borraría en silencio cada permiso
    que un administrador hubiera quitado a propósito."""
    tecnico = roles.obtener(ClaveRol.TECNICO)
    roles.editar(tecnico, tecnico.nombre, "", ["consultar_tratamientos"], _usuario())

    assert roles.sincronizar_roles() == 0
    assert roles.obtener(ClaveRol.TECNICO).capacidades == {"consultar_tratamientos"}


def test_todas_las_capacidades_del_catalogo_estan_agrupadas(app):
    """Una capacidad sin grupo no aparecería en la pantalla y sería inasignable."""
    agrupadas = {clave for claves in GRUPOS.values() for clave in claves}
    assert agrupadas == set(CAPACIDADES)


def test_las_capacidades_de_fabrica_existen_en_el_catalogo(app):
    for clave, capacidades in ASIGNACION_DE_FABRICA.items():
        sobrantes = set(capacidades) - set(CAPACIDADES)
        assert not sobrantes, f"{clave} tiene capacidades inexistentes: {sobrantes}"


# ------------------------------------------------- un rol nuevo funciona entero


def test_un_rol_creado_despues_funciona_sin_tocar_codigo(app):
    """La propiedad que hace útil a toda la etapa."""
    auditor = roles.crear(
        "Auditor de calidad",
        "Solo lectura y bitácora",
        ["consultar_tratamientos", "ver_indicadores", "ver_auditoria"],
        _usuario(),
    )
    alta = usuarios.crear("Vera Auditora", "vera@bahnsa.com.ar", auditor.clave, _usuario())

    assert puede("ver_auditoria", alta.usuario)
    assert puede("consultar_tratamientos", alta.usuario)
    assert not puede("importar_excel", alta.usuario)
    assert not puede("gestionar_usuarios", alta.usuario)


def test_el_rol_nuevo_abre_las_pantallas_que_le_corresponden(app):
    """De punta a punta: no alcanza con que `puede()` diga que sí."""
    auditor = roles.crear(
        "Auditor", "", ["consultar_tratamientos", "ver_auditoria"], _usuario()
    )
    alta = usuarios.crear("Vera", "vera@bahnsa.com.ar", auditor.clave, _usuario())
    alta.usuario.definir_password(PASSWORD)
    alta.usuario.debe_cambiar_password = False
    db.session.commit()

    cliente = _ingresar(app, "vera@bahnsa.com.ar")

    assert cliente.get("/administracion/auditoria").status_code == 200
    assert cliente.get("/tratamientos/").status_code == 200
    assert cliente.get("/administracion/usuarios").status_code == 403
    assert cliente.get("/importacion/").status_code in (403, 404)


def test_la_clave_del_rol_se_deriva_del_nombre(app):
    rol = roles.crear("Auditor de Calidad Externa", "", [], _usuario())
    assert rol.clave == "auditor-de-calidad-externa"


def test_no_se_crean_dos_roles_que_normalizan_igual(app):
    roles.crear("Auditor de calidad", "", [], _usuario())
    with pytest.raises(roles.DatosInvalidos):
        roles.crear("AUDITOR  DE  CALIDAD", "", [], _usuario())


def test_una_capacidad_inventada_se_descarta(app):
    """El formulario lo manda cualquiera: no es entrada confiable."""
    rol = roles.crear(
        "Curioso", "", ["consultar_tratamientos", "borrar_la_base", "ser_root"], _usuario()
    )
    assert rol.capacidades == {"consultar_tratamientos"}


# ------------------------------------------------------------- salvaguardas


def test_no_se_puede_dejar_al_sistema_sin_quien_lo_administre(app):
    """La salvaguarda central: sin esto se sale tocando la base a mano."""
    administrador = roles.obtener(ClaveRol.ADMINISTRADOR)

    with pytest.raises(roles.DatosInvalidos) as error:
        roles.editar(administrador, "Administrador", "", ["consultar_tratamientos"], _usuario())

    assert "único rol con usuarios activos" in str(error.value)
    # Y no quedó a medias: las capacidades siguen intactas.
    assert "gestionar_usuarios" in roles.obtener(ClaveRol.ADMINISTRADOR).capacidades


def test_con_otro_rol_que_administre_si_se_puede_quitar(app):
    """La regla no es «no toques al administrador», es «que quede alguien»."""
    coordinacion = roles.crear(
        "Coordinación", "", ["gestionar_usuarios", "gestionar_roles"], _usuario()
    )
    usuarios.crear("Cora Coord", "cora@bahnsa.com.ar", coordinacion.clave, _usuario())

    administrador = roles.obtener(ClaveRol.ADMINISTRADOR)
    roles.editar(administrador, "Administrador", "", ["consultar_tratamientos"], _usuario())

    assert roles.obtener(ClaveRol.ADMINISTRADOR).capacidades == {"consultar_tratamientos"}


def test_un_rol_con_el_permiso_pero_sin_usuarios_no_destraba_nada(app):
    """Nadie puede entrar con un rol que no tiene a nadie asignado."""
    roles.crear("Coordinación", "", ["gestionar_usuarios", "gestionar_roles"], _usuario())

    administrador = roles.obtener(ClaveRol.ADMINISTRADOR)
    with pytest.raises(roles.DatosInvalidos):
        roles.editar(administrador, "Administrador", "", ["consultar_tratamientos"], _usuario())


def test_un_rol_con_usuarios_solo_suspendidos_tampoco_destraba(app):
    coordinacion = roles.crear(
        "Coordinación", "", ["gestionar_usuarios", "gestionar_roles"], _usuario()
    )
    alta = usuarios.crear("Cora", "cora@bahnsa.com.ar", coordinacion.clave, _usuario())
    usuarios.cambiar_estado(alta.usuario, EstadoUsuario.SUSPENDIDO, _usuario())

    administrador = roles.obtener(ClaveRol.ADMINISTRADOR)
    with pytest.raises(roles.DatosInvalidos):
        roles.editar(administrador, "Administrador", "", ["consultar_tratamientos"], _usuario())


def test_un_rol_del_sistema_no_se_borra(app):
    with pytest.raises(roles.DatosInvalidos):
        roles.eliminar(roles.obtener(ClaveRol.GERENCIA), _usuario())


def test_un_rol_con_usuarios_no_se_borra(app):
    auditor = roles.crear("Auditor", "", ["consultar_tratamientos"], _usuario())
    usuarios.crear("Vera", "vera@bahnsa.com.ar", auditor.clave, _usuario())

    with pytest.raises(roles.DatosInvalidos) as error:
        roles.eliminar(auditor, _usuario())
    assert "usuario(s) con este rol" in str(error.value)


def test_un_rol_vacio_y_sin_usuarios_si_se_borra(app):
    auditor = roles.crear("Auditor", "", ["consultar_tratamientos"], _usuario())
    identificador = auditor.id

    roles.eliminar(auditor, _usuario())

    assert db.session.get(Rol, identificador) is None
    # Y sus permisos se fueron con él, sin dejar filas huérfanas.
    assert db.session.scalar(
        db.select(db.func.count(PermisoRol.id)).where(PermisoRol.rol_id == identificador)
    ) == 0


def test_la_clave_de_un_rol_nunca_cambia(app):
    """Es lo que quedó escrito en la auditoría: si cambiara, los registros viejos
    apuntarían a un rol que ya no existe con ese nombre."""
    auditor = roles.crear("Auditor", "", [], _usuario())
    roles.editar(auditor, "Auditoría externa", "", [], _usuario())

    assert auditor.nombre == "Auditoría externa"
    assert auditor.clave == "auditor"


def test_no_se_puede_dar_de_baja_al_unico_usuario_que_administra(app):
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.cambiar_estado(_usuario(), EstadoUsuario.BAJA, _usuario())


def test_no_se_puede_mover_de_rol_al_unico_que_administra(app):
    admin = _usuario()
    with pytest.raises(usuarios.DatosInvalidos):
        usuarios.editar(admin, admin.nombre, admin.email, str(ClaveRol.COMERCIAL), admin)


def test_la_regla_mira_capacidades_y_no_el_nombre_del_rol(app):
    """Una instalación sin ningún «administrador» pero con un rol propio que
    administra es perfectamente sana, y el sistema tiene que aceptarla."""
    coordinacion = roles.crear(
        "Coordinación", "", list(CAPACIDADES), _usuario()
    )
    usuarios.crear("Cora", "cora@bahnsa.com.ar", coordinacion.clave, _usuario())

    admin = _usuario()
    usuarios.cambiar_estado(admin, EstadoUsuario.BAJA, admin)

    assert _usuario().estado is EstadoUsuario.BAJA


# ------------------------------------------------------------------ auditoría


@pytest.mark.parametrize(
    "accion, operacion",
    [
        ("rol_creado", lambda: roles.crear("Auditor", "", ["consultar_tratamientos"], _usuario())),
        ("rol_permisos", lambda: roles.editar(
            roles.obtener(ClaveRol.GERENCIA), "Gerencia", "", ["consultar_tratamientos"], _usuario())),
    ],
)
def test_los_cambios_de_permisos_quedan_auditados(app, accion, operacion):
    """Un permiso que se cambia desde una pantalla es un objetivo: sin registro, un
    cambio silencioso es indetectable."""
    operacion()
    registro = db.session.scalar(db.select(RegistroAuditoria).filter_by(accion=accion))
    assert registro is not None
    assert registro.usuario_id == _usuario().id


def test_la_auditoria_guarda_el_antes_y_el_despues(app):
    """Solo con el después no se puede reconstruir qué se concedió y qué se quitó."""
    gerencia = roles.obtener(ClaveRol.GERENCIA)
    roles.editar(
        gerencia, "Gerencia", "", ["consultar_tratamientos", "administrar_maestro"], _usuario()
    )

    registro = db.session.scalar(db.select(RegistroAuditoria).filter_by(accion="rol_permisos"))
    assert "administrar_maestro" in registro.datos["agregadas"]
    assert "ver_indicadores" in registro.datos["quitadas"]


# --------------------------------------------------------------------- vistas


def test_la_pantalla_de_roles_muestra_la_matriz_completa(app):
    """Compensa lo que se perdió al sacar los permisos del código."""
    cliente = _ingresar(app)
    respuesta = cliente.get("/administracion/roles")
    cuerpo = respuesta.get_data(as_text=True)

    assert respuesta.status_code == 200
    for capacidad in CAPACIDADES:
        assert capacidad in cuerpo
    for fila in roles.listar():
        assert fila.rol.nombre in cuerpo


def test_solo_quien_gestiona_roles_entra(app):
    for email, esperado in [
        ("admin@bahnsa.com.ar", 200),
        ("tecnica@bahnsa.com.ar", 403),
        ("comercial@bahnsa.com.ar", 403),
    ]:
        assert _ingresar(app, email).get("/administracion/roles").status_code == esperado


def test_crear_un_rol_por_pantalla(app):
    cliente = _ingresar(app)
    respuesta = cliente.post(
        "/administracion/roles/nuevo",
        data={
            "nombre": "Auditor de calidad",
            "descripcion": "Solo lectura",
            "capacidades": ["consultar_tratamientos", "ver_auditoria"],
        },
        follow_redirects=True,
    )

    assert respuesta.status_code == 200
    creado = roles.obtener("auditor-de-calidad")
    assert creado is not None
    assert creado.capacidades == {"consultar_tratamientos", "ver_auditoria"}
    assert not creado.es_del_sistema


def test_una_casilla_desmarcada_quita_el_permiso(app):
    """En un formulario de casillas, la ausencia **es** la orden de quitar."""
    cliente = _ingresar(app)
    gerencia = roles.obtener(ClaveRol.GERENCIA)

    cliente.post(
        f"/administracion/roles/{gerencia.id}",
        data={"nombre": "Gerencia", "capacidades": ["consultar_tratamientos"]},
        follow_redirects=True,
    )

    assert roles.obtener(ClaveRol.GERENCIA).capacidades == {"consultar_tratamientos"}


def test_la_pantalla_rechaza_el_cambio_que_deja_sin_administracion(app):
    cliente = _ingresar(app)
    administrador = roles.obtener(ClaveRol.ADMINISTRADOR)

    respuesta = cliente.post(
        f"/administracion/roles/{administrador.id}",
        data={"nombre": "Administrador", "capacidades": ["consultar_tratamientos"]},
    )

    assert respuesta.status_code == 400
    assert "gestionar_usuarios" in roles.obtener(ClaveRol.ADMINISTRADOR).capacidades


def test_el_selector_de_usuarios_ofrece_los_roles_nuevos(app):
    roles.crear("Auditor de calidad", "", ["consultar_tratamientos"], _usuario())
    cliente = _ingresar(app)

    cuerpo = cliente.get("/administracion/usuarios/nuevo").get_data(as_text=True)

    assert "auditor-de-calidad" in cuerpo
    assert "Auditor de calidad" in cuerpo


def test_la_barra_lateral_muestra_cada_enlace_segun_su_capacidad(app):
    """Un rol con `ver_auditoria` y sin `gestionar_usuarios` tiene que ver su enlace."""
    auditor = roles.crear("Auditor", "", ["consultar_tratamientos", "ver_auditoria"], _usuario())
    alta = usuarios.crear("Vera", "vera@bahnsa.com.ar", auditor.clave, _usuario())
    alta.usuario.definir_password(PASSWORD)
    alta.usuario.debe_cambiar_password = False
    db.session.commit()

    cuerpo = _ingresar(app, "vera@bahnsa.com.ar").get("/tratamientos/").get_data(as_text=True)

    assert "/administracion/auditoria" in cuerpo
    assert "/administracion/usuarios" not in cuerpo


def test_la_bandeja_se_arma_por_capacidades_y_no_por_rol(app):
    """Con roles editables, preguntar «¿es supervisor?» dejaría afuera a un rol nuevo
    que sí puede aprobar: vería la bandeja vacía y el botón de aprobar disponible."""
    from app.models import EstadoFlujo
    from app.services import flujo

    revisor = roles.crear("Revisor", "", ["ver_bandeja", "aprobar_solicitud"], _usuario())
    alta = usuarios.crear("Rita", "rita@bahnsa.com.ar", revisor.clave, _usuario())

    estados = flujo.bandeja_de(alta.usuario)

    assert EstadoFlujo.PENDIENTE_APROBACION in estados
    assert EstadoFlujo.BORRADOR not in estados
