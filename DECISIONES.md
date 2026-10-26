# FormData — análisis de los diagramas y decisiones de arquitectura

Documento de trabajo. Registra qué se encontró al revisar los quince diagramas, qué se
cambió respecto de ellos y por qué. Sirve para dos cosas: guiar la implementación y
sostener la defensa del proyecto ante los docentes.

---

## 1. Qué está bien resuelto en los diagramas

Antes de las correcciones, conviene ser claro con lo que ya funciona, porque es la
mayor parte:

- **La separación en capas del diagrama de arquitectura es correcta** y es la que se
  respeta en el código: presentación → controladores/rutas → servicios → repositorios →
  base de datos. Los servicios están partidos por responsabilidad real (importación,
  validación, normalización, indicadores, consulta IA), no por entidad, que es el error
  típico.
- **La cadena de trazabilidad está bien pensada**: `ArchivoImportado` → `Tratamiento` →
  `Mezcla` → `DetalleMezcla`, con `MapeoProducto` como puente hacia el maestro. Poder
  responder "de qué archivo salió este dato" es la diferencia entre un sistema auditable
  y una planilla con base de datos.
- **`DetalleMezcla.nombreOriginal` conservado junto al producto resuelto** es la decisión
  más importante de todo el modelo y ya estaba tomada. Nunca se pisa el dato crudo: es la
  evidencia de qué decía el Excel.
- Los diagramas de robustez separan correctamente frontera / control / entidad, y los de
  secuencia contemplan los caminos alternativos (`alt` de archivo inválido, de contexto
  insuficiente, de respuesta no confiable). Eso ya está por encima del promedio.

---

## 2. Correcciones aplicadas al modelo de datos

### 2.1 `Usuario` no tenía credenciales — bloqueante

El diagrama define `nombre, email, rol, estado, fechaAlta`. No hay hash de contraseña.
Sin eso no existe el caso de uso "Gestionar usuarios y permisos" ni el requisito de
"sistema seguro" del plan de negocio.

**Agregado:** `password_hash` (Argon2id), `debe_cambiar_password`, `intentos_fallidos`,
`bloqueado_hasta`, `ultimo_acceso`.

`rol` pasa de `varchar` a enumerado. Un `varchar` acepta `"tecnico"`, `"Técnico"` y
`"tecnico "` como tres roles distintos, y el control de acceso se rompe en silencio.

### 2.2 `MapeoProducto` necesita clave normalizada única — es el corazón del sistema

El diagrama guarda `nombreOriginal` y `nombreNormalizado` como texto libre. Con eso,
`"Round Up"`, `"round-up"` y `"ROUND UP x 20 Lt"` generan tres mapeos distintos y el
maestro se vuelve a ensuciar: exactamente el problema que FormData viene a resolver.

**Agregado:** `clave_normalizada` con índice único. La clave se calcula en
`app/services/normalizacion.py`: sin acentos, minúsculas, sin puntuación, sin ruido de
presentación (`x 20 Lt`, `bidón`, `s/d`), espacios colapsados.

Se implementa en Python y no en la base para que el resultado sea idéntico en SQLite y
PostgreSQL, y para poder testearlo sin levantar un motor. Ya hay tests que verifican que
las variantes colapsan y que `Glifosato 48` **no** colapsa con `Glifosato 62`.

### 2.3 `DetalleMezcla` con `idProducto` **y** `idMapeo`: redundancia deliberada, documentada

El diagrama tiene los dos FK. Es redundante — el mapeo ya resuelve al producto — y
redundancia no documentada es divergencia futura.

**Decisión:** se mantienen los dos, con roles distintos y escritos:
- `mapeo_id` = **por qué** se resolvió así (la trazabilidad de la decisión).
- `producto_id` = **cuál** es el producto (caché desnormalizada, para filtrar por activo
  o categoría sin un JOIN extra en cada consulta del tablero).

La regla es que `producto_id` siempre se deriva de `mapeo_id`, nunca al revés.

### 2.4 `Tratamiento.anio` y `Tratamiento.mes` se eliminan como columnas

Son derivables de `fecha`. Guardarlos por separado abre la puerta a que un año quede
desincronizado de su fecha y a que los indicadores por período mientan.

**Decisión:** propiedades calculadas en el modelo. Si más adelante los reportes por
período pesan, se agrega un índice funcional o una columna generada por la base — nunca
un campo que la aplicación tenga que acordarse de actualizar.

### 2.5 `Tratamiento` — `Mezcla` pasa de 1—1 a 1—N

El diagrama de clases y el conceptual atan un tratamiento a exactamente una mezcla. Si en
un ensayo se prueban dos combinaciones, con 1—1 la única salida es duplicar el tratamiento
entero, y ahí se rompe el conteo de tratamientos y toda la trazabilidad.

**Decisión:** 1—N con campo `orden`. Si en la práctica siempre hay una sola, el costo es
nulo; si aparece la segunda, el modelo no se rompe.

> **Punto a validar con los usuarios expertos antes de la etapa 2.** Es la única corrección
> que depende de cómo trabaja realmente el laboratorio, no de un criterio técnico.

### 2.6 `Indicador` sin período no es auditable

El diagrama guarda `nombre, valor, tipoIndicador, fechaGeneracion`. Un valor sin la ventana
temporal y los filtros con que se calculó no es comparable con nada.

**Agregado:** `periodo_desde`, `periodo_hasta`, `filtros` (JSON), `codigo`, `unidad`.

**Además:** el tablero **no** lee de esta tabla. Calcula a demanda con SQL agregado
(`app/services/indicadores.py`). Un tablero que sirve valores congelados miente apenas
entra una importación nueva. `Indicador` queda para snapshots históricos: comparar el mes
cerrado contra el anterior.

### 2.7 `ArchivoImportado` necesita hash y contadores

**Agregado:** `hash_sha256` (para detectar que se está recargando el mismo archivo),
`filas_leidas / filas_ok / filas_con_error`, y `detalle_errores` en JSON para poder
mostrar la pantalla "Informar errores de importación" del diagrama de robustez sin volver
a leer el Excel.

También se agrega una restricción `UNIQUE(archivo_id, numero_tratamiento)`: corta los
tratamientos duplicados por recarga, que es el accidente más probable en producción.

### 2.8 `ConsultaIA` necesita guardar sus fuentes

El diagrama guarda `pregunta, respuesta, estadoRespuesta`. Una respuesta técnica sin las
fuentes que la sustentan no es verificable, y una respuesta no verificable no sirve para
decidir sobre agroquímicos.

**Agregado:** `fuentes` y `herramientas_usadas` (JSON), `proveedor`, `modelo`,
`latencia_ms`, `tokens_*`.

