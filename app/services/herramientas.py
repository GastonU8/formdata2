"""Las herramientas que el asistente puede usar. Nada más que esto.

La decisión central de la etapa
-------------------------------
**El modelo no escribe SQL.** Elige una herramienta de esta lista y le pasa
argumentos; el SQL lo escribió una persona, está parametrizado y vive acá.

El camino fácil habría sido darle el esquema al modelo y dejar que genere consultas.
Se descartó por tres motivos, en orden de gravedad:

1. **Seguridad.** Una consulta generada es código que entra desde afuera. Ninguna
   lista de palabras prohibidas cubre todas las formas de escribir un `DELETE` o de
   leer la tabla de usuarios. Con herramientas fijas, la superficie es exactamente
   la que se decidió.
2. **Corrección silenciosa.** Un `JOIN` mal armado no falla: devuelve un número
   equivocado con cara de correcto. Es el mismo defecto que tenía la fórmula del
   Excel, y el proyecto existe para corregirlo, no para reproducirlo con mejor
   tecnología.
3. **Verificabilidad.** Cada herramienta devuelve, además de los datos, las
   **fuentes**: qué registros los sustentan. Una respuesta sin fuentes no se puede
   auditar, y una respuesta técnica que no se puede auditar no sirve para decidir.

Cada herramienta declara su esquema de argumentos y los valida antes de tocar la
base. Los argumentos vienen de un modelo de lenguaje: son entrada no confiable, y
se tratan igual que los de un formulario público.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    DetalleMezcla,
    EstadoNormalizacion,
    Mezcla,
    Producto,
    Resultado,
    Tratamiento,
)
from . import analitica, consultas
from . import normalizacion as norm

MAXIMO_FILAS = 25          # lo que entra en un contexto sin volverlo ruido
MAXIMO_TEXTO = 120


class ArgumentoInvalido(Exception):
    """El modelo mandó un argumento que no se puede usar. No se ejecuta nada."""


# ------------------------------------------------------------------- fuentes


@dataclass(frozen=True)
class Fuente:
    """Un registro concreto que sustenta parte de la respuesta.

    Lleva `url` para que la persona pueda abrir el dato y comprobarlo con sus
    propios ojos. Ese es el punto: el asistente no pide que se le crea.
    """

    tipo: str
    etiqueta: str
    identificador: int | str | None = None
    url: str | None = None

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "etiqueta": self.etiqueta,
            "id": self.identificador,
            "url": self.url,
        }


@dataclass
class Resultado_:
    """Lo que devuelve una herramienta: datos para el modelo y fuentes para la persona."""

    datos: Any
    fuentes: list[Fuente] = field(default_factory=list)
    nota: str = ""

    @property
    def vacio(self) -> bool:
        if isinstance(self.datos, (list, dict, str)):
            return not self.datos
        return self.datos in (None, 0)


# ------------------------------------------------------- validación de argumentos


def _texto(valor, largo: int = MAXIMO_TEXTO) -> str:
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ArgumentoInvalido(f"Se esperaba texto y llegó {type(valor).__name__}.")
    return valor.strip()[:largo]


def _entero(valor, minimo: int = 1, maximo: int = MAXIMO_FILAS) -> int | None:
    if valor in (None, ""):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ArgumentoInvalido("Se esperaba un número entero.") from None
    return max(minimo, min(numero, maximo))


def _fecha(valor) -> date | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ArgumentoInvalido("Las fechas van en formato AAAA-MM-DD.") from None


def _filtros(argumentos: dict) -> consultas.Filtros:
    """Traduce los argumentos del modelo al mismo `Filtros` que usa todo el sistema.

    Reutilizarlo no es comodidad: garantiza que lo que cuenta el asistente es lo
    mismo que muestran el tablero y la búsqueda. Si el asistente tuviera su propia
    forma de filtrar, tarde o temprano daría otro número que el de la pantalla y
    nadie sabría cuál creer.
    """
    return consultas.Filtros(
        texto=_texto(argumentos.get("texto")),
        cultivo=_texto(argumentos.get("cultivo"), 60),
        tipo_tratamiento=_texto(argumentos.get("tipo_tratamiento"), 60),
        producto_texto=_texto(argumentos.get("producto"), 60),
        estado_resultado=_texto(argumentos.get("observacion"), 60),
        desde=_fecha(argumentos.get("desde")),
        hasta=_fecha(argumentos.get("hasta")),
    )


ESQUEMA_FILTROS = {
    "cultivo": "Cultivo exacto, por ejemplo «Soja». Opcional.",
    "tipo_tratamiento": "Tipo de ensayo, por ejemplo «Compatibilidad». Opcional.",
    "producto": "Nombre comercial o principio activo a buscar dentro de las mezclas. Opcional.",
    "observacion": "Observación del ensayo tal como la escribe el laboratorio. Opcional.",
    "desde": "Fecha mínima, AAAA-MM-DD. Opcional.",
    "hasta": "Fecha máxima, AAAA-MM-DD. Opcional.",
}


def _url_tratamiento(tratamiento_id: int) -> str:
    from flask import url_for

    return url_for("tratamientos.detalle", tratamiento_id=tratamiento_id)


# ------------------------------------------------------------- las herramientas


def contar_tratamientos(**argumentos) -> Resultado_:
    filtros = _filtros(argumentos)
    resumen = analitica.resumen(filtros)
    return Resultado_(
        datos={
            "ensayos": resumen.tratamientos,
            "clientes_distintos": resumen.clientes,
            "cultivos_distintos": resumen.cultivos,
            "productos_distintos": resumen.productos,
            "filas_de_producto_sin_normalizar": resumen.filas_sin_mapear,
        },
        fuentes=[
            Fuente(
                tipo="busqueda",
                etiqueta=f"Búsqueda: {', '.join(filtros.descripcion()) or 'sin filtros'}",
                url=analitica._enlace(filtros),
            )
        ],
        nota=(
            "Hay filas de producto sin normalizar en este recorte: los conteos por "
            "producto pueden quedar cortos."
            if resumen.filas_sin_mapear
            else ""
        ),
    )


def listar_tratamientos(**argumentos) -> Resultado_:
    filtros = _filtros(argumentos)
    limite = _entero(argumentos.get("limite")) or 10
    pagina = consultas.buscar(filtros, pagina=1, por_pagina=limite)

    filas, fuentes = [], []
    for tratamiento in pagina.items:
        productos = [
            (d.producto.nombre_comercial if d.producto else d.nombre_original)
            for m in tratamiento.mezclas
            for d in m.detalles
        ]
        filas.append(
            {
                "numero": tratamiento.numero_tratamiento,
                "fecha": tratamiento.fecha.isoformat() if tratamiento.fecha else None,
                "cliente": tratamiento.cliente.nombre if tratamiento.cliente else None,
                "cultivo": tratamiento.cultivo,
                "tipo": tratamiento.tipo_tratamiento,
                "mezcla": " + ".join(productos),
                "observacion": tratamiento.resultado.estado_resultado
                if tratamiento.resultado
                else None,
            }
        )
        fuentes.append(
            Fuente(
                tipo="tratamiento",
                etiqueta=tratamiento.numero_tratamiento,
                identificador=tratamiento.id,
                url=_url_tratamiento(tratamiento.id),
            )
        )

    return Resultado_(
        datos={"total_que_cumplen": pagina.total, "mostrados": filas},
        fuentes=fuentes,
        nota=(
            f"Se listan {len(filas)} de {pagina.total} ensayos que cumplen los criterios."
            if pagina.total > len(filas)
            else ""
        ),
    )


def detalle_de_tratamiento(**argumentos) -> Resultado_:
    numero = _texto(argumentos.get("numero"), 40)
    if not numero:
        raise ArgumentoInvalido("Falta el número de tratamiento.")

    tratamiento = db.session.scalar(
        select(Tratamiento).where(Tratamiento.numero_tratamiento == numero)
    )
    if tratamiento is None:
        return Resultado_(datos=None, nota=f"No existe el tratamiento {numero}.")

    completo = consultas.obtener_detalle(tratamiento.id)
    resultado = completo.resultado
    productos = [
        {
            "orden": d.orden_producto,
            "escrito_en_la_planilla": d.nombre_original,
            "producto_del_maestro": d.producto.nombre_comercial if d.producto else None,
            "activo": d.producto.activo if d.producto else None,
            "estado_normalizacion": d.estado_normalizacion.value,
        }
        for m in completo.mezclas
        for d in m.detalles
    ]

    return Resultado_(
        datos={
            "numero": completo.numero_tratamiento,
            "fecha": completo.fecha.isoformat() if completo.fecha else None,
            "cliente": completo.cliente.nombre if completo.cliente else None,
            "cultivo": completo.cultivo,
            "tipo": completo.tipo_tratamiento,
            "target": completo.target,
            "productos": productos,
            "mediciones": {
                "ph_agua": float(resultado.ph_agua) if resultado and resultado.ph_agua else None,
                "ph_mezcla": float(resultado.ph_mezcla) if resultado and resultado.ph_mezcla else None,
                "dureza_agua": float(resultado.dureza_agua) if resultado and resultado.dureza_agua else None,
                "observacion_cinco_min": resultado.observacion_cinco_min if resultado else None,
                "observacion_diez_min": resultado.observacion_diez_min if resultado else None,
            }
            if resultado
            else None,
        },
        fuentes=[
            Fuente(
                tipo="tratamiento",
                etiqueta=completo.numero_tratamiento,
                identificador=completo.id,
                url=_url_tratamiento(completo.id),
            )
        ],
    )


def ranking_de_productos(**argumentos) -> Resultado_:
    filtros = _filtros(argumentos)
    limite = _entero(argumentos.get("limite"), maximo=15) or 10
    serie = analitica.productos_mas_usados(filtros, limite=limite)
    resumen = analitica.resumen(filtros)

    return Resultado_(
        datos=[{"producto": p.etiqueta, "usos_en_mezclas": int(p.valor)} for p in serie.puntos],
        fuentes=[
            Fuente(tipo="busqueda", etiqueta="Ranking de productos", url=analitica._enlace(filtros))
        ],
        nota=(
            f"Atención: {resumen.filas_sin_mapear} fila(s) de producto de este recorte no "
            "están normalizadas, así que este ranking las subestima."
            if resumen.filas_sin_mapear
            else ""
        ),
    )


def distribucion_por(**argumentos) -> Resultado_:
    """Conteos agrupados por una dimensión, elegida de una lista cerrada."""
    dimension = _texto(argumentos.get("dimension"), 30).lower()
    permitidas = {
        "cultivo": analitica.por_cultivo,
        "tipo": analitica.por_tipo,
        "cliente": analitica.por_cliente,
        "observacion": analitica.observaciones_de_ensayo,
        "mes": analitica.por_periodo,
    }
    if dimension not in permitidas:
        raise ArgumentoInvalido(
            f"Dimensión no permitida. Las que hay son: {', '.join(sorted(permitidas))}."
        )

    filtros = _filtros(argumentos)
    serie = permitidas[dimension](filtros)
    return Resultado_(
        datos=[{"categoria": p.etiqueta, "ensayos": int(p.valor)} for p in serie.puntos],
        fuentes=[Fuente(tipo="busqueda", etiqueta=serie.titulo, url=analitica._enlace(filtros))],
        nota=serie.nota,
    )


def mediciones_de_laboratorio(**argumentos) -> Resultado_:
    filtros = _filtros(argumentos)
    promedios = analitica.promedios(filtros)
    return Resultado_(
        datos={
            "ph_agua_promedio": promedios.ph_agua,
            "ensayos_con_ph_agua": promedios.medidos_ph_agua,
            "ph_mezcla_promedio": promedios.ph_mezcla,
            "ensayos_con_ph_mezcla": promedios.medidos_ph_mezcla,
            "dureza_agua_promedio": promedios.dureza_agua,
            "ensayos_con_dureza": promedios.medidos_dureza,
        },
        fuentes=[
            Fuente(tipo="busqueda", etiqueta="Mediciones de laboratorio", url=analitica._enlace(filtros))
        ],
        nota="Cada promedio va con la cantidad de ensayos que lo sostienen.",
    )


def buscar_producto(**argumentos) -> Resultado_:
    texto = _texto(argumentos.get("texto"), 60)
    if not texto:
        raise ArgumentoInvalido("Falta el texto a buscar.")

    from flask import url_for

    # `buscar_productos` ya trae el conteo de usos: recalcularlo acá sería una
    # consulta por producto y, peor, otra forma de contar lo mismo.
    encontrados = consultas.buscar_productos(texto=texto)[:MAXIMO_FILAS]
    filas, fuentes = [], []
    for fila in encontrados:
        producto, usos = fila["producto"], fila["usos"]
        filas.append(
            {
                "nombre_comercial": producto.nombre_comercial,
                "activo": producto.activo,
                "categoria": producto.categoria,
                "marca": producto.marca,
                "estado": producto.estado.value,
                "usos_en_mezclas": usos,
            }
        )
        fuentes.append(
            Fuente(
                tipo="producto",
                etiqueta=producto.nombre_comercial,
                identificador=producto.id,
                url=url_for("productos.ficha", producto_id=producto.id),
            )
        )
    return Resultado_(datos=filas, fuentes=fuentes)


def estado_de_normalizacion(**argumentos) -> Resultado_:
    """Cuánto del repositorio está resuelto. Es el indicador de confianza del resto."""
    from . import indicadores

    salud = indicadores.salud_del_dato()
    pendientes = []
    from . import mapeo

    for pendiente in mapeo.listar_pendientes(por_pagina=8, con_sugerencias=False):
        pendientes.append({"nombre": pendiente.nombre, "filas_afectadas": pendiente.usos})

    return Resultado_(
        datos={
            "filas_de_producto": salud.total,
            "resueltas": salud.resueltos,
            "pendientes": salud.pendientes,
            "porcentaje_resuelto": salud.porcentaje_resuelto,
            "nombres_sin_resolver_mas_usados": pendientes,
        },
        fuentes=[Fuente(tipo="indicador", etiqueta="Salud del dato", url="/")],
    )


def productos_sin_normalizar(**argumentos) -> Resultado_:
    """Qué ensayos arrastran nombres sin resolver. Útil para saber qué no se puede afirmar."""
    limite = _entero(argumentos.get("limite")) or 10
    filas = db.session.execute(
        select(
            Tratamiento.id,
            Tratamiento.numero_tratamiento,
            DetalleMezcla.nombre_original,
        )
        .join(Mezcla, Mezcla.tratamiento_id == Tratamiento.id)
        .join(DetalleMezcla, DetalleMezcla.mezcla_id == Mezcla.id)
        .where(DetalleMezcla.estado_normalizacion == EstadoNormalizacion.PENDIENTE)
        .order_by(Tratamiento.fecha.desc().nullslast())
        .limit(limite)
    ).all()

    return Resultado_(
        datos=[{"tratamiento": numero, "nombre_sin_resolver": nombre} for _, numero, nombre in filas],
        fuentes=[
            Fuente(
                tipo="tratamiento",
                etiqueta=numero,
                identificador=identificador,
                url=_url_tratamiento(identificador),
            )
            for identificador, numero, _ in filas
        ],
    )


# --------------------------------------------------------------- el catálogo


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    descripcion: str
    parametros: dict
    funcion: Callable[..., Resultado_]

    def esquema(self) -> dict:
        """Formato de herramientas de la API de Anthropic."""
        return {
            "name": self.nombre,
            "description": self.descripcion,
            "input_schema": {
                "type": "object",
                "properties": {
                    clave: {"type": "string", "description": texto}
                    for clave, texto in self.parametros.items()
                },
                "required": [],
            },
        }


CATALOGO: dict[str, Herramienta] = {
    h.nombre: h
    for h in [
        Herramienta(
            nombre="contar_tratamientos",
            descripcion=(
                "Cuántos ensayos cumplen ciertos criterios, más cuántos clientes, cultivos y "
                "productos distintos hay. Usala para preguntas de «cuántos»."
            ),
            parametros=ESQUEMA_FILTROS,
            funcion=contar_tratamientos,
        ),
        Herramienta(
            nombre="listar_tratamientos",
            descripcion=(
                "Lista ensayos concretos con su número, fecha, cliente, cultivo y la mezcla "
                "completa. Usala cuando pidan ejemplos o «cuáles»."
            ),
            parametros={**ESQUEMA_FILTROS, "limite": "Cuántos listar, hasta 25. Opcional."},
            funcion=listar_tratamientos,
        ),
        Herramienta(
            nombre="detalle_de_tratamiento",
            descripcion=(
                "Todo lo de un ensayo puntual: composición de la mezcla y mediciones de "
                "laboratorio. Requiere el número exacto, por ejemplo «TR-0012»."
            ),
            parametros={"numero": "Número de tratamiento exacto."},
            funcion=detalle_de_tratamiento,
        ),
        Herramienta(
            nombre="ranking_de_productos",
            descripcion="Los productos más usados en mezclas dentro de un recorte.",
            parametros={**ESQUEMA_FILTROS, "limite": "Cuántos, hasta 15. Opcional."},
            funcion=ranking_de_productos,
        ),
        Herramienta(
            nombre="distribucion_por",
            descripcion=(
                "Conteo de ensayos agrupados por una dimensión: cultivo, tipo, cliente, "
                "observacion o mes."
            ),
            parametros={
                "dimension": "Una de: cultivo, tipo, cliente, observacion, mes.",
                **ESQUEMA_FILTROS,
            },
            funcion=distribucion_por,
        ),
        Herramienta(
            nombre="mediciones_de_laboratorio",
            descripcion="Promedios de pH de agua, pH de mezcla y dureza, con su cantidad de ensayos.",
            parametros=ESQUEMA_FILTROS,
            funcion=mediciones_de_laboratorio,
        ),
        Herramienta(
            nombre="buscar_producto",
            descripcion=(
                "Busca en el maestro por nombre comercial, principio activo o marca, e informa "
                "cuántas veces se usó cada uno."
            ),
            parametros={"texto": "Texto a buscar."},
            funcion=buscar_producto,
        ),
        Herramienta(
            nombre="estado_de_normalizacion",
            descripcion=(
                "Qué porcentaje del repositorio tiene los productos resueltos contra el maestro "
                "y cuáles son los nombres sin resolver más frecuentes."
            ),
            parametros={},
            funcion=estado_de_normalizacion,
        ),
        Herramienta(
            nombre="productos_sin_normalizar",
            descripcion="Qué ensayos concretos arrastran nombres de producto sin resolver.",
            parametros={"limite": "Cuántos listar, hasta 25. Opcional."},
            funcion=productos_sin_normalizar,
        ),
    ]
}


def esquemas() -> list[dict]:
    return [h.esquema() for h in CATALOGO.values()]


def ejecutar(nombre: str, argumentos: dict | None = None) -> Resultado_:
    """Ejecuta una herramienta del catálogo. Fuera del catálogo, no se ejecuta nada.

    Esta función es el único punto por donde el asistente toca la base. Si mañana
    hay que auditar qué puede hacer el modelo, se lee `CATALOGO` y se terminó.
    """
    herramienta = CATALOGO.get(nombre)
    if herramienta is None:
        raise ArgumentoInvalido(f"La herramienta «{nombre}» no existe.")

    argumentos = argumentos or {}
    if not isinstance(argumentos, dict):
        raise ArgumentoInvalido("Los argumentos tienen que venir como objeto.")

    # Se descartan los argumentos que la herramienta no declara: un parámetro
    # inventado por el modelo no puede llegar a la consulta.
    permitidos = {
        clave: valor for clave, valor in argumentos.items() if clave in herramienta.parametros
    }
    return herramienta.funcion(**permitidos)
