# FormData

Sistema de análisis y consulta de fórmulas de mezcla. Convierte el histórico de
tratamientos que hoy vive en Excel en un repositorio consultable, trazable y con control
de acceso.

**Estado: etapas 1 (cimientos), 2A (consulta), 2B (importación) y 2C (circuito) completas.** Ver el plan por etapas y el análisis de los
diagramas en [`DECISIONES.md`](DECISIONES.md).

---

## Poner a andar el proyecto

Requiere Python 3.11 o superior.

```bash
# 1 — entorno virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate        # Linux / macOS

# 2 — dependencias
pip install -r requirements.txt

# 3 — configuración
copy .env.example .env           # Windows   (cp en Linux/macOS)
python -c "import secrets; print(secrets.token_urlsafe(48))"
#   pegar el resultado en FORMDATA_SECRET_KEY dentro de .env

# 4 — base de datos
set FLASK_APP=run.py             # Windows   (export en Linux/macOS)
flask iniciar-bd

# 5 — tu usuario administrador
flask crear-usuario --nombre "Gastón Ugalde" --email gaston@ejemplo.com --rol administrador

# 6 — datos de ejemplo, para ver el tablero con contenido (opcional)
flask sembrar-demo

# 7 — a andar
python run.py
```

Abrir <http://127.0.0.1:5000>.

## Pruebas

```bash
pytest
```

272 pruebas. Cubren hash de contraseñas, bloqueo por intentos fallidos, no enumeración de
cuentas, permisos por rol, redirección segura tras login, auditoría, normalización de
nombres, todos los filtros de búsqueda con sus combinaciones, y el circuito completo:
transiciones válidas e inválidas, permisos por rol, propiedad de la solicitud, rechazo
sin motivo y finalización sin resultado; la importación: encabezados con basura,
filas fantasma, decimales con coma, columnas del Excel que resuelven mal, y reimportación
idempotente; y la normalización asistida: agrupación por clave, sugerencias por tipeo,
abreviatura y principio activo, el freno que impide confundir dos concentraciones,
propagación de una decisión a todas sus filas, deshacer, permisos y CSRF con la
validación realmente activa, y ABM del maestro con rechazo de duplicados y fusión; y los
indicadores: que el tablero y la búsqueda cuenten lo mismo en ocho combinaciones de
filtros, que cada barra enlace a la lista que la explica sin perder el filtro activo,
la geometría de los gráficos, el escapado de nombres con HTML dentro del SVG, y que el
Excel exporte exactamente el recorte filtrado con sus criterios adelante; y el
asistente: que una herramienta fuera del catálogo no se ejecute, que los argumentos
que manda el modelo se validen antes de tocar la base, que una respuesta con cifras
que no están en los datos quede marcada para revisar, que si el modelo contesta sin
consultar nada no se le crea, y que el motor no entre en bucle. Ninguna prueba llama
a una API externa; y la administración: que un usuario no se borre ni se recicle su
email, que el sistema no pueda quedarse sin administradores, y que la tabla de
etiquetas de la bitácora no se desincronice de las acciones que el código emite.

Las mismas pruebas corren en los dos motores. Contra PostgreSQL:

```bash
FORMDATA_TEST_DATABASE_URI=postgresql+psycopg://usuario:clave@localhost/formdata_test \
  python -m pytest
```

## El circuito de solicitudes

```
BORRADOR ──enviar──▶ PENDIENTE ──aprobar──▶ APROBADA ──tomar──▶ EN ENSAYO ──▶ FINALIZADA
   ▲                     │                                                        │
   └────corregir──── RECHAZADA                                              queda en el
                                                                        histórico consultable
```

| Etapa | Quién | Qué hace |
|---|---|---|
| Registrar y enviar | Comercial | Carga la mezcla que pidió el cliente |
| Aprobar o rechazar | **Supervisor** | Rechazar exige motivo escrito |
| Tomar en laboratorio | Técnico | La solicitud entra a la cola de ensayo |
| Cargar resultados | Técnico | Al guardar, el tratamiento queda finalizado |

