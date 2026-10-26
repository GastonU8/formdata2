"""Instancias únicas de las extensiones. Se inicializan en la fábrica de aplicación."""

from argon2 import PasswordHasher
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()

# Argon2id: es el algoritmo recomendado hoy para contraseñas (ganador del PHC).
# Los parámetros son un equilibrio entre costo de cómputo y experiencia de login:
# unos 220 ms por hash, suficiente para que probar contraseñas por fuerza bruta sea
# caro sin que el ingreso se sienta lento.
PARAMETROS_FUERTES = dict(time_cost=3, memory_cost=64 * 1024, parallelism=2)
PARAMETROS_RAPIDOS = dict(time_cost=1, memory_cost=8 * 1024, parallelism=1)

hasher = PasswordHasher(**PARAMETROS_FUERTES)


def configurar_hasher(app) -> None:
    """Baja el costo del hash **solo en la suite de pruebas**.

    Esos 220 ms son deliberados en producción, pero la suite crea usuarios e ingresa
    cientos de veces: con los parámetros reales, más de la mitad del tiempo de los
    tests es Argon2 haciendo exactamente aquello para lo que fue diseñado. Con los
    parámetros bajos la suite corre en un tercio del tiempo, y una suite que tarda
    poco es una suite que se corre seguido.

    Lo que se prueba no cambia: el algoritmo, el formato del hash y la verificación
    son los mismos; solo varía cuánto trabajo cuesta calcularlo. Se activa desde la
    configuración de pruebas y en ningún otro entorno.
    """
    global hasher

    # Se asigna siempre, en las dos ramas. Con un `if` sin `else`, un proceso que
    # creara primero una aplicación de pruebas y después una de producción se
    # quedaría con el hash débil: improbable, pero es exactamente la clase de error
    # que nadie nota hasta que ya está en producción.
    hasher = PasswordHasher(
        **(PARAMETROS_RAPIDOS if app.config.get("ARGON2_RAPIDO") else PARAMETROS_FUERTES)
    )

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["600 per hour"],
    storage_uri="memory://",
)

login_manager.login_view = "auth.login"
login_manager.login_message = "Iniciá sesión para entrar a FormData."
login_manager.login_message_category = "aviso"
login_manager.session_protection = "strong"
