"""Normalización asistida: de un nombre escrito a mano a un producto del maestro.

Qué problema resuelve
---------------------
La importación deja marcados como PENDIENTE todos los productos cuyo nombre no
coincidió con nada del maestro. Este módulo los junta, propone candidatos y aplica
la decisión de la persona a **todas** las filas que comparten la misma clave.

Tres reglas que ordenan todo lo de acá:

1. **La unidad de trabajo es la clave, no la fila.** Si "Coadyubante Silic." aparece
   en 40 tratamientos, es *una* decisión que impacta 40 filas, no 40 decisiones.
   Esa es la diferencia entre una tarde de trabajo y quince minutos.
2. **Nada se aplica solo.** El sistema ordena candidatos; confirma una persona. El
   maestro sucio del Excel se originó justamente en una fórmula que resolvía sola.
3. **Toda decisión queda escrita como `MapeoProducto`.** No se toca solamente el
   detalle: se guarda la regla. Así la próxima importación del mismo archivo ya
   resuelve el nombre sin volver a preguntar, y la decisión es auditable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import func, select, update

from ..extensions import db
from ..unidad_de_trabajo import transaccion
from ..models import (
    DetalleMezcla,
    EstadoMapeo,
    EstadoNormalizacion,
    EstadoProducto,
    Mezcla,
    MapeoProducto,
    Producto,
    Tratamiento,
)
from ..security import registrar
from . import normalizacion as norm

MAXIMO_SUGERENCIAS = 5
POR_PAGINA = 25


class DecisionInvalida(Exception):
    """La decisión no se puede aplicar: no hay filas, el producto no existe, etc."""


# --------------------------------------------------------------- estructuras


@dataclass(frozen=True)
class Sugerencia:
    producto: Producto
    puntaje: float
    motivo: str

    @property
    def confianza(self) -> str:
        return norm.confianza(self.puntaje)

    @property
    def porcentaje(self) -> int:
        return int(round(self.puntaje * 100))


@dataclass
class Pendiente:
    """Un nombre sin resolver, con todo lo que hace falta para decidir sobre él."""

    clave: str
    nombre: str                      # la grafía más frecuente, la que se muestra
    usos: int                        # cuántas filas de mezcla arrastra
    variantes: list[str] = field(default_factory=list)
    tratamientos: list[str] = field(default_factory=list)
    sugerencias: list[Sugerencia] = field(default_factory=list)

    @property
    def ancla(self) -> str:
        """Identificador estable y seguro para la URL y el DOM.

        La clave es texto libre con espacios y acentos ya quitados, pero igual no
        sirve como fragmento de URL ni como `id` de HTML. Se usa un hash corto y
        determinístico: la misma clave da siempre el mismo ancla, así HTMX puede
        apuntarle a la tarjeta sin que el servidor tenga que devolver ids inventados.
        """
        return hashlib.blake2s(self.clave.encode("utf-8"), digest_size=6).hexdigest()

    @property
    def mejor(self) -> Sugerencia | None:
        return self.sugerencias[0] if self.sugerencias else None


# ------------------------------------------------------------------ lectura


def _candidatos() -> list[tuple[Producto, str, str]]:
    """El maestro vigente, con las dos claves contra las que se compara.

    Se compara contra el nombre comercial y también contra el principio activo:
    en las planillas es habitual que el técnico escriba "Glifosato" cuando el
    producto se llama "Round Up Full". Sin la segunda clave, ese caso no aparece
    en ninguna sugerencia y hay que buscarlo a mano.
    """
    productos = db.session.scalars(
        select(Producto).where(Producto.estado == EstadoProducto.VIGENTE)
    )
    return [(p, p.clave_normalizada, norm.clave(p.activo)) for p in productos]


def sugerencias_para(
    nombre: str, candidatos=None, limite: int = MAXIMO_SUGERENCIAS
) -> list[Sugerencia]:
    """Candidatos del maestro ordenados por parecido, con el motivo de cada uno."""
    objetivo = norm.clave(nombre)
    if not objetivo:
        return []
    if candidatos is None:
        candidatos = _candidatos()

    encontradas: list[Sugerencia] = []
    for producto, clave_nombre, clave_activo in candidatos:
        por_nombre = norm.puntaje(objetivo, clave_nombre)
        # El activo pesa un poco menos: coincidir en principio activo es una pista
        # fuerte, pero dos productos distintos pueden compartirlo.
        por_activo = norm.puntaje(objetivo, clave_activo) * 0.90 if clave_activo else 0.0

        if por_nombre >= por_activo:
            valor, motivo = por_nombre, "nombre parecido"
        else:
            valor, motivo = por_activo, "coincide el principio activo"

        if valor >= norm.UMBRAL_SUGERENCIA:
            encontradas.append(Sugerencia(producto=producto, puntaje=round(valor, 4), motivo=motivo))

    encontradas.sort(key=lambda s: (-s.puntaje, s.producto.nombre_comercial))
    return encontradas[:limite]


def _consulta_pendientes(texto: str = ""):
    consulta = (
        select(
            DetalleMezcla.clave_normalizada,
            func.count(DetalleMezcla.id).label("usos"),
        )
        .where(DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE)
        .group_by(DetalleMezcla.clave_normalizada)
    )
    if texto:
        consulta = consulta.where(DetalleMezcla.clave_normalizada.ilike(f"%{norm.clave(texto)}%"))
    return consulta.order_by(func.count(DetalleMezcla.id).desc(), DetalleMezcla.clave_normalizada)


def contar_pendientes(texto: str = "") -> int:
    """Cuántos nombres distintos quedan por resolver (no cuántas filas)."""
    return db.session.scalar(
        select(func.count()).select_from(_consulta_pendientes(texto).subquery())
    ) or 0


def listar_pendientes(
    texto: str = "", pagina: int = 1, por_pagina: int = POR_PAGINA, con_sugerencias: bool = True
) -> list[Pendiente]:
    """Los nombres sin resolver, del que más filas arrastra al que menos.

    Ese orden no es cosmético: resolver primero lo más usado es lo que hace subir
    la salud del dato con la menor cantidad de clics.
    """
    pagina = max(1, pagina)
    filas = db.session.execute(
        _consulta_pendientes(texto).limit(por_pagina).offset((pagina - 1) * por_pagina)
    ).all()
    claves = [f[0] for f in filas]
    if not claves:
        return []

    variantes = _variantes_por_clave(claves)
    ejemplos = _tratamientos_por_clave(claves)
    candidatos = _candidatos() if con_sugerencias else []

    pendientes = []
    for clave, usos in filas:
        formas = variantes.get(clave, [])
        nombre = formas[0] if formas else clave
        pendientes.append(
            Pendiente(
                clave=clave,
                nombre=nombre,
                usos=usos,
                variantes=formas,
                tratamientos=ejemplos.get(clave, []),
                sugerencias=sugerencias_para(nombre, candidatos) if con_sugerencias else [],
            )
        )
    return pendientes


def obtener_pendiente(clave: str, con_sugerencias: bool = True) -> Pendiente | None:
    """Un pendiente puntual, para responder a una acción sobre él."""
    usos = db.session.scalar(
        select(func.count(DetalleMezcla.id)).where(
            DetalleMezcla.clave_normalizada == clave,
            DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE,
        )
    ) or 0
    if not usos:
        return None
    formas = _variantes_por_clave([clave]).get(clave, [])
    nombre = formas[0] if formas else clave
    return Pendiente(
        clave=clave,
        nombre=nombre,
        usos=usos,
        variantes=formas,
        tratamientos=_tratamientos_por_clave([clave]).get(clave, []),
        sugerencias=sugerencias_para(nombre) if con_sugerencias else [],
    )


def _variantes_por_clave(claves: list[str]) -> dict[str, list[str]]:
    """Las grafías crudas de cada clave, de la más frecuente a la menos.

    Sirve para dos cosas: mostrar el nombre tal como lo escribe la gente (no la
    clave, que es un artefacto interno) y dejar ver de un vistazo que "Round-up",
    "ROUND UP" y "Round Up " son la misma discusión.
    """
    filas = db.session.execute(
        select(
            DetalleMezcla.clave_normalizada,
            DetalleMezcla.nombre_original,
            func.count(DetalleMezcla.id).label("veces"),
        )
        .where(DetalleMezcla.clave_normalizada.in_(claves))
        .group_by(DetalleMezcla.clave_normalizada, DetalleMezcla.nombre_original)
        .order_by(func.count(DetalleMezcla.id).desc())
    ).all()
    agrupadas: dict[str, list[str]] = {}
    for clave, nombre, _veces in filas:
        agrupadas.setdefault(clave, []).append(nombre)
    return agrupadas


def _tratamientos_por_clave(claves: list[str], limite: int = 4) -> dict[str, list[str]]:
    """Algunos números de tratamiento donde aparece cada nombre.

    Sin esto la decisión es a ciegas: ver en qué ensayos se usó es lo que le permite
    al técnico saber de qué producto se está hablando cuando el nombre es ambiguo.
    """
    filas = db.session.execute(
        select(DetalleMezcla.clave_normalizada, Tratamiento.numero_tratamiento)
        .join(Mezcla, Mezcla.id == DetalleMezcla.mezcla_id)
        .join(Tratamiento, Tratamiento.id == Mezcla.tratamiento_id)
        .where(
            DetalleMezcla.clave_normalizada.in_(claves),
            DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE,
        )
        .order_by(Tratamiento.fecha.desc().nullslast())
    ).all()
    agrupados: dict[str, list[str]] = {}
    for clave, numero in filas:
        lista = agrupados.setdefault(clave, [])
        if numero not in lista and len(lista) < limite:
            lista.append(numero)
    return agrupados


def buscar_para_asignar(texto: str, limite: int = 10) -> list[Producto]:
    """Búsqueda libre en el maestro, para cuando ninguna sugerencia sirve."""
    texto = (texto or "").strip()
    if not texto:
        return []
    patron = f"%{texto}%"
    return list(
        db.session.scalars(
            select(Producto)
            .where(
                Producto.estado == EstadoProducto.VIGENTE,
                db.or_(
                    Producto.nombre_comercial.ilike(patron),
                    Producto.activo.ilike(patron),
                    Producto.marca.ilike(patron),
                ),
            )
            .order_by(Producto.nombre_comercial)
            .limit(limite)
        )
    )


# ---------------------------------------------------------------- decisiones


@dataclass(frozen=True)
class Resultado:
    """Lo que devolvió una decisión, para poder contarlo en pantalla y auditarlo."""

    clave: str
    filas_afectadas: int
    producto: Producto | None
    accion: str

    @property
    def ancla(self) -> str:
        """Mismo ancla que el pendiente del que salió: la tarjeta se reemplaza en su lugar."""
        return hashlib.blake2s(self.clave.encode("utf-8"), digest_size=6).hexdigest()


def _mapeo_de(clave: str, nombre_original: str) -> MapeoProducto:
    """El registro de la regla. Se reutiliza si ya existía para esa clave."""
    mapeo = db.session.scalar(
        select(MapeoProducto).where(MapeoProducto.clave_normalizada == clave)
    )
    if mapeo is None:
        mapeo = MapeoProducto(nombre_original=nombre_original[:220], clave_normalizada=clave)
        db.session.add(mapeo)
    return mapeo


def _aplicar_a_detalles(clave: str, producto_id: int | None, estado: EstadoNormalizacion, mapeo_id: int) -> int:
    """Propaga la decisión a todas las filas con esa clave, en una sola sentencia.

    Un UPDATE masivo y no un bucle por fila: un nombre repetido cientos de veces en
    el histórico tiene que resolverse en una ida a la base, no en cientos.
    """
    resultado = db.session.execute(
        update(DetalleMezcla)
        .where(DetalleMezcla.clave_normalizada == clave)
        .values(producto_id=producto_id, estado_normalizacion=estado, mapeo_id=mapeo_id)
        .execution_options(synchronize_session=False)
    )
    return resultado.rowcount or 0


def confirmar(clave: str, producto_id: int, usuario, nombre_original: str | None = None) -> Resultado:
    """Asocia una clave a un producto del maestro y lo propaga a todo el histórico."""
    clave = (clave or "").strip()
    if not clave:
        raise DecisionInvalida("Falta la clave del nombre a resolver.")

    producto = db.session.get(Producto, producto_id)
    if producto is None:
        raise DecisionInvalida("El producto elegido no existe en el maestro.")

    if nombre_original is None:
        formas = _variantes_por_clave([clave]).get(clave, [])
        nombre_original = formas[0] if formas else clave

    with transaccion():
        return _confirmar(clave, producto, usuario, nombre_original)


def _confirmar(clave, producto, usuario, nombre_original) -> Resultado:

    mapeo = _mapeo_de(clave, nombre_original)
    mapeo.producto_id = producto.id
    mapeo.nombre_normalizado = producto.nombre_comercial
    mapeo.estado = EstadoMapeo.CONFIRMADO
    mapeo.confirmado_por_id = getattr(usuario, "id", None)
    db.session.flush()

    filas = _aplicar_a_detalles(clave, producto.id, EstadoNormalizacion.CONFIRMADA, mapeo.id)
    registrar(
        "mapeo_confirmado",
        entidad="mapeo_producto",
        entidad_id=mapeo.id,
        usuario_id=getattr(usuario, "id", None),
        datos={
            "clave": clave,
            "nombre_original": nombre_original,
            "producto": producto.nombre_comercial,
            "filas": filas,
        },
        confirmar=False,
    )
    return Resultado(clave=clave, filas_afectadas=filas, producto=producto, accion="confirmado")


def descartar(clave: str, usuario, motivo: str = "") -> Resultado:
    """Marca que ese texto no corresponde a ningún producto del maestro.

    Es una decisión tan válida como asignar: en las planillas hay celdas con
    "s/d", con una dosis suelta o con el nombre de un cultivo. Sin esta salida esas
    filas quedan pendientes para siempre y la salud del dato nunca llega al 100 %.
    """
    clave = (clave or "").strip()
    if not clave:
        raise DecisionInvalida("Falta la clave del nombre a descartar.")

    with transaccion():
        return _descartar(clave, usuario, motivo)


def _descartar(clave: str, usuario, motivo: str) -> Resultado:

    formas = _variantes_por_clave([clave]).get(clave, [])
    mapeo = _mapeo_de(clave, formas[0] if formas else clave)
    mapeo.producto_id = None
    mapeo.nombre_normalizado = None
    mapeo.estado = EstadoMapeo.RECHAZADO
    mapeo.confirmado_por_id = getattr(usuario, "id", None)
    db.session.flush()

    filas = _aplicar_a_detalles(clave, None, EstadoNormalizacion.SIN_PRODUCTO, mapeo.id)
    registrar(
        "mapeo_descartado",
        entidad="mapeo_producto",
        entidad_id=mapeo.id,
        usuario_id=getattr(usuario, "id", None),
        datos={"clave": clave, "motivo": motivo[:500], "filas": filas},
        confirmar=False,
    )
    return Resultado(clave=clave, filas_afectadas=filas, producto=None, accion="descartado")


def deshacer(clave: str, usuario) -> Resultado:
    """Devuelve una clave ya resuelta al estado pendiente.

    Existe porque la etapa 3 aplica cada decisión sobre decenas de filas de golpe:
    sin una vuelta atrás barata, un clic equivocado obliga a reimportar el archivo.
    """
    clave = (clave or "").strip()
    mapeo = db.session.scalar(
        select(MapeoProducto).where(MapeoProducto.clave_normalizada == clave)
    )
    if mapeo is None:
        raise DecisionInvalida("Ese nombre no tiene ninguna decisión registrada.")

    with transaccion():
        return _deshacer(clave, mapeo, usuario)


def _deshacer(clave: str, mapeo, usuario) -> Resultado:

    filas = db.session.execute(
        update(DetalleMezcla)
        .where(DetalleMezcla.clave_normalizada == clave)
        .values(
            producto_id=None,
            estado_normalizacion=EstadoNormalizacion.PENDIENTE,
            mapeo_id=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount or 0

    registrar(
        "mapeo_deshecho",
        entidad="mapeo_producto",
        entidad_id=mapeo.id,
        usuario_id=getattr(usuario, "id", None),
        datos={"clave": clave, "filas": filas},
        confirmar=False,
    )
    db.session.delete(mapeo)
    return Resultado(clave=clave, filas_afectadas=filas, producto=None, accion="deshecho")


# ------------------------------------------------------------------ resueltos


@dataclass(frozen=True)
class Resuelto:
    mapeo: MapeoProducto
    usos: int


def listar_resueltos(texto: str = "", limite: int = 100) -> list[Resuelto]:
    """Las decisiones ya tomadas, para revisarlas o darlas de baja."""
    consulta = (
        select(MapeoProducto, func.count(DetalleMezcla.id))
        .outerjoin(
            DetalleMezcla, DetalleMezcla.clave_normalizada == MapeoProducto.clave_normalizada
        )
        .group_by(MapeoProducto.id)
        .order_by(MapeoProducto.actualizado_en.desc())
        .limit(limite)
    )
    if texto:
        patron = f"%{texto.strip()}%"
        consulta = consulta.where(
            db.or_(
                MapeoProducto.nombre_original.ilike(patron),
                MapeoProducto.nombre_normalizado.ilike(patron),
            )
        )
    return [Resuelto(mapeo=m, usos=u) for m, u in db.session.execute(consulta).all()]


def contar_resueltos() -> int:
    return db.session.scalar(select(func.count(MapeoProducto.id))) or 0
