"""Punto de entrada de desarrollo: `python run.py`."""

from dotenv import load_dotenv

load_dotenv()

from app import crear_app  # noqa: E402  (después de cargar el .env)

app = crear_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])
