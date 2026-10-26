"""Piezas compartidas por las pruebas."""

from flask import g


def aislar_sesion_entre_clientes(aplicacion):
    """Evita que un cliente de prueba herede el usuario de otro.

    Flask-Login cachea el usuario resuelto en `g._login_user`. En producción cada
    petición trae su propio contexto de aplicación, así que ese caché dura una
    petición. En las pruebas, el fixture mantiene un contexto abierto para poder
    tocar la base, y Flask reutiliza ese contexto: el caché sobrevive de una
    petición a la siguiente y el segundo `test_client()` termina viendo al usuario
    del primero.

    Limpiarlo al inicio de cada petición reproduce el comportamiento real: la
    identidad se resuelve siempre desde la cookie de sesión.
    """

    @aplicacion.before_request
    def _limpiar_usuario_cacheado():
        g.pop("_login_user", None)

    return aplicacion


def rol_de(clave):
    """Devuelve el rol de la tabla, sembrando los de fábrica si hace falta.

    Desde la etapa 7 los roles son filas y no un enum, así que un test que crea un
    usuario necesita que el rol exista antes. Sembrar acá —y no en cada archivo—
    evita que ocho fixtures repitan lo mismo y se desincronicen.
    """
    from app.services import roles

    roles.sincronizar_roles()
    return roles.obtener(clave)
