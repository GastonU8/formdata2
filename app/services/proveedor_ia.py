"""De dónde sale la inteligencia del asistente.

El motor no sabe qué modelo hay detrás: le pide a un proveedor que, dada una
conversación y el catálogo de herramientas, decida una de dos cosas —usar una
herramienta o responder—. Esa frontera vale por tres razones concretas:

* **Se puede probar sin red ni clave.** Los tests usan `ProveedorFalso`, que decide
  lo que el test le dijo. Un asistente cuyas pruebas dependen de una API externa no
  se puede probar en la defensa ni en una máquina sin internet.
* **Se puede demostrar sin contratar nada.** `ProveedorSinModelo` resuelve las
  preguntas frecuentes con reglas. No es un modelo de lenguaje y no pretende serlo:
  es la garantía de que la pantalla funciona el día de la presentación aunque no
  haya clave cargada, y de que el resto del sistema (herramientas, fuentes,
  validación, auditoría) queda igualmente demostrado.
* **Cambiar de proveedor es cambiar una clase.** El día que convenga otro modelo, o
  uno local, el motor no se toca.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

TIEMPO_LIMITE = 45


# ------------------------------------------------------------------ decisiones


@dataclass(frozen=True)
class UsarHerramienta:
    nombre: str
    argumentos: dict = field(default_factory=dict)
    identificador: str = "1"


@dataclass(frozen=True)
class Responder:
    texto: str


Decision = UsarHerramienta | Responder


class ErrorDelProveedor(Exception):
    """Falló la comunicación con el modelo. El motor lo convierte en un aviso claro."""


@dataclass
class Uso:
    entrada: int = 0
    salida: int = 0


class Proveedor:
    nombre = "base"
    modelo = ""

    def decidir(self, sistema: str, mensajes: list[dict], esquemas: list[dict]) -> Decision:
        raise NotImplementedError

    @property
    def uso(self) -> Uso:
        return Uso()


# ------------------------------------------------------------------ Anthropic


class ProveedorAnthropic(Proveedor):
    """Cliente HTTP mínimo contra la API de mensajes de Anthropic.

    Se habla HTTP directo en lugar de sumar el SDK: son treinta líneas, evita una
    dependencia más en `requirements.txt` y deja a la vista qué se manda —que en un
    proyecto donde importa qué datos salen del sistema no es un detalle menor.
    """

    nombre = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"
    VERSION = "2023-06-01"

    def __init__(self, api_key: str, modelo: str, max_tokens: int = 1200):
        if not api_key:
            raise ErrorDelProveedor("Falta la clave de la API.")
        self.api_key = api_key
        self.modelo = modelo or "claude-sonnet-4-5"
        self.max_tokens = max_tokens
        self._uso = Uso()

    @property
    def uso(self) -> Uso:
        return self._uso

    def decidir(self, sistema: str, mensajes: list[dict], esquemas: list[dict]) -> Decision:
        import urllib.error
        import urllib.request

        cuerpo = json.dumps(
            {
                "model": self.modelo,
                "max_tokens": self.max_tokens,
                "system": sistema,
                "messages": mensajes,
                "tools": esquemas,
            }
        ).encode("utf-8")

        peticion = urllib.request.Request(
            self.URL,
            data=cuerpo,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.VERSION,
            },
        )

        try:
            with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE) as respuesta:
                datos = json.loads(respuesta.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # El cuerpo del error puede traer la clave o datos de la consulta: se
            # informa el código y nada más.
            raise ErrorDelProveedor(f"El modelo respondió con error {error.code}.") from None
        except (urllib.error.URLError, TimeoutError):
            raise ErrorDelProveedor("No se pudo contactar al modelo.") from None
        except json.JSONDecodeError:
            raise ErrorDelProveedor("El modelo devolvió una respuesta ilegible.") from None

        uso = datos.get("usage") or {}
        self._uso = Uso(
            entrada=self._uso.entrada + (uso.get("input_tokens") or 0),
            salida=self._uso.salida + (uso.get("output_tokens") or 0),
        )
        return self._interpretar(datos)

    @staticmethod
    def _interpretar(datos: dict) -> Decision:
        bloques = datos.get("content") or []
        for bloque in bloques:
            if bloque.get("type") == "tool_use":
                return UsarHerramienta(
                    nombre=str(bloque.get("name", "")),
                    argumentos=bloque.get("input") or {},
                    identificador=str(bloque.get("id", "1")),
                )
        texto = " ".join(b.get("text", "") for b in bloques if b.get("type") == "text").strip()
        return Responder(texto or "No pude armar una respuesta con los datos del sistema.")


# --------------------------------------------------------------- sin modelo


def _clave(texto: str) -> str:
    limpio = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in limpio if not unicodedata.combining(c))


# Cada regla: palabras que la disparan, herramienta y extractor de argumentos.
# El orden importa: la primera que coincide gana, así que van de más específica a
# más general.
REGLAS = [
    (("sin mapear", "sin normalizar", "sin resolver", "normalizacion", "normalizado", "pendiente"),
     "estado_de_normalizacion"),
    (("producto mas", "productos mas", "mas usado", "mas usados", "ranking"), "ranking_de_productos"),
    (("ph", "dureza", "medicion", "mediciones", "promedio"), "mediciones_de_laboratorio"),
    (("por cultivo", "por tipo", "por cliente", "por mes", "distribucion", "reparto"), "distribucion_por"),
    (("cuantos", "cuantas", "cantidad", "total"), "contar_tratamientos"),
    (("cuales", "listame", "lista", "mostrame", "ejemplos", "dame"), "listar_tratamientos"),
]

# El prefijo tiene que estar seguido de un dígito. Sin esa exigencia, «trigo» se
# leía como el tratamiento «TR-IGO» y una pregunta por cultivo terminaba pidiendo
# el detalle de un ensayo inexistente.
_NUMERO_TRATAMIENTO = re.compile(r"\b((?:tr|sol)[-\s]?\d[\w-]*)\b", re.IGNORECASE)


class ProveedorSinModelo(Proveedor):
    """Enruta la pregunta a una herramienta por palabras clave, sin modelo de lenguaje.

    No entiende lenguaje natural: reconoce formas de preguntar. Se llama «sin
    modelo» y no «asistente» a propósito —la pantalla avisa que está funcionando en
    este modo— porque presentarlo como si razonara sería exactamente el tipo de
    exageración que este proyecto viene evitando en el resto de las etapas.
    """

    nombre = "sin_modelo"
    modelo = "reglas"

    def __init__(self, cultivos: list[str] | None = None, tipos: list[str] | None = None):
        self.cultivos = cultivos or []
        self.tipos = tipos or []
        self._ya_uso_herramienta = False

    def decidir(self, sistema: str, mensajes: list[dict], esquemas: list[dict]) -> Decision:
        pregunta = self._ultima_pregunta(mensajes)

        if self._ya_uso_herramienta:
            # Segunda vuelta: los datos ya están en la conversación. Este proveedor
            # no redacta: el motor arma el texto a partir de lo que devolvió la
            # herramienta, que es la única fuente que hay.
            return Responder("")

        self._ya_uso_herramienta = True
        clave = _clave(pregunta)
        permitidas = {e["name"] for e in esquemas}

        encontrado = _NUMERO_TRATAMIENTO.search(pregunta)
        if encontrado and "detalle_de_tratamiento" in permitidas:
            return UsarHerramienta(
                "detalle_de_tratamiento", {"numero": encontrado.group(1).upper().replace(" ", "-")}
            )

        for palabras, herramienta in REGLAS:
            if any(palabra in clave for palabra in palabras) and herramienta in permitidas:
                return UsarHerramienta(herramienta, self._argumentos(herramienta, clave, pregunta))

        return UsarHerramienta("contar_tratamientos", self._argumentos("contar_tratamientos", clave, pregunta))

    def _argumentos(self, herramienta: str, clave: str, pregunta: str) -> dict:
        argumentos: dict[str, Any] = {}

        for cultivo in self.cultivos:
            if _clave(cultivo) in clave:
                argumentos["cultivo"] = cultivo
                break
        for tipo in self.tipos:
            if _clave(tipo) in clave:
                argumentos["tipo_tratamiento"] = tipo
                break

        if herramienta == "distribucion_por":
            for palabra, dimension in [
                ("cultivo", "cultivo"), ("tipo", "tipo"), ("cliente", "cliente"),
                ("mes", "mes"), ("observacion", "observacion"), ("resultado", "observacion"),
            ]:
                if palabra in clave:
                    argumentos["dimension"] = dimension
                    break
            argumentos.setdefault("dimension", "cultivo")

        if herramienta == "listar_tratamientos":
            argumentos["limite"] = 10

        return argumentos

    @staticmethod
    def _ultima_pregunta(mensajes: list[dict]) -> str:
        for mensaje in reversed(mensajes):
            if mensaje.get("role") != "user":
                continue
            contenido = mensaje.get("content")
            if isinstance(contenido, str):
                return contenido
            for bloque in contenido or []:
                if isinstance(bloque, dict) and bloque.get("type") == "text":
                    return bloque.get("text", "")
        return ""


# -------------------------------------------------------------- para pruebas


class ProveedorFalso(Proveedor):
    """Devuelve las decisiones que le carga el test, en orden."""

    nombre = "falso"
    modelo = "prueba"

    def __init__(self, decisiones: list[Decision]):
        self.decisiones = list(decisiones)
        self.llamadas: list[list[dict]] = []
        self.sistemas: list[str] = []

    def decidir(self, sistema: str, mensajes: list[dict], esquemas: list[dict]) -> Decision:
        self.llamadas.append(mensajes)
        self.sistemas.append(sistema)
        if not self.decisiones:
            return Responder("Sin más decisiones cargadas.")
        return self.decisiones.pop(0)


# ------------------------------------------------------------------- fábrica


def construir(configuracion, cultivos=None, tipos=None) -> Proveedor:
    """Elige el proveedor según la configuración, sin que el motor se entere."""
    proveedor = (configuracion.get("IA_PROVEEDOR") or "ninguno").lower()

    if proveedor == "anthropic":
        return ProveedorAnthropic(
            api_key=configuracion.get("IA_API_KEY", ""),
            modelo=configuracion.get("IA_MODELO", ""),
        )
    return ProveedorSinModelo(cultivos=cultivos, tipos=tipos)
