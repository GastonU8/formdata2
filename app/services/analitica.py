"""Indicadores del tablero, calculados sobre el conjunto que devuelven los filtros.

Tres reglas que ordenan este módulo:

1. **Un solo conjunto.** Todo se calcula sobre `consultas.subconsulta_ids(filtros)`,
   el mismo conjunto que devuelve la búsqueda. Ningún gráfico puede contar algo que
   la lista no muestre, y cada barra puede enlazar a la búsqueda que la explica.
2. **Nada específico de un motor.** El agrupamiento por período se hace en Python y
   no con `strftime` (SQLite) o `to_char` (PostgreSQL). Son unos miles de fechas: el
   costo es despreciable y el comportamiento es idéntico en los dos motores, que es
   una condición que el proyecto se puso desde la etapa 1.
3. **Solo se grafica lo que el dato real tiene.** El Excel histórico no trae
   porcentaje de efectividad; trae pH, dureza y las observaciones del ensayo escritas
   por el laboratorio. El tablero se construye sobre eso. Ver `observaciones_de_ensayo`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import case, func, select

from ..extensions import db
from ..models import (
    Cliente,
    DetalleMezcla,
    EstadoNormalizacion,
    Mezcla,
    Producto,
    Resultado,
    Tratamiento,
)
from . import consultas

MAXIMO_CATEGORIAS = 10
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@dataclass(frozen=True)
class Punto:
    """Una barra. `enlace` es lo que permite pasar del gráfico a los datos crudos."""

    etiqueta: str
    valor: float
    enlace: str | None = None
    detalle: str | None = None


@dataclass
class Serie:
    titulo: str
    puntos: list[Punto] = field(default_factory=list)
    unidad: str = ""
    nota: str = ""

    @property
    def maximo(self) -> float:
        return max((p.valor for p in self.puntos), default=0.0)

    @property
    def total(self) -> float:
        return sum(p.valor for p in self.puntos)

    @property
    def hay_datos(self) -> bool:
        return any(p.valor for p in self.puntos)

    def porcentaje(self, punto: Punto) -> float:
        """Cuánto mide esta barra respecto de la más alta, en 0..100."""
        return round(punto.valor / self.maximo * 100, 2) if self.maximo else 0.0

    def proporcion(self, punto: Punto) -> float:
        """Cuánto representa esta barra sobre el total, en 0..100."""
        return round(punto.valor / self.total * 100, 2) if self.total else 0.0


# ------------------------------------------------------------------- resumen


@dataclass(frozen=True)
class Resumen:
    tratamientos: int
    clientes: int
    cultivos: int
    productos: int
    filas_producto: int
    filas_sin_mapear: int

    @property
    def porcentaje_normalizado(self) -> float:
        if not self.filas_producto:
            return 100.0
        return round((self.filas_producto - self.filas_sin_mapear) / self.filas_producto * 100, 1)

    @property
    def confiable(self) -> bool:
        return self.porcentaje_normalizado >= 95.0


def resumen(filtros: consultas.Filtros) -> Resumen:
    """Las cifras de cabecera del recorte elegido.

    Incluye la normalización **del recorte**, no la global: un informe filtrado por
    un cliente puede estar impecable mientras el conjunto general no lo está, y al
    revés. Decir "95 % normalizado" sin aclarar sobre qué es lo que vuelve inútil a
    un indicador de calidad.
    """
    ids = consultas.subconsulta_ids(filtros).subquery()

    tratamientos = db.session.scalar(select(func.count()).select_from(ids)) or 0
    clientes = db.session.scalar(
        select(func.count(func.distinct(Tratamiento.cliente_id))).where(
            Tratamiento.id.in_(select(ids.c.id)), Tratamiento.cliente_id.is_not(None)
        )
    ) or 0
    cultivos = db.session.scalar(
        select(func.count(func.distinct(Tratamiento.cultivo))).where(
            Tratamiento.id.in_(select(ids.c.id)), Tratamiento.cultivo.is_not(None)
        )
    ) or 0

    filas = db.session.execute(
        select(
            func.count(DetalleMezcla.id),
            func.count(func.distinct(DetalleMezcla.producto_id)),
            func.sum(
                case(
                    (DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE, 1),
                    else_=0,
                )
            ),
        )
        .join(Mezcla, Mezcla.id == DetalleMezcla.mezcla_id)
        .where(Mezcla.tratamiento_id.in_(select(ids.c.id)))
    ).one()

    return Resumen(
        tratamientos=tratamientos,
        clientes=clientes,
        cultivos=cultivos,
        productos=filas[1] or 0,
        filas_producto=filas[0] or 0,
        filas_sin_mapear=int(filas[2] or 0),
    )


# -------------------------------------------------------------------- series


def _enlace(filtros: consultas.Filtros, **cambios) -> str:
    """La búsqueda que explica una barra: los filtros actuales más el de la barra."""
    from flask import url_for

    argumentos = {
        "texto": filtros.texto,
        "cultivo": filtros.cultivo,
        "tipo": filtros.tipo_tratamiento,
        "cliente": filtros.cliente_id,
        "producto_id": filtros.producto_id,
        "producto": filtros.producto_texto,
        "resultado": filtros.estado_resultado,
        "desde": filtros.desde.isoformat() if filtros.desde else "",
        "hasta": filtros.hasta.isoformat() if filtros.hasta else "",
        "sin_mapear": "1" if filtros.solo_sin_mapear else "",
    }
    argumentos.update(cambios)
    return url_for(
        "tratamientos.buscar", **{k: v for k, v in argumentos.items() if v not in (None, "")}
    )


def por_periodo(filtros: consultas.Filtros, meses: int = 24) -> Serie:
    """Ensayos por mes, sin huecos.

    Los meses sin actividad se rellenan con cero a propósito: si se omitieran, dos
    meses vacíos seguidos se verían como un único intervalo corto y la caída pasaría
    inadvertida, que es justo lo que un gráfico temporal tiene que mostrar.
    """
    fechas = db.session.scalars(
        select(Tratamiento.fecha).where(
            Tratamiento.id.in_(consultas.subconsulta_ids(filtros)),
            Tratamiento.fecha.is_not(None),
        )
    ).all()

    if not fechas:
        return Serie(titulo="Ensayos por mes", unidad="ensayos")

    conteo = Counter((f.year, f.month) for f in fechas)
    fin = max(conteo)
    inicio = min(conteo)

    # Se recorta a los últimos `meses` para que el eje siga siendo legible cuando el
    # histórico abarque varios años.
    total_meses = (fin[0] - inicio[0]) * 12 + (fin[1] - inicio[1]) + 1
    a_mostrar = min(total_meses, meses)

    puntos = []
    anio, mes = fin
    for _ in range(a_mostrar):
        puntos.append(
            Punto(
                etiqueta=f"{MESES[mes - 1]} {str(anio)[2:]}",
                valor=conteo.get((anio, mes), 0),
                enlace=_enlace(
                    filtros,
                    desde=date(anio, mes, 1).isoformat(),
                    hasta=_fin_de_mes(anio, mes).isoformat(),
                ),
            )
        )
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12

    puntos.reverse()
    nota = ""
    if total_meses > a_mostrar:
        nota = f"Se muestran los últimos {a_mostrar} meses de {total_meses} con actividad."
    return Serie(titulo="Ensayos por mes", puntos=puntos, unidad="ensayos", nota=nota)


def _fin_de_mes(anio: int, mes: int) -> date:
    return date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1).fromordinal(
        date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1).toordinal() - 1
    )


def _por_columna(
    filtros: consultas.Filtros, columna, titulo: str, clave_enlace: str, limite: int
) -> Serie:
    filas = db.session.execute(
        select(columna, func.count(Tratamiento.id))
        .where(
            Tratamiento.id.in_(consultas.subconsulta_ids(filtros)),
            columna.is_not(None),
            columna != "",
        )
        .group_by(columna)
        .order_by(func.count(Tratamiento.id).desc())
        .limit(limite)
    ).all()
    return Serie(
        titulo=titulo,
        unidad="ensayos",
        puntos=[
            Punto(etiqueta=str(valor), valor=cantidad, enlace=_enlace(filtros, **{clave_enlace: valor}))
            for valor, cantidad in filas
        ],
    )


def por_cultivo(filtros: consultas.Filtros, limite: int = MAXIMO_CATEGORIAS) -> Serie:
    return _por_columna(filtros, Tratamiento.cultivo, "Ensayos por cultivo", "cultivo", limite)


def por_tipo(filtros: consultas.Filtros, limite: int = MAXIMO_CATEGORIAS) -> Serie:
    return _por_columna(
        filtros, Tratamiento.tipo_tratamiento, "Ensayos por tipo de tratamiento", "tipo", limite
    )


def por_cliente(filtros: consultas.Filtros, limite: int = MAXIMO_CATEGORIAS) -> Serie:
    filas = db.session.execute(
        select(Cliente.id, Cliente.nombre, func.count(Tratamiento.id))
        .join(Tratamiento, Tratamiento.cliente_id == Cliente.id)
        .where(Tratamiento.id.in_(consultas.subconsulta_ids(filtros)))
        .group_by(Cliente.id)
        .order_by(func.count(Tratamiento.id).desc())
        .limit(limite)
    ).all()
    return Serie(
        titulo="Clientes con más ensayos",
        unidad="ensayos",
        puntos=[
            Punto(etiqueta=nombre, valor=cantidad, enlace=_enlace(filtros, cliente=identificador))
            for identificador, nombre, cantidad in filas
        ],
    )


def productos_mas_usados(filtros: consultas.Filtros, limite: int = MAXIMO_CATEGORIAS) -> Serie:
    filas = db.session.execute(
        select(Producto.id, Producto.nombre_comercial, func.count(DetalleMezcla.id))
        .join(DetalleMezcla, DetalleMezcla.producto_id == Producto.id)
        .join(Mezcla, Mezcla.id == DetalleMezcla.mezcla_id)
        .where(Mezcla.tratamiento_id.in_(consultas.subconsulta_ids(filtros)))
        .group_by(Producto.id)
        .order_by(func.count(DetalleMezcla.id).desc())
        .limit(limite)
    ).all()
    return Serie(
        titulo="Productos más usados",
        unidad="usos en mezclas",
        puntos=[
            Punto(etiqueta=nombre, valor=usos, enlace=_enlace(filtros, producto_id=identificador))
            for identificador, nombre, usos in filas
        ],
        nota="Cuenta filas de mezcla, no ensayos: un producto puede repetirse dentro del mismo ensayo.",
    )


def observaciones_de_ensayo(filtros: consultas.Filtros, limite: int = MAXIMO_CATEGORIAS) -> Serie:
    """Cómo terminó el ensayo, agrupando lo que escribió el laboratorio.

    No se normalizan los valores ni se los traduce a categorías inventadas. En el
    archivo real esta columna trae códigos del laboratorio (`OK`, `PPDO` por
    precipitado) y no una clasificación cerrada. Presentarlos como «Compatible /
    Incompatible» sería fabricar una interpretación que el dato no tiene: si el
    laboratorio quiere categorías fijas, esa es una decisión suya, no del tablero.
    """
    filas = db.session.execute(
        select(Resultado.estado_resultado, func.count(Resultado.id))
        .where(
            Resultado.tratamiento_id.in_(consultas.subconsulta_ids(filtros)),
            Resultado.estado_resultado.is_not(None),
            Resultado.estado_resultado != "",
        )
        .group_by(Resultado.estado_resultado)
        .order_by(func.count(Resultado.id).desc())
        .limit(limite)
    ).all()
    return Serie(
        titulo="Observación del ensayo",
        unidad="ensayos",
        puntos=[
            Punto(etiqueta=str(valor), valor=cantidad, enlace=_enlace(filtros, resultado=valor))
            for valor, cantidad in filas
        ],
        nota="Valores tal como los escribe el laboratorio; no se reinterpretan.",
    )


TRAMOS_PH = [
    ("< 4", None, 4.0),
    ("4 – 5", 4.0, 5.0),
    ("5 – 6", 5.0, 6.0),
    ("6 – 7", 6.0, 7.0),
    ("7 – 8", 7.0, 8.0),
    ("≥ 8", 8.0, None),
]


def distribucion_ph(filtros: consultas.Filtros) -> Serie:
    """Histograma del pH de la mezcla, que sí existe en el archivo real.

    Los tramos son fijos y no calculados: un histograma con tramos automáticos
    cambia de forma cada vez que entra un dato nuevo y deja de ser comparable entre
    dos informes, que es exactamente para lo que se usa.
    """
    valores = db.session.scalars(
        select(Resultado.ph_mezcla).where(
            Resultado.tratamiento_id.in_(consultas.subconsulta_ids(filtros)),
            Resultado.ph_mezcla.is_not(None),
        )
    ).all()

    puntos = []
    for etiqueta, minimo, maximo in TRAMOS_PH:
        cantidad = sum(
            1
            for v in valores
            if (minimo is None or float(v) >= minimo) and (maximo is None or float(v) < maximo)
        )
        puntos.append(Punto(etiqueta=etiqueta, valor=cantidad))
    return Serie(
        titulo="pH de la mezcla",
        puntos=puntos,
        unidad="ensayos",
        nota=f"Sobre {len(valores)} ensayo(s) con pH de mezcla medido.",
    )


@dataclass(frozen=True)
class Promedios:
    """Promedios de laboratorio, con el conteo de cuántos ensayos los sostienen.

    El conteo va al lado del promedio a propósito: un pH promedio calculado sobre
    tres ensayos no significa lo mismo que uno sobre trescientos, y sin el `n` el
    número invita a conclusiones que no se sostienen.
    """

    ph_agua: float | None
    ph_mezcla: float | None
    dureza_agua: float | None
    medidos_ph_agua: int
    medidos_ph_mezcla: int
    medidos_dureza: int


def promedios(filtros: consultas.Filtros) -> Promedios:
    ids = consultas.subconsulta_ids(filtros)
    fila = db.session.execute(
        select(
            func.avg(Resultado.ph_agua),
            func.count(Resultado.ph_agua),
            func.avg(Resultado.ph_mezcla),
            func.count(Resultado.ph_mezcla),
            func.avg(Resultado.dureza_agua),
            func.count(Resultado.dureza_agua),
        ).where(Resultado.tratamiento_id.in_(ids))
    ).one()

    def redondear(valor, decimales=2):
        return round(float(valor), decimales) if valor is not None else None

    return Promedios(
        ph_agua=redondear(fila[0]),
        medidos_ph_agua=fila[1] or 0,
        ph_mezcla=redondear(fila[2]),
        medidos_ph_mezcla=fila[3] or 0,
        dureza_agua=redondear(fila[4], 1),
        medidos_dureza=fila[5] or 0,
    )


def tablero(filtros: consultas.Filtros) -> dict:
    """Todo lo que necesita la pantalla, en una sola llamada."""
    return {
        "resumen": resumen(filtros),
        "promedios": promedios(filtros),
        "periodo": por_periodo(filtros),
        "cultivos": por_cultivo(filtros),
        "tipos": por_tipo(filtros),
        "clientes": por_cliente(filtros),
        "productos": productos_mas_usados(filtros),
        "observaciones": observaciones_de_ensayo(filtros),
        "ph": distribucion_ph(filtros),
    }