Los tratamientos que entran por importación de Excel nacen directamente en `FINALIZADA`:
ya ocurrieron, no recorren el circuito. Después son indistinguibles en la búsqueda.

## Pantallas

| Ruta | Qué hace | Quién entra |
|---|---|---|
| `/importacion/` | Carga del Excel histórico e historial de importaciones | técnico |
| `/normalizacion/` | Bandeja de productos sin mapear: sugerencias, confirmar, descartar, deshacer | técnico |
| `/solicitudes/` | Bandeja del circuito, filtrada por lo que le toca a cada rol | todos |
| `/solicitudes/nueva` | Alta de mezcla pedida por un cliente | comercial |
| `/solicitudes/<id>` | Estado, historial y acciones disponibles | todos |
| `/solicitudes/<id>/resultado` | Carga de mediciones y cierre del ensayo | técnico |
| `/` | Tablero filtrable: normalización, cifras, gráficos, últimas importaciones | todos |
| `/exportar/excel`, `/exportar/pdf` | Descarga del recorte visible, con sus criterios | todos |
| `/asistente/` | Preguntas en lenguaje natural, respondidas solo con datos del sistema | todos |
| `/administracion/roles` | Crear roles y configurar qué puede hacer cada uno | gestionar_roles |
| `/administracion/usuarios` | Alta, edición, suspensión y baja de usuarios | gestionar_usuarios |
| `/administracion/auditoria` | Bitácora de acciones sensibles, con filtros | ver_auditoria |
| `/tratamientos/` | Búsqueda por texto, producto o activo, cultivo, tipo, cliente, resultado, fechas | todos |
| `/tratamientos/<id>` | Ficha: composición de la mezcla, mediciones, origen del dato, ensayos relacionados | todos |
| `/productos/` | Maestro por nombre, activo, marca, registro o categoría | todos |
| `/productos/<id>` | Ficha: usos, grafías con las que se escribió, duplicados a fusionar | todos |
| `/productos/nuevo`, `/productos/<id>/editar` | ABM del maestro | técnico |
| `/acceso/` | Ingreso | — |

## Preguntarle al asistente

En `/asistente/` se pregunta en lenguaje natural sobre los ensayos. Tres cosas que
conviene saber antes de usarlo:

- **Responde solo con datos del sistema.** Si no encuentra nada, lo dice; no completa
  con conocimiento general de agroquímicos.
- **Cada respuesta muestra de dónde salió**, con enlaces a los ensayos o productos que
  la sustentan. Están para que puedas ir a comprobar, no de adorno.
- **Si menciona una cifra que no está en los datos consultados, la respuesta queda
  marcada para revisar** y te lo avisa arriba de todo.

Debajo de cada respuesta, *Qué consultó el asistente* muestra qué herramientas usó y
con qué argumentos. El asistente no escribe consultas a la base: elige de una lista
fija de nueve consultas ya programadas y revisadas.

**Sin clave de API también funciona.** En ese caso trabaja en «modo sin modelo»:
enruta la pregunta por palabras clave a las mismas herramientas y muestra los datos
tal cual. La pantalla avisa en qué modo está. Para activar un modelo, en el `.env`:

```
FORMDATA_IA_PROVEEDOR=anthropic
FORMDATA_IA_MODELO=claude-sonnet-4-5
FORMDATA_IA_API_KEY=tu-clave
```

El asistente informa lo que dicen los ensayos registrados. No recomienda mezclas ni
opinar sobre aplicación: eso lo decide el técnico.

## Sacar informes

El tablero (`/`) y la búsqueda (`/tratamientos/`) comparten los mismos filtros. Elegí el
recorte que te interese —producto o activo, cultivo, tipo, cliente, rango de fechas— y:

