# Arquitectura y patrones de diseño

Documento de referencia para la memoria y para la defensa. Cada afirmación de acá
se puede comprobar abriendo el archivo que se cita.

---

## 1. La arquitectura en una frase

**Monolito modular en capas**, con la lógica de negocio concentrada en una capa de
servicios que no depende de Flask, autorización por capacidades y una máquina de
estados para el circuito de aprobación.

---

## 2. Las capas

```
┌──────────────────────────────────────────────────────────┐
│  blueprints/          1.094 líneas                        │
│  HTTP: leer la petición, elegir plantilla, responder      │
└───────────────────────────┬──────────────────────────────┘
                            │ llama
┌───────────────────────────▼──────────────────────────────┐
│  services/            5.191 líneas                        │
│  Toda la lógica del negocio. No importa flask.request     │
└───────────────────────────┬──────────────────────────────┘
                            │ usa
┌───────────────────────────▼──────────────────────────────┐
│  models/                768 líneas                        │
│  Estructura de datos y relaciones. Sin lógica de proceso  │
└──────────────────────────────────────────────────────────┘

  security/  162 líneas — transversal: autorización y auditoría
```

**Las dependencias van en una sola dirección.** Un servicio nunca importa un
blueprint; un modelo nunca importa un servicio.

### Por qué esta arquitectura y no otra

| Alternativa | Por qué se descartó |
|---|---|
| Microservicios | El laboratorio tiene cinco usuarios. La complejidad operativa la pagaría alguien que no está para mantenerla. |
| Lógica en las vistas (CRUD directo) | Obliga a probar todo por HTTP, y la misma regla termina repetida en cada pantalla que la necesita. |
| Modelo rico (lógica en las entidades) | Las operaciones de este dominio cruzan varias entidades a la vez (importar toca tratamiento, mezcla, detalle, producto y mapeo). En un modelo rico, esa lógica no tiene dueño claro. |

### La prueba de que la separación es real

Las vistas tienen **una quinta parte** del código de los servicios. Una vista típica
lee la query string, llama a un servicio y renderiza. Por eso **la mayoría de los 238
tests corren sin cliente HTTP**: llaman al servicio directamente.

---

## 3. Patrones de diseño aplicados

### 3.1 Application Factory
**Dónde:** `crear_app(entorno)` en `app/__init__.py`
**Qué resuelve:** permite construir una aplicación de pruebas con SQLite en memoria
y otra de producción con PostgreSQL dentro del mismo proceso. Sin esto los tests no
se pueden aislar entre sí.

### 3.2 Capa de servicios (Service Layer)
**Dónde:** `app/services/`
**Qué resuelve:** la lógica no vive ni en el modelo ni en la vista. Es lo que permite
que la misma regla la usen una pantalla, un comando de consola y el asistente sin
duplicarse.

### 3.3 Máquina de estados (State)
**Dónde:** `app/services/flujo.py`
**Qué resuelve:** las transiciones del circuito son **datos**
(`TRANSICIONES: dict[str, Transicion]`), no condicionales desparramados. Toda
transición pasa por `ejecutar()`, que valida estado de origen, permiso, autoría y
motivo antes de escribir. **Ninguna vista toca `estado_flujo` a mano**, así que no
hay forma de saltear la regla desde una pantalla nueva.

### 3.4 Autorización basada en capacidades
**Dónde:** `app/security/roles.py` (catálogo) + tablas `rol` y `permiso_rol` (asignación)
**Qué resuelve:** se pregunta «¿puede aprobar?» y no «¿es supervisor?».

La división es la decisión importante:

- **El catálogo de capacidades vive en el código.** Cada una existe porque hay una
  vista que la exige con `@requiere(...)`. Inventar capacidades desde una pantalla
  produciría permisos que no gobiernan nada.
- **Qué rol tiene cada capacidad vive en la base**, editable desde
  `/administracion/roles`. Eso es lo que cambia con la organización.

Que la pregunta sea por capacidad y no por rol es lo que hace que un rol creado
después funcione en todo el sistema —bandeja, botones, permisos de vista— sin tocar
una línea.

### 3.5 Decorator
**Dónde:** `@requiere("aprobar_solicitud")` sobre cada vista
**Qué resuelve:** el permiso se declara al lado de la ruta y no se puede olvidar en
el medio del cuerpo de la función.

### 3.6 Unit of Work (unidad de trabajo)
**Dónde:** `app/unidad_de_trabajo.py`
**Qué resuelve:** una operación de negocio, una transacción.

Antes de este patrón, cada servicio confirmaba por su cuenta y, cuando una operación
componía a otra, había que avisarle a la de adentro que **no** confirmara con un
`confirmar=False` que viajaba entre servicios. Ese booleano era una unidad de trabajo
simulada a mano, con dos problemas:

- **Se olvidaba.** Un `commit()` de más en el medio de una operación compuesta deja
  la mitad escrita y la otra mitad no.
- **Obligaba a deshacer a mano.** Nueve lugares llamaban a `rollback()`, incluidas
  las vistas: lógica de transacción escrita en la capa HTTP.

