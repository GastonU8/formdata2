# Manual de uso de FormData

Para las personas que van a usar el sistema todos los días. No hace falta saber
nada de programación para seguirlo.

---

## Qué es FormData

Un repositorio de los ensayos de compatibilidad de mezclas del laboratorio. Reemplaza
la planilla de Excel que se venía usando y agrega tres cosas que la planilla no podía
dar: **búsqueda por principio activo**, **un circuito de aprobación con trazabilidad**
y **informes que se pueden reproducir**.

---

## Entrar por primera vez

1. Tu administrador te da un usuario y una **contraseña inicial de un solo uso**.
2. Al entrar, el sistema te obliga a cambiarla. Elegí una de al menos 12 caracteres.
3. Después de cambiarla, la sesión se cierra y tenés que volver a entrar con la nueva.
   Es a propósito: así queda claro cuál es tu contraseña de ahora en más.

Si te equivocás cinco veces seguidas, la cuenta se bloquea un rato. No es un error:
es la protección contra alguien probando contraseñas. Pedile a un administrador que
te levante el bloqueo.

**Tu contraseña no la conoce nadie**, ni el administrador. Si la perdés, se
restablece con una nueva de un solo uso y ese restablecimiento queda registrado.

---

## Qué puede hacer cada rol

| Rol | Puede |
|---|---|
| **Comercial** | Consultar, pedir ensayos nuevos, ver el tablero, exportar, preguntarle al asistente |
| **Supervisor** | Todo lo anterior, más aprobar o rechazar solicitudes |
| **Técnico (laboratorio)** | Consultar, importar el Excel, normalizar productos, administrar el maestro, cargar resultados |
| **Gerencia** | Consultar, tablero, exportar |
| **Administrador** | Todo, más usuarios y bitácora |

Si una opción no te aparece en el menú, es porque tu rol no la tiene.

---

## Consultar ensayos

En **Tratamientos** buscás combinando lo que necesites: producto o principio activo,
cultivo, tipo de ensayo, cliente, rango de fechas.

Lo importante: **buscar por «Glifosato» encuentra los ensayos donde se escribió
«Round Up Full»**, porque el sistema sabe que ese producto tiene ese principio
activo. Eso es lo que la planilla no podía hacer.

Desde cada ensayo se ve la mezcla completa, las mediciones y —si vino del circuito—
quién lo pidió, quién lo aprobó y quién lo ejecutó.

---

## Pedir un ensayo nuevo (circuito)

El recorrido es: **borrador → pendiente → aprobada → en ensayo → finalizada**.

1. **Comercial** carga la solicitud con el cliente, el cultivo y los productos de la
   mezcla, y la envía.
2. **Supervisor** la aprueba o la rechaza. Para rechazar hay que escribir el motivo:
   sin motivo el sistema no deja.
3. **Laboratorio** la toma, hace el ensayo y carga el resultado.
4. Al cargar el resultado, la solicitud queda finalizada automáticamente.

Cada cambio de estado queda registrado con quién y cuándo. Una solicitud rechazada se
puede corregir y volver a enviar.

---

## Importar el Excel histórico *(rol técnico)*

En **Importar Excel** subís el archivo. El sistema:

- lee las hojas de tratamientos y de productos;
- **no duplica**: si reimportás el mismo archivo, actualiza en lugar de crear de nuevo;
- te devuelve un informe de qué filas entraron y cuáles no, con el motivo.

Si una fila no entró, el informe dice por qué. Corregila en la planilla y volvé a
importar: solo se van a agregar los cambios.

---

## Normalizar los productos sin mapear *(rol técnico)*

Después de importar, el tablero muestra el porcentaje de normalización. Todo lo que
esté pendiente son ensayos que **no aparecen** cuando alguien busca por principio
activo.

En **Sin mapear** (el número al lado es cuánto falta):

1. Cada tarjeta es un nombre escrito en la planilla, con cuántas veces aparece y en
   qué ensayos.
2. Elegí una salida:
   - **Es este** sobre una sugerencia. La lista viene ordenada por parecido: verde es
     confianza alta, ámbar es media. **La ámbar leela antes de confirmar.**
   - **Buscar otro producto** si ninguna sugerencia sirve.
   - **Darlo de alta en el maestro** si el producto existe pero nunca se cargó.
   - **No es un producto** para celdas con «s/d», una dosis suelta o texto suelto.
3. Todo tiene **Deshacer** al lado.

**Empezá por arriba**: la lista está ordenada por cuántas filas arrastra cada nombre,
así que las primeras decisiones son las que más suben la calidad del dato.

Una decisión vale para siempre: la próxima importación ya no vuelve a preguntar por
ese nombre.

---

## El tablero y los informes

El tablero (la pantalla de inicio) y la búsqueda comparten los mismos filtros. Elegí
el recorte y:

- Los gráficos se recalculan solos. **Cada barra es un enlace**: al hacer clic se abre
  la lista de ensayos que la componen.
