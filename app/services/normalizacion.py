"""Normalización de nombres.

Es la pieza más chica del sistema y la que más decide su calidad: si dos formas de
escribir el mismo producto no colapsan en la misma clave, el maestro se ensucia y
todos los indicadores mienten.

Se resuelve en Python y no en la base para que el comportamiento sea idéntico en
SQLite y en PostgreSQL, y para poder testearlo sin levantar un motor.

Dos responsabilidades separadas a propósito:

* `clave()` decide **identidad**: dos textos con la misma clave son el mismo
  producto, sin intervención humana. Tiene que ser conservadora.
* `puntaje()` decide **parecido**: propone candidatos para que una persona
  confirme con un clic. Puede ser generosa, porque nada se aplica solo.
"""

from __future__ import annotations

import re
import unicodedata

# Ruido frecuente en planillas de laboratorio: presentaciones, envases y sufijos
# comerciales que no distinguen al producto.
_RUIDO = re.compile(
    r"\b(x\s?\d+\s?(lt|l|kg|gr|g|cc|ml)|bidon|bidón|envase|frasco|pack|s/?d)\b",
    re.IGNORECASE,
)
_NO_ALFANUM = re.compile(r"[^a-z0-9]+")
_SOLO_DIGITOS = re.compile(r"^\d+$")

# Palabras que aparecen en casi todos los nombres y no distinguen nada. Se ignoran
# al comparar (no al calcular la clave: ahí toda diferencia cuenta).
_VACIAS = frozenset({"de", "del", "la", "el", "y", "con", "para", "sc", "ec", "sl", "wg", "wp"})

LARGO_MINIMO_PREFIJO = 4   # "ciper" abrevia a "cipermetrina"; "cip" no abrevia nada
UMBRAL_SUGERENCIA = 0.55   # por debajo de esto no vale ni mostrarlo
UMBRAL_ALTA = 0.88
UMBRAL_MEDIA = 0.70
TECHO_CONCENTRACION = 0.60  # ver `puntaje`: dos concentraciones distintas nunca son "alta"


def quitar_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def clave(texto: str | None) -> str:
    """Devuelve la clave canónica de un nombre.

    >>> clave("  Round-Up  Full  x 20 Lt ")
    'round up full'
    >>> clave("ROUNDUP FULL") == clave("Round Up Full")
    False
    """
    if not texto:
        return ""
    valor = quitar_acentos(str(texto)).lower()
    valor = _RUIDO.sub(" ", valor)
    valor = _NO_ALFANUM.sub(" ", valor)
    return " ".join(valor.split())


def similitud(a: str, b: str) -> float:
    """Similitud 0..1 entre dos claves, para sugerir equivalencias.

    Usa distancia de Levenshtein normalizada. Es suficiente para listas de cientos
    de productos; si el maestro creciera a decenas de miles conviene mover esto a
    un índice trigram (pg_trgm) del lado de PostgreSQL.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        actual = [i]
        for j, cb in enumerate(b, start=1):
            actual.append(
                min(anterior[j] + 1, actual[j - 1] + 1, anterior[j - 1] + (ca != cb))
            )
        anterior = actual
    return 1.0 - anterior[-1] / max(len(a), len(b))


# ------------------------------------------------------------------- tokens


def tokens(clave_texto: str) -> list[str]:
    """Palabras significativas de una clave, sin las vacías."""
    return [t for t in clave_texto.split() if t not in _VACIAS]


def numeros(clave_texto: str) -> frozenset[str]:
    """Los tokens puramente numéricos: concentraciones, grados, dosis."""
    return frozenset(t for t in clave_texto.split() if _SOLO_DIGITOS.match(t))


def _parecido_token(a: str, b: str) -> float:
    """Cuánto se parecen dos palabras sueltas.

    Los tokens numéricos solo valen si son idénticos: en agroquímicos el número es
    la concentración, y 48 no es "casi" 62.
    """
    if a == b:
        return 1.0
    if _SOLO_DIGITOS.match(a) or _SOLO_DIGITOS.match(b):
        return 0.0
    if (
        len(a) >= LARGO_MINIMO_PREFIJO
        and len(b) >= LARGO_MINIMO_PREFIJO
        and (a.startswith(b) or b.startswith(a))
    ):
        # Abreviatura: "ciper" por "cipermetrina", "coadyuv" por "coadyuvante".
        return 0.85
    return 0.70 if similitud(a, b) >= 0.80 else 0.0


def _puntaje_tokens(a: str, b: str) -> float:
    """Solapamiento palabra a palabra, penalizando lo que sobra de cada lado.

    Dividir por el máximo de las dos longitudes y no por la cantidad de
    coincidencias es lo que evita que "Round Up" puntúe alto contra "Round Up Full
    Extra": las palabras que sobran del otro lado bajan el puntaje.
    """
    tokens_a, tokens_b = tokens(a), tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    total = sum(
        max((_parecido_token(ta, tb) for tb in tokens_b), default=0.0) for ta in tokens_a
    )
    return total / max(len(tokens_a), len(tokens_b))


def puntaje(a: str, b: str) -> float:
    """Parecido 0..1 entre dos claves, combinando tres lecturas del mismo par.

    Ninguna sola alcanza: Levenshtein no ve abreviaturas ("ciper 25" contra
    "cipermetrina 25" da 0,53), los tokens no ven errores de tipeo dentro de una
    palabra, y ninguno de los dos ve el caso de un nombre contenido en otro. Se
    toma el máximo de las tres lecturas y después se aplica el freno de concentración.

    El freno: si los dos nombres traen números y no son los mismos, el puntaje se
    corta en 0,60 por más que el resto del texto coincida. "Glifosato 48" y
    "Glifosato 62" comparten todas las letras y son productos distintos; confundirlos
    en el maestro es exactamente el error que este sistema existe para no cometer.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    contenido = 0.0
    if min(len(a), len(b)) >= 5 and (a in b or b in a):
        contenido = 0.88

    valor = max(similitud(a, b), _puntaje_tokens(a, b), contenido)

    numeros_a, numeros_b = numeros(a), numeros(b)
    if numeros_a and numeros_b and numeros_a != numeros_b:
        valor = min(valor, TECHO_CONCENTRACION)

    return round(valor, 4)


def confianza(valor: float) -> str:
    """Traduce un puntaje a las tres categorías que muestra la pantalla."""
    if valor >= UMBRAL_ALTA:
        return "alta"
    if valor >= UMBRAL_MEDIA:
        return "media"
    return "baja"


def sugerir(
    nombre: str, candidatos: dict[str, int], umbral: float = UMBRAL_SUGERENCIA
) -> list[tuple[int, float]]:
    """Ordena candidatos {clave_normalizada: id} por parecido al nombre dado."""
    objetivo = clave(nombre)
    puntajes = [
        (identificador, puntaje(objetivo, clave_candidata))
        for clave_candidata, identificador in candidatos.items()
    ]
    return sorted(
        [(i, p) for i, p in puntajes if p >= umbral], key=lambda par: par[1], reverse=True
    )
