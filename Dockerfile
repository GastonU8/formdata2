# Imagen de la aplicación.
#
# Se usa `slim` y no `alpine`: alpine usa musl en lugar de glibc y obliga a
# compilar desde fuente varias dependencias (argon2, psycopg), lo que agranda el
# tiempo de construcción y a veces rompe de maneras difíciles de diagnosticar.
# La diferencia de tamaño no compensa en un despliegue de una sola máquina.
FROM python:3.12-slim

# Sin .pyc en disco y con la salida sin buffer, para que los logs del contenedor
# aparezcan en el momento y no cuando se llena el buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app

WORKDIR /app

# Las dependencias se copian e instalan antes que el código: mientras
# requirements.txt no cambie, Docker reutiliza esta capa y reconstruir después de
# tocar una plantilla tarda segundos en lugar de minutos.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# La aplicación no corre como root. Si alguien encuentra una forma de ejecutar
# código dentro del contenedor, lo hace con un usuario sin privilegios.
RUN useradd --create-home --shell /bin/bash formdata \
    && mkdir -p /app/var/importaciones \
    && chown -R formdata:formdata /app
USER formdata

EXPOSE 8000

# Gunicorn y no el servidor de desarrollo de Flask: ese es de un solo hilo, no
# maneja bien las desconexiones y su propia documentación dice que no se use en
# producción.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-", "run:app"]