### 2.9 Falta la entidad de auditoría — agregada

El plan de negocio (sección 2.1.3) promete "mecanismos de auditoría", pero el modelo de
datos no tiene dónde escribirlos.

**Agregado:** `RegistroAuditoria`, append-only, con quién / qué / cuándo / desde qué IP /
si salió bien. La aplicación escribe pero nunca edita ni borra. Ya está registrando logins
exitosos y fallidos, y hay un test que verifica que **no** guarda contraseñas.

### 2.10 Detalle menor: `Cliente.numeroCuenta`

Es dato sensible del cliente. No se muestra a rol comercial en las pantallas de consulta
general y no se incluye en las exportaciones abiertas.

---

## 3. Correcciones al comportamiento

### 3.1 La importación de Excel no puede ser sincrónica

`SECUENCIAIMPORTAREXCEL` muestra el ciclo completo dentro del request HTTP: leer, validar,
recorrer tratamiento por tratamiento, y dentro de cada uno recorrer producto por producto.
Con un histórico de miles de filas eso supera el timeout del servidor web y el usuario ve
una pantalla colgada sin saber si los datos entraron o no.

**Decisión para la etapa 2:** el request solo guarda el archivo, calcula el hash, crea el
`ArchivoImportado` en estado `PENDIENTE` y devuelve. El procesamiento corre aparte y el
usuario ve el avance por estado. Arranca con un hilo en segundo plano — suficiente para el
volumen real — y la interfaz del servicio queda preparada para mover eso a una cola (RQ o
Celery) sin tocar el resto del código.

Cada fila se procesa dentro de su propia transacción: una fila mal formada no debe tirar
abajo las 3.000 anteriores.

### 3.2 El asistente IA **no** genera SQL

Esta es la decisión de seguridad más importante del proyecto y conviene tenerla firme para
la defensa.

El camino intuitivo es *text-to-SQL*: el modelo escribe la consulta y el sistema la ejecuta.
Se descarta por tres razones concretas:

1. **Ejecutar SQL generado por un modelo es una inyección con pasos extra.** Una pregunta
   redactada para eso puede terminar en un `DELETE`.
2. **No es auditable.** Si cada respuesta sale de una consulta distinta e irrepetible, no
   se puede explicar de dónde salió un número.
3. **Falla en silencio.** Un JOIN mal armado devuelve un número plausible y equivocado, y
   nadie lo detecta.

**Arquitectura adoptada — herramientas acotadas (*tool calling* con consultas fijas):**

```
pregunta del usuario
      ↓
[InterpretarPregunta]  ← el modelo elige herramienta + parámetros, no escribe SQL
      ↓
[BuscarContextoHistorico]  ← ejecuta consultas parametrizadas ya escritas y revisadas
      ↓                       (buscar_tratamientos, productos_por_activo,
      ↓                        resultados_por_cultivo, mezclas_con_producto…)
[GenerarRespuestaAsistida] ← el modelo redacta SOBRE esos resultados, no sobre la base
      ↓
[ValidarRespuestaIA]  ← toda cifra afirmada debe existir en el contexto recuperado;
      ↓                 si no, la respuesta se marca REQUIERE_REVISION
respuesta + fuentes citadas (nros. de tratamiento clickeables)
```

Ventajas para la defensa: el modelo nunca toca la base, la superficie de ataque es el
conjunto cerrado de consultas revisadas, cada respuesta cita registros verificables, y el
proveedor de IA es intercambiable (una API externa o un Ollama local) porque el contrato
es la interfaz de herramientas, no el modelo.

El modelo se conecta detrás de una interfaz `ProveedorIA`. Si en la defensa preguntan por
privacidad de datos: con Ollama local, ningún dato de mezclas sale de la red de la empresa.

### 3.3 Falta un caso de uso en el diagrama

Los diagramas contemplan "Detectar productos sin mapear" pero no **auditar los cambios**.
Se agrega el caso de uso *Consultar auditoría* (rol administrador), que es lo que hace
utilizable la entidad nueva de la sección 2.9.

---

## 4. Stack elegido y por qué

| Capa | Elección | Por qué esta y no otra |
|---|---|---|
| Lenguaje | Python 3.11+ | Es el que ya usás y el que declara el plan de negocio. |
| Web | Flask 3 + *app factory* + Blueprints | El plan lo nombra. La fábrica permite instanciar la app con config de pruebas, que es lo que hace testeable todo lo demás. |
| ORM | SQLAlchemy 2.0 + Alembic | Consultas parametrizadas por construcción: la inyección SQL deja de ser una preocupación. Alembic da migraciones versionadas. |
| Base | SQLite en desarrollo, PostgreSQL en producción | SQLite arranca sin instalar nada. PostgreSQL aporta `pg_trgm` para búsqueda difusa de productos y JSONB para los logs de importación. El código no distingue: nada específico del motor está en la lógica. |
| Contraseñas | Argon2id | Ganador del Password Hashing Competition; resistente a GPU, a diferencia de un SHA con sal. |
| Sesiones | Flask-Login + cookie `HttpOnly` / `SameSite=Lax` / `Secure` | |
| CSRF | Flask-WTF | Token en todo formulario, incluido el de cerrar sesión. |
| Interfaz | Jinja2 + CSS propio + HTMX donde haga falta interactividad | Un SPA en React duplica el trabajo (dos aplicaciones, dos modelos de auth) sin aportar nada a este caso de uso. HTMX da las partes dinámicas — filtros, autocompletado de productos — sin build step. |
| Gráficos | Chart.js | |
| Excel | openpyxl (streaming) | `read_only=True` evita cargar el archivo entero en memoria. |
| Pruebas | pytest | |

### Sobre Power BI

El plan lo menciona. **No debería ser parte del sistema.** Requiere licencia por usuario y
ata el proyecto a un ecosistema propietario. Los indicadores se sirven desde el tablero
propio. Si Dirección lo pide, se resuelve exponiendo una vista de solo lectura en
PostgreSQL para que Power BI se conecte — sin que el sistema dependa de eso.

---

## 5. Seguridad — qué queda implementado y verificado

