# Gestión de configuración del software

Documento del ítem (j) del Examen Parcial 2. Define qué se versiona, cómo se
identifican los cambios y cómo se reproduce un entorno de trabajo.

---

## 1. Ítems de configuración

Un *ítem de configuración* es todo artefacto cuyo cambio puede alterar el
comportamiento del sistema y que, por lo tanto, se versiona y se identifica.

| Categoría | Ítems | Dónde |
|---|---|---|
| Código fuente | Aplicación completa (modelos, servicios, vistas, plantillas, estilos) | `app/` |
| Pruebas | 272 pruebas automatizadas agrupadas por etapa | `tests/` |
| Dependencias | Versiones exactas de bibliotecas | `requirements.txt` |
| Configuración de entorno | Plantilla de variables, sin secretos | `.env.example` |
| Datos de demostración | Comando reproducible de carga | `app/demo.py` |
| Despliegue | Imagen y orquestación | `Dockerfile`, `docker-compose.yml` |
| Documentación | Arquitectura, decisiones, manual | `*.md` |

**Lo que deliberadamente NO se versiona** (declarado en `.gitignore`):

```
__pycache__/   *.py[cod]      → artefactos de compilación, se regeneran
.venv/  venv/                 → el entorno se reconstruye desde requirements.txt
.env                          → contiene secretos: clave de sesión, credenciales
var/    *.sqlite3             → base de datos local y archivos importados
.pytest_cache/                → caché de la herramienta de pruebas
```

El criterio es uniforme: **no se versiona lo que se puede regenerar ni lo que es
secreto**. Un `.env` versionado publica la clave de sesión en el historial, y
borrarlo después no sirve porque el historial lo conserva.

---

## 2. Control de versiones

### Repositorio

```bash
git init
git add .
git commit -m "chore: estructura del proyecto y dependencias"
```

El repositorio se inicializó al consolidar el proyecto y su historial está
**organizado por etapas de construcción**, no por sesión de trabajo. Son 23
commits que reconstruyen el orden en que se levantó el sistema: cimientos,
seguridad, circuito, importación, normalización, indicadores, asistente y
administración.

### Convención de mensajes — Conventional Commits

```
<tipo>(<alcance>): <qué cambia, en imperativo>
```

| Tipo | Cuándo |
|---|---|
| `feat` | Funcionalidad nueva visible para el usuario |
| `fix` | Corrección de un defecto |
| `test` | Pruebas nuevas o modificadas |
| `docs` | Documentación |
| `refactor` | Cambio interno sin alterar el comportamiento |
| `chore` | Dependencias, configuración, despliegue |

El alcance es el módulo afectado: `cimientos`, `circuito`, `importacion`,
`normalizacion`, `indicadores`, `asistente`, `admin`, `seguridad`.

Ejemplos del historial real:

```
feat(circuito): máquina de estados de la solicitud de mezcla
feat(admin): roles y permisos editables desde la aplicación
chore(deploy): contenedor de aplicación y orquestación con PostgreSQL
```

La convención no es decorativa: permite responder «¿qué cambió en el circuito?»
con `git log --oneline --grep "circuito"` en vez de leer 21 mensajes.

### Estrategia de ramas

Para un equipo de una persona, un esquema de rama por funcionalidad sobre
`main`:

```
main                    siempre con la suite en verde; es lo que se despliega
 └── feat/<alcance>      una funcionalidad; se integra cuando sus pruebas pasan
 └── fix/<descripcion>   corrección puntual
```

La regla que sostiene el esquema: **nada se integra a `main` con pruebas en
rojo**. `main` es la referencia de qué funciona.

### Publicar en GitHub

```bash
# 1. Crear el repositorio vacío en github.com (sin README ni .gitignore,
#    para que no haya que resolver un conflicto en el primer push)

# 2. Vincularlo y subir
git remote add origin https://github.com/<usuario>/formdata.git
git branch -M main
git push -u origin main
```

Verificación posterior: `git log --oneline` local y el historial de GitHub deben
mostrar los mismos 23 commits.

---

## 3. Gestión de datos de demostración

Los datos de prueba son un **ítem de configuración**: sin ellos, una instalación
nueva no se puede evaluar ni demostrar, y cada persona inventaría los suyos.

