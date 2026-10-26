"""Datos de demostración para ver el tablero funcionando antes de tener el Excel real.

No es un fixture de test ni parte del sistema: sirve para mostrar el proyecto y para
verificar visualmente cada etapa. Se niega a correr si la base ya tiene tratamientos.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import click
from flask import Flask

from .extensions import db
from .models import (
    ClaveRol,
    ArchivoImportado,
    EstadoFlujo,
    OrigenTratamiento,
    Cliente,
    DetalleMezcla,
    EstadoImportacion,
    EstadoNormalizacion,
    EstadoUsuario,
    Mezcla,
    Producto,
    Resultado,
    Rol,
    Tratamiento,
    Usuario,
)
from .services import flujo, normalizacion, solicitudes

PRODUCTOS = [
    ("Round Up Full", "Glifosato 66,2%", "Herbicida", "SL"),
    ("Atrazina 90 WG", "Atrazina 90%", "Herbicida", "WG"),
    ("Cipermetrina 25", "Cipermetrina 25%", "Insecticida", "EC"),
    ("Opera Max", "Piraclostrobin + Epoxiconazole", "Fungicida", "SC"),
    ("Coadyuvante Silic", "Siliconado no iónico", "Coadyuvante", "SL"),
    ("Metsulfuron 60", "Metsulfuron metil 60%", "Herbicida", "WG"),
]

# Nombres tal como aparecen mal escritos en las planillas: el caso que el sistema resuelve.
CRUDOS_SIN_MAPEAR = ["Coadyubante Silic.", "Round-up FULL x20", "Ciper 25 (s/d)", "Opera-Max"]

CULTIVOS = ["Soja", "Maíz", "Trigo", "Girasol"]

# Códigos tal como los escribe el laboratorio en la planilla real: «OK» y «PPDO»
# (precipitado). No son una clasificación cerrada y el sistema no los reinterpreta.
OBSERVACIONES_LAB = ["OK", "OK", "OK", "PPDO", "PPDO", "SEPARA FASES", "GRUMOS"]
CLIENTES = [
    ("Agro Sur SA", "Venado Tuerto"),
    ("Estancia La Lucía", "Pergamino"),
    ("Cooperativa Rosario", "Cañada de Gómez"),
]


def registrar_cli_demo(app: Flask) -> None:
    @app.cli.command("sembrar-demo")
    @click.option("--tratamientos", default=40, show_default=True)
    def sembrar_demo(tratamientos: int) -> None:
        """Carga clientes, productos y tratamientos de ejemplo."""
        if db.session.scalar(db.select(db.func.count(Tratamiento.id))):
            raise click.ClickException(
                "La base ya tiene tratamientos. Este comando solo corre sobre una base vacía."
            )

        aleatorio = random.Random(2026)

        # Los roles tienen que existir antes que cualquier usuario.
        from .services import roles as servicio_roles

        servicio_roles.sincronizar_roles()

        usuario = db.session.scalar(db.select(Usuario).order_by(Usuario.id))
        if usuario is None:
            usuario = Usuario(
                nombre="Usuario demo",
                email="demo@formdata.com.ar",
                rol=servicio_roles.obtener(ClaveRol.TECNICO),
                estado=EstadoUsuario.ACTIVO,
                debe_cambiar_password=True,
            )
            usuario.definir_password("cambiar-esta-clave-demo")
            db.session.add(usuario)
            db.session.flush()
            click.secho("Usuario demo@formdata.com.ar / cambiar-esta-clave-demo", fg="yellow")

        clientes = []
        for nombre, ubicacion in CLIENTES:
            cliente = Cliente(
                nombre=nombre,
                clave_normalizada=normalizacion.clave(nombre),
                ubicacion=ubicacion,
                clasificacion="Distribuidor",
            )
            db.session.add(cliente)
            clientes.append(cliente)

        productos = []
        for nombre, activo, categoria, formulacion in PRODUCTOS:
            producto = Producto(
                nombre_comercial=nombre,
                clave_normalizada=normalizacion.clave(nombre),
                activo=activo,
                categoria=categoria,
                tipo_formulacion=formulacion,
                marca="Bahnsa",
            )
            db.session.add(producto)
            productos.append(producto)
        db.session.flush()

        archivo = ArchivoImportado(
            nombre_archivo="tratamientos_historico_demo.xlsx",
            hash_sha256="0" * 64,
            tamano_bytes=1_240_000,
            usuario_id=usuario.id,
            estado=EstadoImportacion.FINALIZADO,
            filas_leidas=tratamientos,
            filas_ok=tratamientos,
        )
        db.session.add(archivo)
        db.session.flush()

        base = date.today() - timedelta(days=540)
        for indice in range(1, tratamientos + 1):
            tratamiento = Tratamiento(
                numero_tratamiento=f"TR-{indice:04d}",
                fecha=base + timedelta(days=aleatorio.randint(0, 520)),
                archivo_id=archivo.id,
                cliente_id=aleatorio.choice(clientes).id,
                cultivo=aleatorio.choice(CULTIVOS),
                laboratorio="Laboratorio central",
                tipo_tratamiento=aleatorio.choice(["Compatibilidad", "Eficacia", "Estabilidad"]),
                target=aleatorio.choice(["Malezas de hoja ancha", "Chinche", "Roya"]),
                estado_flujo=EstadoFlujo.FINALIZADA,
                origen=OrigenTratamiento.IMPORTACION,
            )
            db.session.add(tratamiento)
            db.session.flush()

            mezcla = Mezcla(tratamiento_id=tratamiento.id, nombre_mezcla=f"Mezcla {indice}")
            db.session.add(mezcla)
            db.session.flush()

            for orden, producto in enumerate(aleatorio.sample(productos, aleatorio.randint(2, 4)), 1):
                db.session.add(
                    DetalleMezcla(
                        mezcla_id=mezcla.id,
                        orden_producto=orden,
                        nombre_original=producto.nombre_comercial,
                        clave_normalizada=producto.clave_normalizada,
                        producto_id=producto.id,
                        estado_normalizacion=EstadoNormalizacion.AUTOMATICA,
                        dosis=round(aleatorio.uniform(0.2, 3.5), 2),
                        unidad="l/ha",
                    )
                )

            # Uno de cada seis tratamientos trae un nombre que no resolvió.
            if indice % 6 == 0:
                crudo = aleatorio.choice(CRUDOS_SIN_MAPEAR)
                db.session.add(
                    DetalleMezcla(
                        mezcla_id=mezcla.id,
                        orden_producto=9,
                        nombre_original=crudo,
                        clave_normalizada=normalizacion.clave(crudo),
                        estado_normalizacion=EstadoNormalizacion.PENDIENTE,
                        dosis=round(aleatorio.uniform(0.1, 1.0), 2),
                        unidad="l/ha",
                    )
                )

            # Deliberadamente sin `porcentaje_efectividad`: el Excel histórico no
            # trae esa columna, así que ningún tratamiento importado la tiene. Si el
            # demo la llenara, el tablero se vería mejor de lo que se va a ver con el
            # archivo real, que es la peor manera de preparar una demostración.
            observacion = aleatorio.choice(OBSERVACIONES_LAB)
            db.session.add(
                Resultado(
                    tratamiento_id=tratamiento.id,
                    ph_agua=round(aleatorio.uniform(6.2, 8.4), 2),
                    dureza_agua=round(aleatorio.uniform(40, 320), 1),
                    ph_mezcla=round(aleatorio.uniform(4.8, 7.9), 2),
                    observacion_cinco_min=aleatorio.choice(["OK", "OK", "PPDO"]),
                    observacion_diez_min=observacion,
                    suspensibilidad=aleatorio.choice(["Buena", "Regular", "Deficiente"]),
                    granulometria=aleatorio.choice(["Fina", "Media", "Gruesa"]),
                    estado_resultado=observacion,
                )
            )

        db.session.commit()
        click.secho(f"Sembrados {tratamientos} tratamientos históricos.", fg="green")

        _sembrar_circuito(aleatorio, clientes, productos)
        click.secho("Sembradas 5 solicitudes en distintas etapas del circuito.", fg="green")


def _usuario_demo(nombre: str, email: str, clave_rol) -> Usuario:
    """Crea la cuenta si no existe. Contraseña inicial de un solo uso."""
    from .services import roles as servicio_roles

    usuario = db.session.scalar(db.select(Usuario).filter_by(email=email))
    if usuario is None:
        # Los roles se resuelven contra la tabla: desde la etapa 7 se pueden editar
        # y podría haber uno propio de la organización con la misma clave.
        rol = servicio_roles.obtener(clave_rol)
        usuario = Usuario(
            nombre=nombre, email=email, rol=rol,
            estado=EstadoUsuario.ACTIVO, debe_cambiar_password=True,
        )
        usuario.definir_password("cambiar-esta-clave-demo")
        db.session.add(usuario)
        db.session.flush()
        click.secho(f"  {email} / cambiar-esta-clave-demo  ({clave_rol})", fg="yellow")
    return usuario


def _sembrar_circuito(aleatorio, clientes, productos) -> None:
    """Cinco solicitudes, una en cada etapa, para ver el circuito completo."""
    comercial = _usuario_demo("Bruno Vega (comercial)", "comercial@formdata.com.ar", ClaveRol.COMERCIAL)
    supervisora = _usuario_demo("Marcela Ríos (supervisora)", "supervisora@formdata.com.ar", ClaveRol.SUPERVISOR)
    tecnica = _usuario_demo("Ana Duarte (laboratorio)", "laboratorio@formdata.com.ar", ClaveRol.TECNICO)
    # Un administrador, para poder mostrar la gestión de usuarios y la bitácora.
    _usuario_demo("Diego Sosa (administración)", "admin@formdata.com.ar", ClaveRol.ADMINISTRADOR)
    db.session.commit()

    escenarios = [
        ("borrador", []),
        ("pendiente", [("enviar", comercial, None)]),
        ("rechazada", [("enviar", comercial, None),
                       ("rechazar", supervisora, "Falta la dosis del coadyuvante")]),
        ("aprobada", [("enviar", comercial, None), ("aprobar", supervisora, None)]),
        ("en_ensayo", [("enviar", comercial, None), ("aprobar", supervisora, None),
                       ("tomar", tecnica, None)]),
    ]

    for indice, (_, pasos) in enumerate(escenarios, start=1):
        elegidos = aleatorio.sample(productos, 3)
        lineas = [
            solicitudes.LineaProducto(p.nombre_comercial, round(aleatorio.uniform(0.3, 2.5), 2), "l/ha")
            for p in elegidos[:2]
        ]
        # Una línea escrita "a mano", que no resuelve contra el maestro.
        lineas.append(solicitudes.LineaProducto("Coadyubante Silic.", 0.25, "l/ha"))

        solicitud = solicitudes.crear_solicitud(
            autor=comercial,
            cliente_id=aleatorio.choice(clientes).id,
            cultivo=aleatorio.choice(CULTIVOS),
            tipo_tratamiento="Compatibilidad",
            target=aleatorio.choice(["Malezas de hoja ancha", "Chinche", "Roya"]),
            observaciones="El cliente pide confirmar compatibilidad antes de aplicar.",
            fecha=date.today() + timedelta(days=indice * 3),
            lineas=lineas,
        )
        for accion, autor, motivo in pasos:
            flujo.ejecutar(solicitud, accion, autor, motivo=motivo)