- **Ver los valores**, debajo de cada gráfico, abre la tabla con los números exactos.
- **Exportar a Excel** baja cuatro hojas: los criterios usados, el resumen, una hoja
  ancha (un ensayo por fila) y una larga (un producto por fila, lista para tabla
  dinámica).
- **Informe en PDF** arma algo para circular.
- `Ctrl+P` imprime el tablero sin el menú.

Lo que se descarga es **lo que estás mirando**, no todo. Y el archivo abre diciendo
quién lo generó, cuándo y con qué filtros.

> **Atención:** si el recorte tiene productos sin normalizar, aparece un aviso. Los
> rankings de producto los subestiman. Conviene resolverlos antes de mandar un
> informe afuera.

---

## Preguntarle al asistente

En **Asistente** preguntás en lenguaje natural: «¿cuántos ensayos de soja dieron
precipitado?», «¿cuáles son los productos más usados?».

Tres cosas que conviene saber:

- **Responde solo con datos del sistema.** Si no encuentra nada, lo dice. No completa
  con conocimiento general de agroquímicos.
- **Muestra de dónde salió cada respuesta**, con enlaces a los ensayos. Están para
  que vayas a comprobar.
- **Si menciona una cifra que no está en los datos consultados, te avisa** que la
  revises antes de usarla.

El asistente **no recomienda mezclas ni opina sobre aplicación**. Informa lo que
dicen los ensayos registrados; decidir es del técnico.

---

## Administrar usuarios *(quien pueda gestionar usuarios)*

En **Usuarios**:

- **Nuevo usuario**: el sistema genera una contraseña inicial y la muestra **una sola
  vez**. Copiala y entregala por un canal aparte —no por el mismo correo donde está
  el usuario—.
- **Suspender** es temporal (una licencia, una cuenta bajo sospecha). **Dar de baja**
  es definitivo.
- **Nunca se borra un usuario.** Su nombre tiene que seguir explicando lo que hizo en
  la bitácora. Por el mismo motivo, **un email no se reutiliza** entre personas, ni
  después de una baja.
- **Restablecer contraseña** genera una nueva de un solo uso.
- **Levantar bloqueo** desbloquea sin cambiar la contraseña, para quien se equivocó
  varias veces y ya la recordó.

El sistema no te deja quitarte el rol de administrador si sos el único activo: te
quedarías sin poder administrar nada.

---

## Roles y permisos *(quien pueda gestionar roles)*

En **Roles** configurás qué puede hacer cada rol, sin tocar código.

La tabla de arriba es la matriz completa: una fila por capacidad, una columna por rol,
un punto donde está concedida. Sirve para responder de un vistazo «¿quién puede
aprobar solicitudes?».

- **Nuevo rol**: le ponés nombre, una descripción de para qué es, y marcás las
  capacidades. Después se lo asignás a la gente desde *Usuarios*.
- **Editar**: entrás a un rol y marcás o desmarcás. **Lo que dejes sin marcar se
  quita**: en un formulario de casillas, la ausencia es la orden.
- Los roles que trae el sistema **no se borran**, pero sus capacidades sí se editan.
- Un rol **con usuarios asignados no se puede borrar**. Cambiales el rol primero.

Dos capacidades están resaltadas: *gestionar usuarios* y *gestionar roles*. El
sistema **no te va a dejar** guardar un cambio que deje a la instalación sin ningún
rol con usuarios activos que las tenga — porque después no habría forma de volver.

Todo cambio de permisos queda en la bitácora, con qué se concedió y qué se quitó.

## La bitácora *(quien pueda ver auditoría)*

En **Auditoría** está el registro de las acciones sensibles: ingresos, cambios de
estado, importaciones, mapeos, exportaciones, consultas al asistente.

Sirve para tres preguntas concretas:

- **¿Quién tocó este ensayo?** Filtrá por entidad.
- **¿Qué hizo esta persona?** Filtrá por usuario, o abrí su ficha.
- **¿Hubo intentos de entrar que fallaron?** Marcá *Solo intentos fallidos*.

Los intentos fallidos se muestran resaltados. Varios seguidos contra la misma cuenta
es la señal de que alguien está probando contraseñas.

La bitácora **no se puede editar ni borrar** desde el sistema. Es a propósito: una
bitácora que la aplicación puede modificar no prueba nada.

---

## Preguntas frecuentes

**Busqué un producto y no aparece ningún ensayo.**
Puede estar sin normalizar. Fijate el porcentaje en el tablero y revisá la bandeja
*Sin mapear*.

**Los números del tablero no coinciden con lo que esperaba.**
Fijate qué filtros tenés puestos: el tablero muestra el recorte, no todo. El texto
debajo de los filtros dice cuál es.

**Exporté un Excel hace un mes y no me acuerdo con qué filtros.**
Está en la primera hoja del archivo, «Criterios».

**El asistente me dio una respuesta con un cartel de revisar.**
Significa que mencionó una cifra que no aparece en los datos que consultó.
Verificala en las fuentes que muestra abajo antes de usarla.

**Perdí la contraseña inicial de un usuario que acabo de crear.**
Restablecela desde su ficha. No se puede recuperar: el sistema no la guarda en claro.