| Amenaza | Control | Verificado |
|---|---|---|
| Robo de contraseñas de la base | Argon2id, rehash automático al cambiar parámetros | test: el hash no contiene la clave |
| Fuerza bruta de login | Bloqueo a los 5 intentos + límite de tasa por IP | test: la sexta devuelve 429 |
| Enumeración de cuentas | Mensaje y código HTTP idénticos para "no existe" / "clave mal" / "suspendido" | test |
| Escalada de privilegios | Capacidades declaradas en una tabla, decorador `@requiere(...)` | test: comercial no importa |
| CSRF | Token en todos los formularios | Flask-WTF |
| Inyección SQL | Solo ORM, cero SQL armado por concatenación | por construcción |
| XSS | Autoescape de Jinja2 + CSP sin `unsafe-inline` en scripts | test de cabeceras |
| Clickjacking | `X-Frame-Options: DENY` + `frame-ancestors 'none'` | test |
| Redirección abierta tras login | Solo se aceptan rutas internas | test |
| Secuestro de sesión | Cookie `HttpOnly`/`Secure`, `session_protection="strong"`, vencimiento a 8 h | |
| Secretos en el repositorio | Todo por variable de entorno; producción falla al arrancar si faltan | |
| Carga maliciosa | Límite de 25 MB, extensión validada, hash registrado | |
| Falta de trazabilidad | `RegistroAuditoria` append-only | test: no guarda credenciales |

---

## 6. Plan por etapas

Cada etapa termina con algo que se puede abrir en el navegador y con sus pruebas en verde.

- **Etapa 1 — Cimientos. `LISTA`**
  Modelo de datos completo, autenticación, roles, auditoría, cabeceras, tablero con
  indicadores reales, sistema visual, 15 pruebas.

- **Etapa 2A — Consulta de tratamientos. `LISTA`**
  Búsqueda combinable (texto, producto o activo, cultivo, tipo, cliente, resultado, rango
  de fechas, pendientes de mapeo), orden, paginación, ficha del tratamiento con la mezcla
  completa y las mediciones, ensayos relacionados, y maestro de productos con salto
  cruzado. Filtros con HTMX y degradación a formulario común sin JavaScript.
  Cubre los casos de uso *Consultar tratamiento*, *Buscar productos por activo o
  categoría* y *Buscar tratamientos por producto*.

  Dos decisiones de implementación que conviene poder explicar:
  - El filtro por producto va como subconsulta `IN` y no como JOIN. Con JOIN, un
    tratamiento que usa tres productos coincidentes aparece tres veces en el listado.
  - La búsqueda por producto mira **también** el nombre crudo del Excel, no solo el
    maestro. Si mirara solo el maestro, los tratamientos con productos sin normalizar
    serían invisibles — justo los que más importa encontrar.

- **Etapa 2C — Circuito de solicitudes. `LISTA`**
  Máquina de estados con siete estados y siete transiciones, rol Supervisor nuevo,
  bandeja por rol, alta de solicitud con resolución automática de productos contra el
  maestro, carga de resultados, e historial completo de quién hizo qué y por qué.

  Decisiones que conviene poder defender:
  - **Toda transición pasa por `flujo.ejecutar()`.** Las vistas no tocan `estado_flujo`
    a mano. La regla vive en un solo lugar, y una pantalla nueva no puede saltearla.
  - **Los botones se dibujan desde `acciones_disponibles()`**, y `ejecutar()` vuelve a
    validar todo. Ocultar un botón es comodidad; la seguridad está en el servidor. Hay
    un test que postea una transición prohibida por URL y verifica que no pasa.
  - **El rechazo exige motivo escrito.** Sin eso el circuito registra que algo se frenó
    pero no por qué, y el comercial no sabe qué corregir.
  - **Resultado y cierre van en la misma transacción.** Un tratamiento finalizado sin
    resultado, o un resultado en un tratamiento abierto, serían estados inexplicables.
  - **Las solicitudes y lo importado conviven en `Tratamiento`.** Lo que entra por Excel
    nace `FINALIZADA` con `origen = importacion`; lo que nace en el sistema recorre el
    circuito. La búsqueda, la ficha y los indicadores no se enteran de la diferencia.
  - **El vendedor escribe el nombre del producto como lo conoce.** Se resuelve con el
    mismo motor de normalización que usa la importación. Si no resuelve, queda pendiente
    y cae en la misma bandeja: un solo mecanismo para los dos orígenes.

- **Etapa 2B — Importación de Excel. `LISTA`**
  Lector tolerante, validador con informe por fila y columna, carga idempotente y
  procesamiento en segundo plano. Verificado contra el archivo real.

  ### El archivo real: qué se encontró

  Ocho hojas. `Tratamientos` con 37 columnas y una fila por ensayo; `Productos
  Normalizados` con una fila por producto por tratamiento y su orden; `Mapeo_Productos`
  con las equivalencias; `Maestro_Productos` con registro SENASA, empresa, activos,
  categoría y formulación; dos vademécums SENASA; y un `Instructivo` que documenta el
  proceso, incluida la macro VBA `Normalizar_Incremental` que desglosa la mezcla usando
  `+` como separador.

  Que ese proceso ya exista y esté escrito es el argumento más fuerte del proyecto: el
  sistema no inventa un método, automatiza uno que la organización ya validó.

  ### El defecto que condiciona todo el importador

  La columna `Producto_Final` de la hoja `Productos Normalizados` **devuelve productos
  equivocados**. De los ocho nombres crudos de la muestra, siete no existen en
  `Mapeo_Productos` y deberían mostrar «SIN MAPEAR» según el propio instructivo; en
  cambio muestran un producto real pero incorrecto (`Sulfato de Amonio` → `ADAMA OXALIS
  48 SC`). Es el síntoma de una búsqueda con coincidencia aproximada o con el rango mal
  anclado: no tira `#N/A`, no rompe nada, y deja los datos mal en silencio.

  **Decisión: el importador ignora `Producto_Final` e `Id_Producto`.** Lee el nombre
  crudo y lo resuelve él mismo, en este orden: mapeo confirmado → coincidencia directa
  contra el maestro → pendiente. Heredar esa columna habría contaminado el histórico
  entero y ningún indicador posterior sería confiable.

  ### Otras decisiones

  - **Lector y cargador separados.** `lector_excel.py` no conoce SQLAlchemy y
    `importacion.py` no conoce openpyxl. El parser se testea con un diccionario.
  - **Encabezados tolerantes.** El archivo real trae `Granulometría (>180 µm)22`, con
    basura pegada de una edición vieja. Un validador estricto rechazaría todo el archivo
    por eso; el nuestro identifica la columna por el número de micras dentro del `>`.
  - **Filas fantasma.** La hoja tiene 3.559 filas sin número, con `Año=1900` y `Mes=1`
    por fórmulas arrastradas hasta el fondo. Se descartan en silencio: no son errores del
    usuario, son un artefacto de la planilla. Se cuentan y se informan aparte.
  - **Idempotencia por número de tratamiento.** El negocio confirmó que el número no se
    reutiliza entre años, así que es la clave natural del histórico. Reimportar el mismo
    archivo actualiza en lugar de duplicar. Hay un test que lo verifica.
  - **Una fila, una transacción.** Una fila rota no tira abajo las miles anteriores.
  - **Segundo plano.** El request guarda el archivo y devuelve; el procesamiento corre en
    un hilo y la pantalla sondea el estado. La interfaz de `procesar()` no cambia el día
    que eso pase a una cola.
  - **`Año` y `Mes` se ignoran.** Se derivan de `Fecha`, y en las filas fantasma además
    mienten.
  - **Dos clientes distintos.** El negocio confirmó que `Cliente` y `CLIENTE SOFTLAND`
    no son la misma entidad escrita de dos formas. `Tratamiento` tiene dos referencias a
    `Cliente`, y la cuenta de Softland lleva el `NRO CUENTA`.
  - **Sin dosis.** El Excel no registra dosis por producto, solo `TasaAplicacion` a nivel
    tratamiento. `DetalleMezcla.dosis` queda vacío en todo lo importado y se llena solo
    desde las solicitudes nuevas.

  ### Hallazgo colateral

  De las 99 filas de `Mapeo_Productos`, 7 son duplicados que solo difieren en acentos,
  guiones o espacios (`2,4 D Enlist` / `2,4-D Enlist`, `Paraquat Secafol` / `Paraquat
  (Secafol)` / `Paraquat ( Secafol)`). La normalización las colapsa en 92 equivalencias
  reales. Es la demostración práctica de para qué sirve la clave normalizada.

