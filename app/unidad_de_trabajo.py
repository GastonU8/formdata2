"""Unidad de trabajo: una operación de negocio, una transacción.

El problema que resuelve
------------------------
Antes de esto, cada servicio confirmaba por su cuenta y, cuando una operación
componía a otra, había que avisarle a la de adentro que **no** confirmara pasándole
un `confirmar=False`. Ese booleano viajando entre servicios era una unidad de
trabajo simulada a mano, con dos problemas concretos:

* **Se olvida.** Quien escribe un servicio nuevo no tiene por qué saber que existe
  el flag. Un `commit()` de más en el medio de una operación compuesta deja la mitad
  del trabajo escrita y la otra mitad no.
* **Obliga a deshacer a mano.** Las vistas terminaban llamando a `rollback()` cuando
  el servicio fallaba, que es lógica de transacción escrita en la capa HTTP.

Con `transaccion()`, quien inicia la operación decide dónde empieza y dónde termina;
los servicios de adentro no saben si son la operación entera o parte de otra.

Reentrante a propósito
----------------------
`solicitudes.registrar()` llama a `flujo.ejecutar()`. Los dos abren su transacción,
pero solo la de más afuera confirma: la de adentro se suma a la que ya está abierta.
Así una operación compuesta es atómica sin que ninguna de sus partes tenga que
enterarse de quién la llamó.

Por qué la auditoría va adentro
-------------------------------
El registro de auditoría se escribe en la misma transacción que la acción que
registra. Si fuera una transacción aparte, podría quedar una acción sin registro
—o peor, un registro de algo que después se deshizo—. En un sistema donde la
auditoría existe para poder responder «quién tocó esto», eso la volvería inservible.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from .extensions import db

_profundidad = threading.local()


def _nivel() -> int:
    return getattr(_profundidad, "valor", 0)


def en_transaccion() -> bool:
    """Si ya hay una unidad de trabajo abierta en este hilo."""
    return _nivel() > 0


@contextmanager
def transaccion():
    """Agrupa todo lo que pase adentro en una sola transacción.

    Al salir sin error confirma; ante cualquier excepción deshace y la vuelve a
    lanzar, para que la capa de arriba decida qué mostrar. Nunca se traga un error:
    una operación que falla en silencio es peor que una que falla.

        with transaccion():
            producto = maestro.crear(datos, usuario)
            mapeo.confirmar(clave, producto.id, usuario)

    Las dos escrituras entran juntas o no entra ninguna.
    """
    _profundidad.valor = _nivel() + 1
    externa = _nivel() == 1
    try:
        yield db.session
    except Exception:
        if externa:
            db.session.rollback()
        raise
    else:
        if externa:
            db.session.commit()
    finally:
        _profundidad.valor = _nivel() - 1