- Los gráficos se recalculan sobre ese recorte. **Cada barra es un enlace**: al hacer
  clic se abre la lista de ensayos que la componen, con el filtro que ya tenías puesto.
- **Ver los valores**, debajo de cada gráfico, despliega la tabla con los números
  exactos y los porcentajes, para copiar y pegar.
- **Exportar a Excel** baja cuatro hojas: los criterios usados, el resumen, una hoja
  ancha (un ensayo por fila) y una larga (un producto por fila, lista para tabla
  dinámica).
- **Informe en PDF** arma algo para circular: criterios, cifras, gráficos y las primeras
  40 filas.
- Si preferís papel, `Ctrl+P` sobre el tablero imprime sin la barra lateral ni los
  controles.

Lo que se descarga es siempre **lo que estás mirando**, no todo el repositorio. Y el
archivo abre diciendo quién lo generó, cuándo y con qué filtros: una cifra que no se
puede reproducir no se puede discutir.

Una advertencia que el tablero muestra solo: si el recorte tiene filas de producto sin
normalizar, los rankings de producto las subestiman y aparece un aviso con el enlace a la
bandeja. Conviene resolverlas antes de mandar un informe afuera.

## Normalizar los productos sin mapear

Después de importar el Excel, el tablero muestra la probeta con el porcentaje de
normalización. Todo lo que esté pendiente son tratamientos que **no aparecen** cuando
alguien busca por principio activo o categoría. Para resolverlo:

1. Entrá a **Sin mapear** en la barra lateral (el número al lado es cuánto falta).
2. Cada tarjeta es un nombre de la planilla. Arriba dice en cuántas filas de mezcla
   aparece y en qué tratamientos, para que puedas decidir con contexto.
3. Elegí una de las salidas:
   - **Es este** sobre una sugerencia — la lista viene ordenada por parecido, con el
     porcentaje y una barra. Verde es confianza alta, ámbar es media: la ámbar leela
     antes de confirmar.
   - **Buscar otro producto** si ninguna sugerencia sirve.
   - **Darlo de alta en el maestro** si el producto existe pero nunca se cargó. Se crea
     y se asocia en un solo paso, sin salir de la pantalla.
   - **No es un producto** para celdas con `s/d`, una dosis suelta o texto que no
     corresponde a nada.
4. La tarjeta se reemplaza por el resultado, con **Deshacer** al lado. Todo lo resuelto
   queda en la pestaña *Resueltos*, donde también se puede revertir.

La bandeja ordena por cantidad de filas afectadas, así que empezando por arriba subís la
salud del dato con la menor cantidad de clics. Cada decisión queda guardada como regla:
la próxima importación del histórico ya no vuelve a preguntar por ese nombre.

---

## Comandos disponibles

| Comando | Qué hace |
|---|---|
| `flask iniciar-bd` | Crea las tablas. Para cambios de esquema posteriores: `flask db migrate` + `flask db upgrade`. |
| `flask crear-usuario` | Alta de usuario desde consola, con contraseña oculta. |
| `flask sembrar-demo` | Carga tratamientos de ejemplo. Solo corre sobre una base vacía. |

---

## Cómo está organizado