- **Etapa 3 — Normalización asistida y maestro. `LISTA`**
  Bandeja de productos sin mapear con sugerencias ordenadas por parecido, confirmación en
  un clic, descarte, deshacer, alta rápida en el maestro y ABM completo de productos con
  fusión de duplicados. Es la pantalla que hace que la probeta del tablero suba.

  ### La decisión de diseño que ordena toda la etapa

  **La unidad de trabajo es la clave, no la fila.** Si `Coadyubante Silic.` aparece en 40
  tratamientos, eso es *una* decisión que impacta 40 filas, no 40 decisiones. La bandeja
  agrupa por `clave_normalizada`, muestra el conteo de filas que arrastra cada nombre y
  ordena de mayor a menor: resolver primero lo más repetido es lo que sube la salud del
  dato con la menor cantidad de clics. Con el archivo real, 29 pendientes se resuelven en
  una veintena de decisiones.

  El `UPDATE` es masivo (`update().where(clave_normalizada == ...)`), no un bucle por
  fila: un nombre repetido cientos de veces se resuelve en una ida a la base.

  ### Nada se aplica solo

  El sistema **ordena candidatos; confirma una persona**. Esto no es prudencia genérica:
  el maestro sucio que el proyecto viene a arreglar se originó justamente en una fórmula
  de Excel que resolvía sola y devolvía productos equivocados en silencio (ver 2B). Repetir
  ese patrón con mejor tecnología sería reproducir el defecto, no corregirlo.

  ### Cómo se calcula el parecido — y el freno de concentración

  `normalizacion.puntaje()` combina tres lecturas del mismo par de nombres y toma el
  máximo, porque ninguna sola alcanza:

  | Lectura | Resuelve | Ejemplo que solo ella ve |
  |---|---|---|
  | Levenshtein normalizada | errores de tipeo | `Coadyubante` → `Coadyuvante` (0,94) |
  | Solapamiento de tokens con prefijo | abreviaturas | `Ciper 25` → `Cipermetrina 25` (0,93) |
  | Contención | un nombre dentro de otro | `Round-up FULL x20` → `Round Up Full` (0,88) |

  Y encima de todo eso, **el freno de concentración**: si los dos nombres traen números y
  no son los mismos, el puntaje se corta en 0,60 por más que el resto del texto coincida.
  `Glifosato 48` y `Glifosato 62` comparten todas las letras y son productos distintos.
  En agroquímicos el número es la concentración, y confundir dos concentraciones en el
  maestro es exactamente el error que este sistema existe para no cometer. Hay un test
  que lo fija.

  La sugerencia compara además contra el **principio activo**, no solo contra el nombre
  comercial: en las planillas es habitual que el técnico escriba «Glifosato» cuando el
  producto se llama «Round Up Full». Ese candidato pesa un 10 % menos, porque dos
  productos distintos pueden compartir activo.

  Los umbrales (0,55 para mostrar, 0,70 media, 0,88 alta) son constantes con nombre en
  `normalizacion.py`, no números sueltos en el código: son el parámetro que se va a
  calibrar cuando se use con el histórico completo.

  ### «No es un producto» es una decisión de primera clase

  En las planillas hay celdas con `s/d`, con una dosis suelta o con el nombre de un
  cultivo. Sin una salida explícita para eso, esas filas quedan pendientes para siempre y
  la salud del dato nunca llega al 100 %, que es como decir que el indicador de salud
  deja de significar algo. Descartar escribe un `MapeoProducto` en estado `RECHAZADO` y
  pasa las filas a `SIN_PRODUCTO`.

  ### Toda decisión se guarda como regla, no solo como dato

  Confirmar no toca únicamente los `DetalleMezcla`: escribe el `MapeoProducto`. Esa es la
  diferencia entre corregir el pasado y no volver a preguntar. La próxima importación
  resuelve ese nombre sola, y queda registrado quién lo confirmó.

  **Defecto encontrado y corregido acá:** `_resolver_producto()` no contemplaba el estado
  `RECHAZADO`, así que devolvía esas filas a `PENDIENTE` en cada reimportación. El trabajo
  de la bandeja se habría borrado solo la primera vez que alguien recargara el histórico.
  Corregido y con dos tests que lo cubren.

  ### Deshacer no es un lujo

  Cada acción de esta pantalla modifica decenas de filas de un solo POST. Sin una vuelta
  atrás barata, un clic equivocado obliga a reimportar el archivo entero. Deshacer borra
  la regla y devuelve las filas a pendiente, y queda en la auditoría.

  ### El maestro no borra

  - **Alta duplicada rechazada por clave.** Dos productos que normalizan igual repartirían
    los usos entre dos filas y ningún indicador cerraría. Se rechaza el alta y se sugiere
    fusionar.
  - **Discontinuar en lugar de borrar.** Un producto usado en ensayos de 2019 no se puede
    eliminar sin romper la trazabilidad de esos ensayos. Discontinuado deja de ofrecerse
    en las sugerencias y sigue explicando el pasado.
  - **Fusionar mueve las dos puntas:** los usos (`DetalleMezcla`) *y* las reglas
    (`MapeoProducto`). Si solo se movieran los usos, la próxima importación volvería a
    mandar filas al producto viejo y el duplicado renacería. El origen queda discontinuado
    con la constancia escrita de a dónde fue.
  - La ficha del producto muestra **con qué grafías se lo escribió**. Es la evidencia de
    que el mapeo está bien hecho y, cuando está mal, es donde salta a la vista.

  ### Interfaz

  Confirmar responde con HTMX el fragmento de la tarjeta ya resuelta: la pantalla no se
  mueve y el técnico sigue en el mismo lugar de la lista. La misma vista contesta el
  formulario común sin JavaScript con un `redirect`, así no hay una versión degradada que
  nadie prueba nunca.

  Las acciones van por `POST` con la clave en el cuerpo y no en la URL: la clave es texto
  libre venido de una planilla (`s/d`, `ciper 25 %`) y meterla en el path obliga a
  escaparla en cada lado. En el cuerpo viaja tal cual, y `POST` es además lo correcto
  semánticamente cuando cada clic actualiza decenas de filas.

