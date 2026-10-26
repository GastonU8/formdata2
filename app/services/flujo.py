"""Máquina de estados del circuito de solicitudes.

Toda transición pasa por acá. Las vistas no cambian `estado_flujo` a mano: llaman a
`ejecutar()`, que valida el estado de origen, el permiso, la propiedad de la
solicitud y el motivo, y recién entonces escribe el cambio junto con su registro
en el historial. Si la regla vive en un solo lugar, no hay forma de saltearla desde
una pantalla nueva.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import EstadoFlujo, HistorialEstado, Tratamiento, Usuario, ahora
from ..security import registrar
from ..unidad_de_trabajo import transaccion


class TransicionInvalida(Exception):
    """La acción no se puede hacer sobre este tratamiento con este usuario."""


@dataclass(frozen=True)
class Transicion:
    accion: str
    etiqueta: str
    desde: tuple[EstadoFlujo, ...]
    hacia: EstadoFlujo
    capacidad: str
    requiere_motivo: bool = False
    solo_autor: bool = False
    tono: str = "normal"

    def aplica_a(self, estado: EstadoFlujo) -> bool:
        return estado in self.desde


TRANSICIONES: dict[str, Transicion] = {
    t.accion: t
    for t in [
        Transicion(
            "enviar", "Enviar a aprobación",
            (EstadoFlujo.BORRADOR,), EstadoFlujo.PENDIENTE_APROBACION,
            "registrar_solicitud", solo_autor=True,
        ),
        Transicion(
            "aprobar", "Aprobar",
            (EstadoFlujo.PENDIENTE_APROBACION,), EstadoFlujo.APROBADA,
            "aprobar_solicitud",
        ),
        Transicion(
            "rechazar", "Rechazar",
            (EstadoFlujo.PENDIENTE_APROBACION,), EstadoFlujo.RECHAZADA,
            "aprobar_solicitud", requiere_motivo=True, tono="peligro",
        ),
        Transicion(
            "corregir", "Corregir y reenviar",
            (EstadoFlujo.RECHAZADA,), EstadoFlujo.BORRADOR,
            "registrar_solicitud", solo_autor=True,
        ),
        Transicion(
            "tomar", "Tomar en laboratorio",
            (EstadoFlujo.APROBADA,), EstadoFlujo.EN_ENSAYO,
            "ejecutar_ensayo",
        ),
        Transicion(
            "finalizar", "Cargar resultados y finalizar",
            (EstadoFlujo.EN_ENSAYO,), EstadoFlujo.FINALIZADA,
            "ejecutar_ensayo",
        ),
        Transicion(
            "anular", "Anular",
            (
                EstadoFlujo.BORRADOR,
                EstadoFlujo.PENDIENTE_APROBACION,
                EstadoFlujo.RECHAZADA,
                EstadoFlujo.APROBADA,
                EstadoFlujo.EN_ENSAYO,
            ),
            EstadoFlujo.ANULADA,
            "anular_solicitud", requiere_motivo=True, tono="peligro",
        ),
    ]
}

# Orden en que se dibuja la línea de tiempo del circuito.
SECUENCIA = (
    EstadoFlujo.BORRADOR,
    EstadoFlujo.PENDIENTE_APROBACION,
    EstadoFlujo.APROBADA,
    EstadoFlujo.EN_ENSAYO,
    EstadoFlujo.FINALIZADA,
)


def _es_autor(tratamiento: Tratamiento, usuario: Usuario) -> bool:
    return tratamiento.solicitado_por_id == usuario.id


def acciones_disponibles(tratamiento: Tratamiento, usuario: Usuario) -> list[Transicion]:
    """Qué puede hacer este usuario, ahora, con este tratamiento.

    Las pantallas dibujan botones a partir de esto: si una acción no está acá, no
    aparece. Y si igual llegara por URL, `ejecutar()` la vuelve a rechazar.
    """
    from ..security import puede

    return [
        transicion
        for transicion in TRANSICIONES.values()
        if transicion.aplica_a(tratamiento.estado_flujo)
        and puede(transicion.capacidad, usuario)
        and (not transicion.solo_autor or _es_autor(tratamiento, usuario))
    ]


def ejecutar(
    tratamiento: Tratamiento,
    accion: str,
    usuario: Usuario,
    motivo: str | None = None,
) -> Tratamiento:
    """Aplica una transición. Levanta `TransicionInvalida` si algo no cierra.

    Abre su propia unidad de trabajo, pero si ya hay una abierta se suma a ella:
    `solicitudes.registrar()` crea la solicitud y la envía en un solo movimiento, y
    las dos cosas tienen que entrar juntas o no entrar ninguna.
    """
    from ..security import puede

    with transaccion():
        return _aplicar(tratamiento, accion, usuario, motivo, puede)


def _aplicar(tratamiento, accion, usuario, motivo, puede) -> Tratamiento:
    """La transición en sí. Separada para que la transacción se lea de un vistazo."""
    transicion = TRANSICIONES.get(accion)
    if transicion is None:
        raise TransicionInvalida(f"Acción desconocida: {accion}")

    if not transicion.aplica_a(tratamiento.estado_flujo):
        raise TransicionInvalida(
            f"No se puede {transicion.etiqueta.lower()} algo que está en estado "
            f"{tratamiento.estado_flujo.etiqueta.lower()}."
        )
    if not puede(transicion.capacidad, usuario):
        raise TransicionInvalida("Tu rol no puede realizar esta acción.")
    if transicion.solo_autor and not _es_autor(tratamiento, usuario):
        raise TransicionInvalida("Solo quien registró la solicitud puede hacer esto.")

    motivo = (motivo or "").strip()
    if transicion.requiere_motivo and not motivo:
        raise TransicionInvalida("Esta acción necesita un motivo escrito.")

    anterior = tratamiento.estado_flujo
    momento = ahora()
    tratamiento.estado_flujo = transicion.hacia

    if accion == "enviar":
        tratamiento.fecha_envio = momento
    elif accion == "aprobar":
        tratamiento.aprobado_por_id = usuario.id
        tratamiento.fecha_aprobacion = momento
        tratamiento.motivo_rechazo = None
    elif accion == "rechazar":
        tratamiento.motivo_rechazo = motivo
    elif accion == "corregir":
        tratamiento.motivo_rechazo = None
    elif accion == "tomar":
        tratamiento.tomado_por_id = usuario.id

    db.session.add(
        HistorialEstado(
            tratamiento=tratamiento,
            desde=anterior,
            hacia=transicion.hacia,
            accion=accion,
            usuario_id=usuario.id,
            motivo=motivo or None,
            ocurrido_en=momento,
        )
    )
    registrar(
        f"solicitud_{accion}",
        entidad="tratamiento",
        entidad_id=tratamiento.id,
        usuario_id=usuario.id,
        datos={"desde": anterior.value, "hacia": transicion.hacia.value},
        confirmar=False,
    )
    return tratamiento


def proximo_numero(prefijo: str = "SOL") -> str:
    """Numera las solicitudes por año: SOL-2026-0001.

    Cuenta sobre las solicitudes del año, no sobre la tabla entera, para que los
    tratamientos importados del histórico no corran la numeración.
    """
    anio = ahora().year
    patron = f"{prefijo}-{anio}-%"
    cantidad = (
        db.session.scalar(
            select(func.count(Tratamiento.id)).where(
                Tratamiento.numero_tratamiento.like(patron)
            )
        )
        or 0
    )
    return f"{prefijo}-{anio}-{cantidad + 1:04d}"


def bandeja_de(usuario: Usuario) -> list[EstadoFlujo]:
    """Estados que le importan a este usuario al abrir la bandeja.

    Se decide por **capacidades y no por rol**. Con roles editables, preguntar «¿es
    supervisor?» dejaría afuera a un rol nuevo que sí puede aprobar: la bandeja le
    aparecería vacía y el botón de aprobar, disponible. Preguntando «¿puede
    aprobar?», cualquier rol que se cree después funciona sin tocar esto.

    Los estados se acumulan porque un rol puede tener varias capacidades a la vez.
    """
    from ..security import puede

    estados: list[EstadoFlujo] = []

    def sumar(*valores):
        for valor in valores:
            if valor not in estados:
                estados.append(valor)

    if puede("aprobar_solicitud", usuario):
        sumar(EstadoFlujo.PENDIENTE_APROBACION, EstadoFlujo.APROBADA, EstadoFlujo.EN_ENSAYO)
    if puede("ejecutar_ensayo", usuario):
        sumar(EstadoFlujo.APROBADA, EstadoFlujo.EN_ENSAYO, EstadoFlujo.FINALIZADA)
    if puede("registrar_solicitud", usuario):
        sumar(
            EstadoFlujo.BORRADOR,
            EstadoFlujo.PENDIENTE_APROBACION,
            EstadoFlujo.RECHAZADA,
            EstadoFlujo.APROBADA,
        )

    # Quien solo consulta ve todo: no puede tocar nada igual.
    return estados or list(EstadoFlujo)