El contexto es **reentrante**: `solicitudes.registrar()` llama a `flujo.ejecutar()`,
los dos abren su transacción y solo la de más afuera confirma. Un servicio no tiene
que saber si es la operación entera o parte de otra.

La **auditoría se escribe en la misma transacción que la acción que registra**. Si
fuera aparte, podría quedar una acción sin registro —o peor, un registro de algo que
después se deshizo—, y eso volvería inservible a la bitácora.

### 3.7 Strategy + Inyección de dependencias
**Dónde:** `Proveedor` en `app/services/proveedor_ia.py`
**Qué resuelve:** tres implementaciones intercambiables (Anthropic, sin modelo,
falso). Es lo que hace que 50 tests del asistente corran sin internet ni clave de
API, y que cambiar de modelo —o pasar a uno local— no toque el motor.

### 3.8 Command + Registry
**Dónde:** `CATALOGO` en `app/services/herramientas.py`
**Qué resuelve:** el asistente elige de una lista cerrada de nueve consultas ya
programadas; **no genera SQL**. `ejecutar()` es el único punto por donde el modelo
toca la base, así que auditar qué puede hacer es leer un diccionario.

### 3.9 Objeto de parámetros (Parameter Object)
**Dónde:** `Filtros` en `app/services/consultas.py`
**Qué resuelve:** un solo objeto atraviesa búsqueda, tablero y exportación, y los
tres se calculan sobre el mismo conjunto de ids. Es lo que garantiza que el número
del gráfico y el de la lista que se abre al hacer clic sean el mismo.

### 3.10 DTO / objetos de valor inmutables
**Dónde:** `Serie`, `Punto`, `Sugerencia`, `Resumen`, `Transicion`, `Fuente`
**Qué resuelve:** los servicios no devuelven modelos crudos a las plantillas.

### 3.11 Null Object
**Dónde:** `ProveedorSinModelo`
**Qué resuelve:** el asistente funciona sin clave de API en lugar de fallar o
esconderse. La pantalla avisa en qué modo está.

### 3.12 Policy / Registry de permisos en datos
**Dónde:** `app/services/roles.py`
**Qué resuelve:** la política de autorización pasa de ser código a ser configuración,
con las salvaguardas que eso exige: ningún cambio puede dejar la instalación sin un
rol **con usuarios activos** que tenga las capacidades críticas, y cada cambio se
audita con el antes y el después.

### 3.13 Idempotencia por clave natural
**Dónde:** `app/services/importacion.py`
**Qué resuelve:** reimportar el mismo Excel actualiza en lugar de duplicar, usando
`numero_tratamiento` como identidad.

---

## 4. Lo que deliberadamente NO se usó

Decirlo es más fuerte que inventar un patrón: muestra que la decisión fue consciente.

| Patrón | Por qué no |
|---|---|
| **Repository** | SQLAlchemy ya *es* la capa de acceso a datos. Envolverla en repositorios a esta escala es ceremonia sin beneficio. |
| **Arquitectura hexagonal** | Hay un puerto real (el proveedor de IA), pero los servicios importan `db` directamente. Llamarla hexagonal sería exagerar. |
| **CQRS** | Hay separación entre módulos de lectura (`consultas`, `analitica`) y de escritura (`importacion`, `mapeo`, `flujo`), pero es separación de responsabilidades: no hay dos modelos ni bus de eventos. |
| **Event Sourcing** | `HistorialEstado` se le parece, pero el estado actual vive en su columna y no se reconstruye de los eventos. Es una bitácora, no un origen de verdad. |
| **DDD táctico** | El dominio no tiene la complejidad conceptual que justifique agregados, repositorios y objetos de valor por todos lados. |

---

## 5. Decisiones transversales

- **Ninguna dependencia de CDN.** htmx se sirve desde `app/static/js/`. El
  laboratorio puede estar sin internet, y así la CSP se cierra a `script-src 'self'`.
- **Nada específico de un motor en la lógica.** El agrupamiento por período se hace
  en Python y no con `strftime` (SQLite) o `to_char` (PostgreSQL). **Las 238 pruebas
  corren en los dos motores**, apuntando `FORMDATA_TEST_DATABASE_URI` a PostgreSQL.
- **Degradación sin JavaScript.** Cada interacción con htmx tiene su equivalente por
  formulario común, servido por la misma vista. No hay una versión degradada que
  nadie prueba.
- **Gráficos en SVG generado en el servidor.** La misma serie alimenta pantalla,
  impresión y PDF.

---

## 6. Cómo se defiende en dos minutos

1. **Arquitectura:** monolito modular en capas. La lógica está en servicios que no
   dependen de Flask; por eso se puede probar sin HTTP y por eso hay 238 tests.
2. **El patrón que ordena el circuito:** máquina de estados. Las transiciones son
   datos y hay un solo punto de entrada que valida todo.
3. **El patrón que ordena la persistencia:** unidad de trabajo. Una operación de
   negocio es una transacción, y la auditoría entra en la misma.
4. **El patrón que hace seguro al asistente:** catálogo cerrado de herramientas. El
   modelo elige, no escribe SQL.
5. **Lo que no se usó y por qué:** no hay Repository ni hexagonal; SQLAlchemy ya
   cumple ese rol y agregar capas a esta escala es costo sin beneficio.