- **Etapa 4 — Indicadores y exportación. `LISTA`**
  Tablero filtrable con gráficos, exportación a Excel en dos formatos e informe en PDF.

  ### Lo que se encontró en el archivo real antes de diseñar el tablero

  Dos cosas obligaron a replantear qué se podía graficar:

  - **`porcentaje_efectividad` no existe en el Excel.** No hay columna, y el importador
    nunca la llena. Un tablero apoyado en «efectividad promedio» se habría visto
    impecable con datos de ejemplo y vacío con los reales.
  - **El estado del ensayo no es una clasificación cerrada.** Llega como el código que
    escribe el laboratorio: `OK`, `PPDO` (precipitado). Presentarlo como
    «Compatible / Incompatible» sería fabricar una interpretación que el dato no tiene.

  **Decisión: el tablero se construye sobre lo que el dato real tiene** —conteos,
  cultivo, tipo, cliente, producto, fecha, pH y dureza— y las observaciones se muestran
  tal como se escribieron. Si el laboratorio quiere categorías fijas, esa es una decisión
  suya que hay que tomar en la planilla, no una que el tablero pueda inventar.

  Como consecuencia, `demo.py` se corrigió para sembrar los códigos reales y **no**
  llenar el porcentaje de efectividad en lo importado. Un demo que se ve mejor que la
  realidad es la peor preparación posible para una defensa.

  ### El tablero es la búsqueda vista desde arriba

  `leer_filtros()` vive en `consultas.py` y lo usan las tres pantallas: búsqueda, tablero
  y exportación. Todos los indicadores se calculan sobre
  `consultas.subconsulta_ids(filtros)`, el mismo conjunto que devuelve la lista.

  De ahí salen tres propiedades que se testean explícitamente:

  - Ningún gráfico puede contar algo que la búsqueda no muestre (ocho combinaciones de
    filtros verificadas contra `consultas.buscar()`).
  - Cada barra enlaza a la búsqueda que la explica, arrastrando los filtros vigentes. El
    test sigue el enlace y comprueba que lista exactamente el valor de la barra: un
    enlace que pierde el filtro activo lleva a una lista más grande y hace que quien la
    abra concluya que el gráfico está mal.
  - Lo que se exporta es lo que se está mirando. El botón arrastra la query string; un
    «exportar» que ignora la búsqueda activa es una trampa.

  La salud del dato de la probeta, en cambio, sigue siendo **global** a propósito: es el
  estado del repositorio y no puede depender de qué esté mirando cada uno. La
  normalización que muestran las cifras sí es la **del recorte**, porque decir «95 %
  normalizado» sin aclarar sobre qué vuelve inútil al indicador.

  ### Gráficos en SVG generado en el servidor, sin librería de JavaScript

  La alternativa habitual (Chart.js, ApexCharts) exigía tres concesiones:

  | Concesión | Por qué no |
  |---|---|
  | Abrir la CSP | La política solo admite scripts propios y dos CDN. Agrandar la superficie por una función decorativa no se justifica. |
  | Pantalla vacía sin JavaScript | Todo el resto del sistema degrada a formulario común; el tablero sería la única pantalla que no muestra nada. |
  | Perder impresión y PDF | Un `<canvas>` no se imprime bien ni se reutiliza del lado del servidor. |

  Con SVG, la misma serie alimenta pantalla, impresión y —recalculada con primitivas— el
  PDF. El costo es dibujar a mano: para barras y columnas son treinta líneas de
  geometría, testeadas (la barra mayor ocupa la pista completa; la apilada reparte
  exactamente el ancho; una serie vacía dibuja un cartel y no un gráfico roto).

  **Las etiquetas se escapan.** Los nombres vienen de un Excel que carga cualquiera: son
  entrada no confiable, y en un SVG se inyectan igual que en HTML. Hay un test que mete
  `<img src=x onerror=...>` como nombre de producto.

  **Cada gráfico va con su tabla de valores**, plegada. El gráfico responde «qué forma
  tiene»; la tabla, «cuánto exactamente». Ofrecer solo el gráfico obliga a estimar
  valores mirando largos de barra, que es lo que un informe de laboratorio no puede
  pedirle a nadie. Además es lo que lee un lector de pantalla.

  ### Dos defectos que solo aparecieron al abrirlo en un navegador

  Ninguna prueba automática los habría detectado:

  - Las etiquetas de los meses se pisaban. El salto se calculaba sobre la cantidad de
    barras; ahora sale de comparar el ancho de una etiqueta con el paso entre columnas.
  - El texto de los gráficos quedaba a 7 px en las tarjetas de media columna: el texto de
    un SVG escala con el `viewBox`, y uno de 640 dentro de una tarjeta de 400 px encoge
    todo. Cada tipo de gráfico declara ahora un ancho parecido al del hueco donde vive.

  ### Exportación: un archivo tiene que poder explicarse solo

  Los dos formatos abren con **quién exportó, cuándo y con qué filtros**. Seis meses
  después nadie recuerda cómo se generó una planilla, y una cifra que no se puede
  reproducir no se puede discutir.

  - **Excel es para seguir trabajando.** Cuatro hojas: `Criterios` (primera, a propósito),
    `Resumen`, `Tratamientos` en formato ancho —un ensayo por fila, para leer— y
    `Productos por ensayo` en formato largo —un producto por fila, para tablas dinámicas—.
    Convertir de largo a ancho en Excel es tedioso; al revés es imposible, así que se
    entregan los dos. Con panel fijo y autofiltro: quien recibe el archivo espera filtrar.
  - **PDF es para circular.** Resumen, gráficos y las primeras 40 filas. No pretende
    llevar el dato completo; para eso está el Excel, y el PDF lo dice cuando recorta.
  - **Tope de 20.000 filas** en Excel. Un histórico entero exportado consume memoria del
    servidor y produce un archivo que nadie abre. Se corta, y el archivo lo declara en la
    hoja de criterios en lugar de mentir por omisión.
  - **`reportlab` y no WeasyPrint.** Rueda pura de Python: instala en Windows sin GTK ni
    Pango. Para un sistema que va a correr en la máquina del laboratorio, esa diferencia
    decide.
  - **Toda exportación queda auditada** con el usuario y los criterios. Son datos
    comerciales de clientes saliendo del sistema.
  - `Cache-Control: no-store` en las descargas: la misma URL con otros datos detrás tiene
    que volver a generarse.

  ### htmx pasa a servirse desde la aplicación

  Al abrir el tablero en un navegador real apareció algo que ninguna prueba de servidor
  podía detectar: **htmx se cargaba desde `unpkg.com`**, y en un entorno sin salida a esa
  CDN no cargaba nunca. Con eso, todas las interacciones del cliente —los filtros de la
  búsqueda, los del tablero, el confirmar de la bandeja— quedaban sin ejercitar: las
  pruebas cubrían el lado del servidor (peticiones con la cabecera `HX-Request`) y nadie
  había comprobado el otro lado.

  Ahora el archivo vive en `app/static/js/htmx-2.0.2.min.js`. Tres razones:

  - El laboratorio puede estar sin internet o detrás de un proxy. Sin htmx los filtros se
    caen; con la copia local funcionan siempre.
  - Un script de terceros cargado en una aplicación con datos comerciales de clientes es
    una dependencia de cadena de suministro que no hace falta asumir.
  - La versión queda congelada en el repositorio y no depende de que la CDN la siga
    publicando.

  Como consecuencia, la CSP se cerró de
  `script-src 'self' https://unpkg.com https://cdn.jsdelivr.net` a `script-src 'self'`.

  ### Detalle menor corregido

  El orden «Mayor efectividad primero» de la búsqueda apunta a un campo que solo tienen
  los ensayos nacidos en el circuito. La etiqueta ahora lo aclara, para que nadie
  interprete que los importados quedaron últimos por malos.