```
app/
├── __init__.py          fábrica de aplicación, errores, filtros, comandos
├── config.py            configuración por entorno (desarrollo / pruebas / producción)
├── extensions.py        instancias de SQLAlchemy, Login, CSRF, límite de tasa, Argon2
├── models/              entidades del dominio
│   ├── usuario.py       credenciales, roles, política de bloqueo
│   ├── catalogo.py      Cliente, Producto, MapeoProducto
│   ├── tratamiento.py   ArchivoImportado, Tratamiento, Mezcla, DetalleMezcla, Resultado
│   └── analitica.py     Indicador, ConsultaIA, RegistroAuditoria
├── security/
│   ├── roles.py         capacidades por rol y decorador @requiere
│   └── auditoria.py     bitácora y cabeceras HTTP de seguridad
├── services/
│   ├── normalizacion.py clave canónica de nombres y puntaje de parecido
│   ├── analitica.py     series de indicadores sobre el conjunto filtrado
│   ├── graficos.py      SVG generado en el servidor, sin JavaScript
│   ├── exportacion.py   libro de Excel e informe en PDF
│   ├── herramientas.py  catálogo cerrado de consultas que puede pedir el asistente
│   ├── proveedor_ia.py  abstracción del modelo: Anthropic, sin modelo y falso
│   ├── asistente.py     motor: herramientas → respuesta verificada → registro
│   ├── usuarios.py      ABM de usuarios con las reglas de la auditoría
│   ├── roles.py         ABM de roles y permisos, con las salvaguardas
│   └── auditoria.py     consulta de la bitácora (solo lectura)
│   ├── mapeo.py         bandeja de pendientes, sugerencias y decisiones de mapeo
│   ├── maestro.py       ABM de productos, fusión de duplicados y ficha
│   ├── lector_excel.py  lectura tolerante del .xlsm (no conoce la base)
│   ├── importacion.py   carga idempotente de lo leído (no conoce openpyxl)
│   ├── flujo.py         máquina de estados del circuito de solicitudes
│   ├── solicitudes.py   alta de solicitudes y carga de resultados
│   ├── indicadores.py   métricas del tablero
│   └── consultas.py     filtros, paginación y detalle de tratamientos
├── blueprints/
│   ├── auth/            ingreso, salida, cambio de contraseña
│   ├── panel/           tablero
│   ├── importacion/     carga de Excel y resultado de importación
│   ├── normalizacion/   bandeja de productos sin mapear
│   ├── asistente/       consulta en lenguaje natural
│   ├── admin/           usuarios y bitácora
│   ├── solicitudes/     bandeja, alta, transiciones y resultados
│   ├── tratamientos/    búsqueda con filtros y ficha del ensayo
│   └── productos/       maestro y su ABM, con salto a los tratamientos de cada producto
├── templates/
└── static/
    ├── css/app.css      sistema visual, sin framework
    └── js/htmx-*.min.js servido desde la app, no desde una CDN
```

## Base de datos

En desarrollo usa SQLite y no hay nada que instalar. Para producción, en `.env`:

```
FORMDATA_ENTORNO=produccion
FORMDATA_DATABASE_URI=postgresql+psycopg://formdata:CLAVE@localhost:5432/formdata
```

El código no depende del motor: nada específico de PostgreSQL está en la lógica de
negocio. En entorno `produccion` la aplicación se niega a arrancar si falta
`FORMDATA_SECRET_KEY` o `FORMDATA_DATABASE_URI`.

## Antes de mostrarlo fuera de tu máquina

- Servir con `gunicorn` o `waitress`, nunca con el servidor de desarrollo.
- HTTPS por delante (nginx o Caddy): recién ahí la cookie `Secure` y HSTS tienen efecto.
- Mover el límite de tasa a Redis: en memoria no sobrevive a varios procesos.

---

## Desplegar con Docker

```bash
cp .env.example .env
# Completar en el .env:
#   FORMDATA_SECRET_KEY   → python -c "import secrets; print(secrets.token_urlsafe(48))"
#   POSTGRES_PASSWORD     → una clave larga

docker compose up -d --build

# Crear las tablas y el primer administrador:
docker compose exec aplicacion flask iniciar-bd
docker compose exec aplicacion flask crear-usuario --rol administrador
```

Queda en `http://localhost:8000`. La base **no publica puertos**: solo la alcanza la
aplicación por la red interna de Compose.

Los datos viven en volúmenes con nombre (`datos_postgres`, `archivos_importados`), así
que `docker compose down` no los borra. Para borrarlos hace falta `down -v`.

Para actualizar:

```bash
docker compose up -d --build
docker compose exec aplicacion flask db upgrade   # si hubo cambios de esquema
```
