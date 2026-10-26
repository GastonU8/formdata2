"""Fábrica de aplicación de FormData."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, render_template

from .config import obtener_config
from .extensions import configurar_hasher, csrf, db, limiter, login_manager, migrate

__version__ = "0.1.0"


def crear_app(entorno: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(obtener_config(entorno))

    Path(app.config["CARPETA_IMPORTACIONES"]).mkdir(parents=True, exist_ok=True)
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///") and ":memory:" not in app.config["SQLALCHEMY_DATABASE_URI"]:
        Path(app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        # El hilo de importación usa una conexión distinta a la del request.
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            **app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}),
            "connect_args": {"check_same_thread": False},
        }

    _registrar_extensiones(app)
    _registrar_blueprints(app)
    _registrar_errores(app)
    _registrar_plantillas(app)
    _registrar_cli(app)

    from .demo import registrar_cli_demo
    registrar_cli_demo(app)

    logging.basicConfig(
        level=logging.DEBUG if app.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return app


def _registrar_extensiones(app: Flask) -> None:
    from . import models  # noqa: F401  (necesario para que Alembic vea las tablas)
    from .security import instalar_cabeceras

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    configurar_hasher(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    instalar_cabeceras(app)

    @login_manager.user_loader
    def cargar_usuario(identificador: str):
        return db.session.get(models.Usuario, int(identificador))


def _registrar_blueprints(app: Flask) -> None:
    from .blueprints.admin import bp as admin_bp
    from .blueprints.asistente import bp as asistente_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.importacion import bp as importacion_bp
    from .blueprints.normalizacion import bp as normalizacion_bp
    from .blueprints.panel import bp as panel_bp
    from .blueprints.productos import bp as productos_bp
    from .blueprints.solicitudes import bp as solicitudes_bp
    from .blueprints.tratamientos import bp as tratamientos_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(panel_bp)
    app.register_blueprint(importacion_bp)
    app.register_blueprint(normalizacion_bp)
    app.register_blueprint(asistente_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(solicitudes_bp)
    app.register_blueprint(tratamientos_bp)
    app.register_blueprint(productos_bp)


def _registrar_errores(app: Flask) -> None:
    def pagina(codigo: int, titulo: str, detalle: str):
        def manejador(_error):
            return render_template(
                "errores/error.html", codigo=codigo, titulo=titulo, detalle=detalle
            ), codigo

        return manejador

    app.register_error_handler(
        401, pagina(401, "Necesitás iniciar sesión", "Tu sesión venció o todavía no ingresaste.")
    )
    app.register_error_handler(
        403,
        pagina(
            403,
            "Tu rol no llega hasta acá",
            "Pedile a un administrador que amplíe tus permisos si necesitás esta pantalla.",
        ),
    )
    app.register_error_handler(
        404, pagina(404, "No encontramos esa pantalla", "Revisá el enlace o volvé al tablero.")
    )
    app.register_error_handler(
        413,
        pagina(
            413,
            "El archivo es demasiado grande",
            "El límite de carga es 25 MB. Dividí el Excel y volvé a intentar.",
        ),
    )
    app.register_error_handler(
        429,
        pagina(
            429,
            "Demasiados intentos",
            "Esperá unos minutos antes de volver a probar.",
        ),
    )

    @app.errorhandler(500)
    def error_interno(error):
        db.session.rollback()
        app.logger.exception("Error no controlado", exc_info=error)
        return render_template(
            "errores/error.html",
            codigo=500,
            titulo="Algo se rompió de nuestro lado",
            detalle="Quedó registrado. Probá de nuevo en un momento.",
        ), 500


def _registrar_plantillas(app: Flask) -> None:
    from .security import puede

    def pendientes_sin_mapear() -> int:
        """Cuántos nombres esperan en la bandeja de normalización.

        Se expone como función y no como valor para que la consulta se haga solo
        si la plantilla la usa: la barra lateral la llama únicamente dentro del
        bloque visible para el rol técnico, así el resto de los usuarios no paga
        un COUNT por página. Si la base falla, la barra muestra el enlace sin
        número en lugar de tirar abajo la pantalla (incluida la de error).
        """
        from .services import mapeo

        try:
            return mapeo.contar_pendientes()
        except Exception:  # noqa: BLE001 — un contador decorativo no rompe una vista
            return 0

    @app.context_processor
    def variables_globales():
        from .services import graficos

        return {
            "puede": puede,
            "pendientes_sin_mapear": pendientes_sin_mapear,
            # Los gráficos se dibujan en el servidor. Se exponen como diccionario para
            # que la plantilla elija el tipo por nombre y no haya un `if` por formato.
            "grafico": {
                "barras": graficos.barras,
                "columnas": graficos.columnas,
                "apilada": graficos.apilada,
            },
            "nombre_sistema": app.config["NOMBRE_SISTEMA"],
            "descripcion_sistema": app.config["DESCRIPCION_SISTEMA"],
            "version": __version__,
        }

    @app.template_filter("numero")
    def formato_numero(valor):
        if valor is None:
            return "—"
        return f"{valor:,}".replace(",", ".")

    @app.template_filter("fecha")
    def formato_fecha(valor):
        return valor.strftime("%d/%m/%Y") if valor else "—"

    @app.template_filter("fecha_hora")
    def formato_fecha_hora(valor):
        return valor.strftime("%d/%m/%Y %H:%M") if valor else "—"


def _registrar_cli(app: Flask) -> None:
    import click
    from email_validator import EmailNotValidError, validate_email

    from .models import EstadoUsuario, Usuario

    @app.cli.command("iniciar-bd")
    def iniciar_bd():
        """Crea las tablas y siembra los roles de fábrica."""
        from .services import roles as servicio_roles

        db.create_all()
        creados = servicio_roles.sincronizar_roles()
        click.secho(f"Tablas creadas. Roles sembrados: {creados}.", fg="green")

    @app.cli.command("sincronizar-roles")
    def sincronizar_roles_cli():
        """Crea los roles de fábrica que falten, sin tocar los que ya existen."""
        from .services import roles as servicio_roles

        creados = servicio_roles.sincronizar_roles()
        click.secho(f"Roles creados: {creados}.", fg="green")

    @app.cli.command("crear-usuario")
    @click.option("--nombre", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--rol", default="administrador", help="Clave del rol, por ejemplo: administrador")
    @click.password_option("--password", confirmation_prompt=True)
    def crear_usuario(nombre, email, rol, password):
        """Da de alta un usuario desde la consola."""
        largo = app.config["LARGO_MINIMO_PASSWORD"]
        if len(password) < largo:
            raise click.ClickException(f"La contraseña debe tener al menos {largo} caracteres.")

        try:
            # Misma validación que el formulario de login: si no, el CLI crea
            # cuentas con las que después no se puede ingresar.
            email = validate_email(email.strip(), check_deliverability=False).normalized.lower()
        except EmailNotValidError as error:
            raise click.ClickException(f"Email inválido: {error}")

        if db.session.scalar(db.select(Usuario).filter_by(email=email)):
            raise click.ClickException("Ya existe un usuario con ese email.")

        from .services import roles as servicio_roles

        servicio_roles.sincronizar_roles()
        rol_elegido = servicio_roles.obtener(rol)
        if rol_elegido is None:
            disponibles = ", ".join(fila.rol.clave for fila in servicio_roles.listar())
            raise click.ClickException(f"No existe el rol «{rol}». Hay: {disponibles}.")

        usuario = Usuario(
            nombre=nombre,
            email=email,
            rol=rol_elegido,
            estado=EstadoUsuario.ACTIVO,
        )
        usuario.definir_password(password)
        db.session.add(usuario)
        db.session.commit()
        click.secho(f"Usuario {email} creado con rol {rol}.", fg="green")


# Alias en inglés: el CLI de Flask busca automáticamente una fábrica llamada
# `create_app` o `make_app`. Con esto, `flask <comando>` funciona aunque no haya
# ninguna variable de entorno configurada.
create_app = crear_app
