"""Configuración por entorno.

Regla del proyecto: ningún secreto vive en el código. Todo lo sensible se lee
de variables de entorno (.env en desarrollo, variables del sistema en producción).
"""

import os
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


class ConfigBase:
    # --- Identidad ---
    NOMBRE_SISTEMA = "FormData"
    DESCRIPCION_SISTEMA = "Análisis y consulta de fórmulas de mezcla"

    # --- Seguridad ---
    SECRET_KEY = os.environ.get("FORMDATA_SECRET_KEY")

    SESSION_COOKIE_HTTPONLY = True          # el JS del navegador no puede leer la cookie
    SESSION_COOKIE_SAMESITE = "Lax"         # corta CSRF por navegación cruzada
    SESSION_COOKIE_NAME = "formdata_sesion"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = True

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8

    # Política de bloqueo de cuenta ante intentos fallidos
    MAX_INTENTOS_LOGIN = 5
    MINUTOS_BLOQUEO_LOGIN = 15

    # Política de contraseñas
    LARGO_MINIMO_PASSWORD = 12

    # --- Base de datos ---
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Importación de Excel ---
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024   # 25 MB por archivo
    EXTENSIONES_PERMITIDAS = {".xlsx", ".xlsm"}
    CARPETA_IMPORTACIONES = os.environ.get(
        "FORMDATA_CARPETA_IMPORTACIONES", str(RAIZ / "var" / "importaciones")
    )
    # Un histórico de miles de filas no entra en el tiempo de una petición HTTP.
    # En pruebas se procesa en línea para poder verificar el resultado.
    IMPORTACION_EN_SEGUNDO_PLANO = True

    # --- Asistente IA (etapa 4) ---
    IA_HABILITADA = os.environ.get("FORMDATA_IA_HABILITADA", "0") == "1"
    IA_PROVEEDOR = os.environ.get("FORMDATA_IA_PROVEEDOR", "ninguno")
    IA_MODELO = os.environ.get("FORMDATA_IA_MODELO", "")
    IA_API_KEY = os.environ.get("FORMDATA_IA_API_KEY", "")
    IA_MAX_FILAS_CONTEXTO = 200


class ConfigDesarrollo(ConfigBase):
    DEBUG = True
    SECRET_KEY = os.environ.get("FORMDATA_SECRET_KEY", "clave-solo-para-desarrollo")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "FORMDATA_DATABASE_URI", f"sqlite:///{RAIZ / 'var' / 'formdata.sqlite3'}"
    )
    SESSION_COOKIE_SECURE = False           # en dev no hay HTTPS


class ConfigPruebas(ConfigBase):
    TESTING = True
    SECRET_KEY = "clave-de-pruebas"
    # Por defecto SQLite en memoria: la suite corre en segundos y sin instalar nada.
    # Con FORMDATA_TEST_DATABASE_URI apuntando a PostgreSQL, la misma suite se corre
    # contra el motor de producción. Es la única forma de sostener que el sistema
    # funciona en los dos sin decirlo de memoria.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "FORMDATA_TEST_DATABASE_URI", "sqlite:///:memory:"
    )
    WTF_CSRF_ENABLED = False                # los tests postean sin token
    ARGON2_RAPIDO = True                    # ver extensions.configurar_hasher
    IMPORTACION_EN_SEGUNDO_PLANO = False
    SESSION_COOKIE_SECURE = False
    MAX_INTENTOS_LOGIN = 3


class ConfigProduccion(ConfigBase):
    DEBUG = False
    SESSION_COOKIE_SECURE = True            # solo se envía por HTTPS
    PREFERRED_URL_SCHEME = "https"
    SQLALCHEMY_DATABASE_URI = os.environ.get("FORMDATA_DATABASE_URI")

    def __init__(self) -> None:
        faltantes = [
            nombre
            for nombre in ("FORMDATA_SECRET_KEY", "FORMDATA_DATABASE_URI")
            if not os.environ.get(nombre)
        ]
        if faltantes:
            raise RuntimeError(
                "Faltan variables de entorno obligatorias: " + ", ".join(faltantes)
            )


ENTORNOS = {
    "desarrollo": ConfigDesarrollo,
    "pruebas": ConfigPruebas,
    "produccion": ConfigProduccion,
}


def obtener_config(nombre: str | None = None):
    nombre = nombre or os.environ.get("FORMDATA_ENTORNO", "desarrollo")
    if nombre not in ENTORNOS:
        raise RuntimeError(f"Entorno desconocido: {nombre}. Opciones: {list(ENTORNOS)}")
    config = ENTORNOS[nombre]
    return config() if nombre == "produccion" else config