- **Etapa 5 — Asistente IA. `LISTA`**
  Nueve herramientas acotadas, validación de la respuesta contra lo recuperado, fuentes
  con enlace en cada respuesta, y un modo sin modelo para que la pantalla funcione
  aunque no haya clave cargada.

  ### La decisión que ya estaba tomada y se sostuvo: el modelo no escribe SQL

  El camino fácil era darle el esquema al modelo y dejar que genere consultas. Se
  descartó por tres motivos, en orden de gravedad:

  1. **Seguridad.** Una consulta generada es código que entra desde afuera. Ninguna
     lista de palabras prohibidas cubre todas las formas de escribir un `DELETE` o de
     leer la tabla de usuarios. Con herramientas fijas, la superficie es exactamente la
     que se decidió y se audita leyendo un diccionario de nueve entradas.
  2. **Corrección silenciosa.** Un `JOIN` mal armado no falla: devuelve un número
     equivocado con cara de correcto. Es el mismo defecto que tenía la fórmula del Excel
     (ver 2B), y el proyecto existe para corregirlo, no para reproducirlo con mejor
     tecnología.
  3. **Verificabilidad.** Cada herramienta devuelve, además de los datos, las fuentes.

  El modelo **elige** una herramienta y le pasa argumentos; el SQL lo escribió una
  persona y está parametrizado. Los argumentos vienen de un modelo de lenguaje: son
  entrada no confiable y se validan igual que los de un formulario público. Un
  parámetro que la herramienta no declara se descarta antes de llegar a la consulta.

  ### Las tres barreras del motor

  | Barrera | Qué evita |
  |---|---|
  | Sin datos recuperados, no se responde | Que el modelo conteste de memoria sobre agroquímicos y suene igual de seguro que cuando sí consultó |
  | Toda respuesta arrastra sus fuentes, con enlace | Que haya que creerle: la persona abre el ensayo y comprueba |
  | Las cifras se contrastan contra lo recuperado | El dato inventado con forma de dato preciso |

  La tercera es burda a propósito —un número puede ser correcto y calculado— pero
  atrapa el caso peligroso. Cuando salta, la consulta queda como `requiere_revision` y
  la pantalla lo dice en el borde de la respuesta, no en una nota al pie.

  **Defecto encontrado al escribir esa validación:** la comparación era «el número está
  contenido en alguno del contexto», y un `3` suelto en los datos daba por bueno un
  `93,5` inventado. Ahora es por prefijo y solo cuentan los valores de dos cifras o más.

  ### El modo sin modelo

  `ProveedorSinModelo` enruta la pregunta a una herramienta por palabras clave. No
  entiende lenguaje natural: reconoce formas de preguntar. Está por dos razones
  concretas:

  - **La pantalla funciona el día de la presentación aunque no haya clave cargada**, y
    todo lo demás —herramientas, fuentes, validación, registro— queda igualmente
    demostrado, porque los datos que devuelve son los mismos.
  - **Las pruebas no dependen de internet.** Ninguno de los 50 tests de esta etapa llama
    a una API externa: el proveedor se sustituye por uno falso al que el test le carga
    las decisiones. Un asistente que solo se puede probar con conexión no se puede
    verificar en la máquina donde se defiende el proyecto.

  La pantalla **avisa en qué modo está**. Presentarlo como si razonara sería la clase de
  exageración que el resto del proyecto viene evitando.

  **Defecto encontrado por un test:** la expresión que detecta números de tratamiento
  (`TR-0006`, `SOL-2026-0001`) leía **«trigo»** como el tratamiento `TR-IGO`, así que
  preguntar por un cultivo terminaba pidiendo el detalle de un ensayo inexistente. Ahora
  el prefijo tiene que estar seguido de un dígito.

  ### Otras decisiones

  - **Cliente HTTP propio en lugar del SDK.** Son treinta líneas, evita una dependencia
    más y deja a la vista qué se manda —que en un sistema donde importa qué datos salen
    no es un detalle menor. La abstracción `Proveedor` permite cambiar de modelo, o pasar
    a uno local, sin tocar el motor.
  - **Sin memoria conversacional.** Cada consulta se resuelve sola. Con memoria, la
    respuesta a la pregunta 5 depende de lo que el modelo entendió en la 2, y verificar
    una cifra obliga a releer el hilo. Acá cada respuesta se sostiene con sus propias
    fuentes, que es lo que hace falta cuando alguien va a apoyar una decisión técnica.
  - **Tope de cuatro vueltas.** Un modelo que pide herramientas para siempre no puede
    colgar el servidor.
  - **Límite de tasa por usuario.** Cada consulta cuesta tiempo de base y, con modelo,
    dinero.
  - **Inyección por los datos.** Los nombres de producto salen de planillas que carga
    cualquiera. Las instrucciones del sistema declaran que los resultados de herramienta
    son contenido y no órdenes. No es una defensa perfecta; omitirla sería no haber
    pensado el problema.
  - **Errores del proveedor sin filtrar detalles.** El cuerpo de un error HTTP puede
    traer la clave: se informa el código y nada más.
  - **Se guarda todo, también lo que salió mal.** Sin el registro de las respuestas malas
    no hay forma de saber después si el asistente sirve o si la gente lo usa igual.
  - **Abrir un enlace no dispara una consulta.** Las sugerencias dejan la pregunta
    escrita en el campo; ejecutar es del botón. Una consulta registra y puede costar
    dinero: no puede pasar por un GET.
  - **El asistente no recomienda.** Informa lo que dicen los ensayos registrados;
    decidir si una mezcla se aplica es del técnico.