### Qué se versiona

No se versiona una base de datos ni un volcado SQL, sino **el programa que los
genera**: `app/demo.py`. La diferencia importa por tres motivos:

1. **Un volcado se desactualiza.** Al cambiar el esquema, el `.sql` deja de
   cargar; un generador se adapta porque usa los mismos modelos que la aplicación.
2. **Un volcado no se puede leer en una revisión.** Un `diff` sobre miles de
   `INSERT` no dice nada; sobre el generador se ve qué cambió.
3. **Es reproducible.** `random.Random(2026)` fija la semilla: la misma versión
   del generador produce siempre el mismo conjunto de datos.

### Cómo se reconstruye el entorno

```bash
pip install -r requirements.txt
python -m flask iniciar-bd       # crea las tablas y siembra los 5 roles de fábrica
python -m flask sembrar-demo     # carga 40 tratamientos + 5 solicitudes del circuito
```

`sembrar-demo` **se niega a correr sobre una base que ya tiene tratamientos**.
Es deliberado: evita duplicar datos por ejecutarlo dos veces, y evita mezclar
datos de demostración con datos reales en una instalación productiva.

### Qué genera

| Elemento | Cantidad | Para qué |
|---|---|---|
| Roles de fábrica | 5 | Comercial, Supervisor, Técnico, Gerencia, Administrador |
| Usuarios | 5 | Uno por rol, con contraseña inicial de un solo uso |
| Clientes | 3 | Para poder filtrar y comparar |
| Productos del maestro | 6 | Con principio activo y categoría |
| Tratamientos históricos | 40 | Simulan la importación del Excel |
| Solicitudes del circuito | 5 | Una en cada estado: borrador, pendiente, rechazada, aprobada, en ensayo |
| Nombres sin mapear | 4 | Para poder demostrar la normalización asistida |

Las cinco solicitudes en distintos estados existen para que el circuito se pueda
demostrar sin tener que recorrerlo entero primero.

---

## 4. Gestión de dependencias

Las versiones están **fijadas exactamente** (`==`, no `>=`):

```
Flask==3.0.3
SQLAlchemy==2.0.34
reportlab==4.4.10
```

Un rango abierto hace que dos instalaciones de la misma versión del código
traigan bibliotecas distintas, y entonces «funciona en mi máquina» deja de ser
una frase graciosa.

> **Incidente registrado.** La versión `psycopg[binary]==3.2.1` dejó de estar
> disponible en el índice de paquetes y la instalación desde cero falló. Se
> actualizó a `3.2.13`. Es el riesgo conocido de fijar versiones: protegen de
> cambios inesperados pero exigen mantenimiento cuando una versión se retira.

---

## 5. Entornos y configuración por ambiente

Tres configuraciones, seleccionadas por la variable `FORMDATA_ENTORNO`:

| Entorno | Base de datos | Particularidades |
|---|---|---|
| `desarrollo` | SQLite local | Depuración activa |
| `pruebas` | SQLite en memoria | CSRF desactivado, hash de contraseña de bajo costo |
| `produccion` | PostgreSQL | **Se niega a arrancar** sin `FORMDATA_SECRET_KEY` ni `FORMDATA_DATABASE_URI` |

Que producción falle al arrancar sin sus secretos es una decisión: es preferible
un arranque fallido y ruidoso a un sistema andando con una clave por defecto.

**Ningún secreto vive en el repositorio.** `.env.example` documenta qué variables
hacen falta, con los valores vacíos.

---

## 6. Trazabilidad entre versión y comportamiento

La suite de 272 pruebas es lo que permite afirmar que un commit no rompió nada:

```bash
pytest                                                 # SQLite,     ~20 s
FORMDATA_TEST_DATABASE_URI=postgresql+psycopg://...    # PostgreSQL, ~70 s
```

Correrla en los dos motores no es redundante: al pasar el rol de enumeración a
tabla, una consulta con `JOIN ... GROUP BY` quedó inválida y **las 272 pruebas
pasaban en SQLite mientras dos fallaban en PostgreSQL**. Ese defecto se habría
visto por primera vez en producción.