- **Etapa 6 — Administración y despliegue. `LISTA`**
  ABM de usuarios, visor de auditoría, Docker Compose con PostgreSQL, manual de usuario
  y documento de arquitectura.

  ### El patrón que faltaba: unidad de trabajo

  Se encontró mirando el código, no eligiéndolo de una lista: había `commit()` y
  `rollback()` en **nueve lugares, incluidas las vistas**, y un `confirmar=False`
  viajando entre servicios. Ese booleano era una unidad de trabajo simulada a mano.

  `app/unidad_de_trabajo.py` define `transaccion()`, un contexto **reentrante**:
  `solicitudes.registrar()` llama a `flujo.ejecutar()`, los dos abren transacción y
  solo la de más afuera confirma. Un servicio no tiene que saber si es la operación
  entera o parte de otra.

  Un beneficio que no estaba previsto: **la auditoría se escribe ahora en la misma
  transacción que la acción que registra**. Antes podía quedar un registro de algo que
  después se deshizo, y eso vuelve inservible a una bitácora.

  ### Los usuarios: tres reglas y el mismo motivo detrás de las tres

  El motivo es que **el usuario es la clave de todo lo que quedó registrado**.

  - **No se borra.** Quien aprobó ensayos en 2024 sigue explicando esas aprobaciones.
    Se suspende (temporal) o se da de baja (definitivo); son estados distintos a
    propósito, porque tratarlos igual obliga a adivinar después por qué se cerró una
    cuenta.
  - **El email no se recicla**, ni después de una baja. Si mañana entrara otra persona
    con la misma dirección, el histórico diría que ella aprobó lo que aprobó otra.
  - **La contraseña la elige su dueño.** El alta genera una clave de un solo uso que se
    muestra **una sola vez** y obliga a cambiarla al ingresar. Un administrador que
    conoce la contraseña de otro puede actuar en su nombre, y eso vacía de sentido a
    toda la auditoría. La clave usa `secrets` (no `random`) y omite los caracteres que
    se confunden al dictarlos: `O`, `0`, `l`, `I`, `1`.

  Y una salvaguarda: **el sistema no se deja quedar sin administradores activos**. Sin
  eso, un administrador puede bajarse a sí mismo de rol y para salir hay que tocar la
  base a mano.

  ### La bitácora solo se lee

  `services/auditoria.py` **no escribe**. La bitácora es append-only: la escribe
  `security.registrar()` y la aplicación no la edita ni la borra. Una bitácora que el
  sistema puede modificar no prueba nada, porque el mismo sistema que registró la
  acción pudo cambiar el registro.

  Se registran también los **fracasos**: una ráfaga de ingresos fallidos contra la
  misma cuenta es la única señal temprana que tiene un sistema chico de que alguien
  está probando contraseñas. Se muestran resaltados.

  **Dos defectos encontrados al verificar en el navegador:**

  - La tabla de etiquetas se había escrito de memoria: cinco acciones no tenían
    traducción y otras cinco traducían nombres que el código nunca emite. Hay ahora un
    test que lee las llamadas reales a `registrar()` del código fuente y las cruza
    contra la tabla, para que la próxima desincronización salte sola.
  - La bitácora mostraba `#5` en lugar del nombre de quien actuó. Una bitácora que hay
    que traducir a mano no se usa. Se agregó la relación con carga en lote.

  ### Despliegue

  - **`postgres:16-alpine` para la base, `python:3.12-slim` para la aplicación.** Slim y
    no alpine del lado de Python: alpine usa musl y obliga a compilar argon2 y psycopg
    desde fuente, lo que rompe de maneras difíciles de diagnosticar y no compensa en
    una sola máquina.
  - **La base no publica puertos.** Solo la alcanza la aplicación por la red interna.
  - **`healthcheck` en la base.** La aplicación arranca más rápido que PostgreSQL; sin
    el chequeo, el primer arranque falla con un error de conexión que parece un
    problema de la app.
  - **Volúmenes con nombre** para los datos y los Excel importados: sin ellos, bajar los
    contenedores para actualizar borraría la base.
  - **La aplicación no corre como root** dentro del contenedor.
  - **Gunicorn y no el servidor de Flask**, que es de un solo hilo y su propia
    documentación dice que no se use en producción.

  ### La afirmación que por fin se verificó

  Desde la etapa 1 el proyecto sostenía que funciona en SQLite y en PostgreSQL sin
  cambiar la lógica. **Nunca se había probado.** Ahora `ConfigPruebas` lee
  `FORMDATA_TEST_DATABASE_URI`, y **las 241 pruebas corren en los dos motores**:

  ```bash
  python -m pytest                                                      # SQLite, ~26 s
  FORMDATA_TEST_DATABASE_URI=postgresql+psycopg://... python -m pytest   # PostgreSQL, ~67 s
  ```

  ### La suite pasó de 2 min 39 s a 26 segundos

  Argon2 tarda deliberadamente unos 220 ms por hash, y la suite crea usuarios e ingresa
  cientos de veces: más de la mitad del tiempo era el algoritmo haciendo exactamente
  aquello para lo que fue diseñado. `configurar_hasher()` baja los parámetros **solo con
  `ARGON2_RAPIDO`**, que únicamente activa la configuración de pruebas. No cambia lo que
  se prueba: el algoritmo, el formato del hash y la verificación son los mismos; solo
  cuesta menos calcularlo.

  Dos detalles que costaron encontrar:

  - `usuario.py` hacía `from ..extensions import hasher`, lo que ata el nombre al objeto
    original en el momento de importar. Reemplazar el global no lo alcanzaba y el primer
    intento no cambió nada. Ahora se resuelve por módulo, en el momento de la llamada.
  - `configurar_hasher` asigna en **las dos ramas**. Con un `if` sin `else`, un proceso
    que configurara primero pruebas y después producción se quedaría con el hash débil.
    Hay un test que lo fija.

  ### Documentación

  - `ARQUITECTURA.md`: las capas, los doce patrones aplicados con su archivo, y —lo más
    útil para la defensa— una tabla de lo que **no** se usó y por qué.
  - `MANUAL.md`: escrito para quien va a usar el sistema, no para el jurado.

- **Etapa 7 — Roles y permisos editables. `LISTA`**
  Los roles dejan de ser un enum del código y pasan a ser filas editables desde
  `/administracion/roles`: se pueden crear roles nuevos y cambiar qué puede hacer cada
  uno, sin tocar código ni redesplegar.

  ### Qué se movió a la base y qué no

  | Vive en | Qué | Por qué |
  |---|---|---|
  | Código (`security/roles.py`) | El **catálogo** de capacidades | Cada una existe porque hay una vista que la exige con `@requiere(...)`. Inventarlas desde una pantalla daría permisos que no gobiernan nada, y la impresión de que sí. |
  | Base (`rol`, `permiso_rol`) | La **asignación** de capacidades a roles | Es lo que cambia con la organización: que el supervisor también importe, que exista un «Auditor» que solo lee. |

  ### Lo que se gana y lo que se paga

  Se gana flexibilidad real. Se pagan tres riesgos, y los tres están atajados:

  1. **Quedarse afuera.** Si alguien le quita a todos los roles la gestión de usuarios,
     no hay forma de volver desde la aplicación. La salvaguarda mira que quede al menos
     un rol **con usuarios activos** que conserve las capacidades críticas. El detalle
     de «con usuarios activos» importa: un rol con el permiso pero sin nadie asignado no
     destraba nada, porque nadie puede entrar con él.
  2. **Los permisos pasan a ser un objetivo.** Antes cambiarlos exigía acceso al código;
     ahora basta una sesión. Por eso cada cambio se audita con **el antes y el después**:
     solo con el después no se puede reconstruir qué se concedió y qué se quitó.
  3. **Se pierde el «abro un archivo y sé quién puede qué».** Se compensa con la matriz
     completa en la pantalla de roles.

  ### La regla de administración, reescrita

  Antes era «no dejar el sistema sin administradores», mirando el rol por su nombre. Con
  roles editables eso dejó de alcanzar: puede haber un rol «Coordinación» con la
  capacidad de gestionar usuarios y ningún «administrador» activo, y ser una instalación
  perfectamente sana. La regla nueva no mira **cómo se llama** el rol sino **si queda
  alguien activo con las capacidades críticas**.

  ### Dos defectos que la etapa introdujo y se corrigieron

  - `flujo.bandeja_de()` decidía por rol (`¿es supervisor?`). Un rol nuevo con permiso de
    aprobar habría visto la bandeja vacía y el botón de aprobar disponible. Ahora se
    arma por capacidades y acumula: un rol puede tener varias a la vez.
  - La barra lateral condicionaba **todo** el bloque de administración a
    `gestionar_usuarios`. Un rol «Auditor» con solo `ver_auditoria` nunca habría visto el
    enlace a la única pantalla que sí puede abrir. Ahora cada enlace se condiciona a su
    propia capacidad.

  ### Detalles de diseño

  - **La clave del rol no cambia nunca**, el nombre visible sí. La clave es lo que quedó
    escrito en la auditoría: si cambiara, los registros viejos apuntarían a un rol que ya
    no existe con ese nombre.
  - **Los roles de fábrica no se borran**, aunque sus capacidades sí se editan. Hay
    reglas del código que los nombran.
  - **Un rol con usuarios no se borra**: habría que decidir por ellos a qué rol pasan, y
    esa es una decisión de la organización.
  - **`sincronizar_roles()` solo crea lo que falta.** Si reasignara las capacidades de
    fábrica en cada arranque, desharía en silencio cada permiso quitado a propósito.
  - **`ClaveRol` es un `StrEnum`**: `ClaveRol.ADMINISTRADOR == "administrador"` da
    verdadero, así que el código compara contra la clave guardada sin convertir de un
    lado a otro.
  - **Caché por petición.** `puede()` se llama decenas de veces por pantalla; las
    capacidades del rol se resuelven una vez y quedan en `g`. Fuera de una petición se
    consulta directo, que es lo correcto: ahí no hay ciclo donde cachear sin arriesgar
    datos viejos.
  - **Una capacidad que llega del formulario y no está en el catálogo se descarta**, como
    cualquier entrada no confiable.

  ### El defecto que solo apareció en PostgreSQL

  Correr la suite en los dos motores se pagó en esta misma etapa. `usuarios_con_actividad()`
  usaba `JOIN ... GROUP BY usuario.id`, y desde que `Usuario.rol` se carga con
  `lazy="joined"`, SQLAlchemy suma las columnas del rol a la consulta. **PostgreSQL exige
  que todas estén en el `GROUP BY`; SQLite no.** Las 272 pruebas pasaban en SQLite y dos
  fallaban en PostgreSQL — es decir, el defecto solo se habría visto en producción.

  Se reescribió con `IN (subconsulta)`, que además saca el problema de raíz: no queda un
  agrupamiento que haya que mantener sincronizado con las columnas que la carga de
  relaciones agregue en el futuro.

  ### Migración

  El tipo de la columna `usuario.rol` cambió de enum a clave foránea. **Una base creada
  antes de esta etapa hay que rehacerla** (`flask iniciar-bd` sobre una base vacía). El
  sistema no estaba desplegado, así que no había datos productivos que preservar; si los
  hubiera, esto exigiría una migración de Alembic con el traspaso de la columna.

  ### Dos defectos de entorno corregidos de paso

  - **`pytest` a secas fallaba** con `No module named 'app'` y solo andaba
    `python -m pytest`, que agrega el directorio actual al path. La diferencia entre los
    dos comandos no es evidente y hace perder una tarde. `pytest.ini` declara ahora
    `pythonpath = .` y las dos formas funcionan igual.
  - **`pandas` estaba en `requirements.txt` y no se usa en ninguna parte.** Peor: la
    versión fijada no tiene ruedas para Python 3.13, así que pip intentaba compilarla
    desde fuente. Se quitó.

### Defecto encontrado y corregido en 2C

La auditoría exigía un contexto HTTP para escribir. Eso la volvía inutilizable desde un
comando de consola o desde el procesamiento de una importación en segundo plano —
justamente lo que necesita la etapa 2B. Ahora registra igual, con el actor pasado en
forma explícita, y deja constancia de que el origen fue un proceso interno.

**Orden recomendado de defensa:** la etapa 2 es la que decide si el proyecto funciona. La 5
es la que lo vuelve memorable. No arrancar por la 5.
