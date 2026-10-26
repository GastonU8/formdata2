"""Consulta de la bitácora de auditoría.

Este módulo **solo lee**. La bitácora es append-only: se escribe desde
`security.registrar()` y no se edita ni se borra desde la aplicación. Una bitácora
que la aplicación puede modificar no prueba nada, porque el mismo sistema que
registró la acción pudo haber cambiado el registro.

Su razón de ser es poder contestar tres preguntas concretas:

* ¿Quién tocó este tratamiento, y cuándo?
* ¿Qué hizo esta persona en el sistema?
* ¿Hubo intentos de entrar que fallaron?

La tercera es la que justifica registrar también los fracasos: una ráfaga de
ingresos fallidos contra la misma cuenta es la única señal temprana que tiene un
sistema chico de que alguien está probando contraseñas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select

from ..extensions import db
from ..models import RegistroAuditoria, Usuario

POR_PAGINA = 50
DIAS_RESUMEN = 7

# Cómo se lee cada acción en la pantalla. Lo que no está acá se muestra tal cual:
# preferimos un nombre técnico a inventar una traducción que diga otra cosa.
# Los nombres salen de leer las llamadas a `registrar()` en el código, no de
# suponerlos: al escribir esta tabla de memoria quedaron cinco acciones sin traducir
# y otras cinco traducidas que nunca se emiten.
ETIQUETAS = {
    # El ingreso usa una sola acción y distingue el resultado con la bandera `exito`,
    # así que «Ingreso» cubre tanto el logrado como el fallido; la columna de estado
    # muestra cuál fue.
    "login": "Ingreso",
    "login_bloqueado": "Ingreso con cuenta bloqueada",
    "login_inactivo": "Ingreso con cuenta inactiva",
    "logout": "Cierre de sesión",
    "solicitud_enviar": "Solicitud enviada",
    "solicitud_aprobar": "Solicitud aprobada",
    "solicitud_rechazar": "Solicitud rechazada",
    "solicitud_corregir": "Solicitud corregida",
    "solicitud_tomar": "Ensayo tomado",
    "solicitud_finalizar": "Ensayo finalizado",
    "solicitud_anular": "Solicitud anulada",
    "cambio_password": "Cambio de contraseña",
    "password_restablecida": "Contraseña restablecida",
    "usuario_creado": "Alta de usuario",
    "usuario_editado": "Edición de usuario",
    "usuario_estado": "Cambio de estado de usuario",
    "usuario_desbloqueado": "Desbloqueo de usuario",
    "rol_creado": "Alta de rol",
    "rol_permisos": "Cambio de permisos de un rol",
    "rol_eliminado": "Eliminación de rol",
    "importacion_iniciada": "Importación iniciada",
    "importacion_finalizada": "Importación finalizada",
    "mapeo_confirmado": "Producto mapeado",
    "mapeo_descartado": "Nombre descartado",
    "mapeo_deshecho": "Mapeo deshecho",
    "producto_creado": "Alta de producto",
    "producto_editado": "Edición de producto",
    "producto_estado": "Cambio de estado de producto",
    "producto_fusionado": "Fusión de productos",
    "exportacion_excel": "Exportación a Excel",
    "exportacion_pdf": "Exportación a PDF",
    "consulta_ia": "Consulta al asistente",
}


def etiqueta(accion: str) -> str:
    return ETIQUETAS.get(accion, accion.replace("_", " ").capitalize())


@dataclass
class Filtros:
    texto: str = ""
    accion: str = ""
    usuario_id: int | None = None
    entidad: str = ""
    solo_fallidos: bool = False
    desde: date | None = None
    hasta: date | None = None

    @property
    def hay_alguno(self) -> bool:
        return any(
            [self.texto, self.accion, self.usuario_id, self.entidad, self.solo_fallidos,
             self.desde, self.hasta]
        )

    def descripcion(self) -> list[str]:
        partes = []
        if self.accion:
            partes.append(etiqueta(self.accion))
        if self.solo_fallidos:
            partes.append("solo intentos fallidos")
        if self.texto:
            partes.append(f"«{self.texto}»")
        if self.desde:
            partes.append(f"desde {self.desde:%d/%m/%Y}")
        if self.hasta:
            partes.append(f"hasta {self.hasta:%d/%m/%Y}")
        return partes


def leer_filtros(argumentos) -> Filtros:
    def fecha(nombre):
        valor = (argumentos.get(nombre) or "").strip()
        try:
            return datetime.strptime(valor, "%Y-%m-%d").date()
        except ValueError:
            return None

    identificador = (argumentos.get("usuario") or "").strip()
    return Filtros(
        texto=(argumentos.get("texto") or "").strip()[:120],
        accion=(argumentos.get("accion") or "").strip()[:80],
        usuario_id=int(identificador) if identificador.isdigit() else None,
        entidad=(argumentos.get("entidad") or "").strip()[:80],
        solo_fallidos=(argumentos.get("fallidos") or "") == "1",
        desde=fecha("desde"),
        hasta=fecha("hasta"),
    )


def _consulta(filtros: Filtros):
    consulta = select(RegistroAuditoria)

    if filtros.accion:
        consulta = consulta.where(RegistroAuditoria.accion == filtros.accion)
    if filtros.usuario_id:
        consulta = consulta.where(RegistroAuditoria.usuario_id == filtros.usuario_id)
    if filtros.entidad:
        consulta = consulta.where(RegistroAuditoria.entidad == filtros.entidad)
    if filtros.solo_fallidos:
        consulta = consulta.where(RegistroAuditoria.exito.is_(False))
    if filtros.desde:
        consulta = consulta.where(RegistroAuditoria.ocurrido_en >= filtros.desde)
    if filtros.hasta:
        # `hasta` se interpreta inclusivo: quien filtra «hasta el 20» espera ver lo
        # del 20, no todo lo anterior a su medianoche.
        consulta = consulta.where(
            RegistroAuditoria.ocurrido_en < filtros.hasta + timedelta(days=1)
        )
    if filtros.texto:
        patron = f"%{filtros.texto}%"
        consulta = consulta.where(
            or_(
                RegistroAuditoria.accion.ilike(patron),
                RegistroAuditoria.email_intentado.ilike(patron),
                RegistroAuditoria.entidad.ilike(patron),
                RegistroAuditoria.entidad_id.ilike(patron),
            )
        )
    return consulta


@dataclass
class Pagina:
    items: list
    total: int
    pagina: int
    por_pagina: int

    @property
    def paginas(self) -> int:
        return max(1, -(-self.total // self.por_pagina))

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina < self.paginas

    @property
    def hay_anterior(self) -> bool:
        return self.pagina > 1


def buscar(filtros: Filtros, pagina: int = 1, por_pagina: int = POR_PAGINA) -> Pagina:
    pagina = max(1, pagina)
    base = _consulta(filtros)

    total = db.session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    items = list(
        db.session.scalars(
            base.order_by(RegistroAuditoria.ocurrido_en.desc(), RegistroAuditoria.id.desc())
            .limit(por_pagina)
            .offset((pagina - 1) * por_pagina)
        )
    )
    return Pagina(items=items, total=total, pagina=pagina, por_pagina=por_pagina)


def acciones_registradas() -> list[tuple[str, str]]:
    """Las acciones que existen en la bitácora, para armar el desplegable.

    Se leen de los datos y no de la lista de etiquetas: si mañana alguien agrega una
    acción nueva y se olvida de traducirla, igual va a poder filtrarse por ella.
    """
    filas = db.session.scalars(
        select(RegistroAuditoria.accion).group_by(RegistroAuditoria.accion).order_by(RegistroAuditoria.accion)
    ).all()
    return [(accion, etiqueta(accion)) for accion in filas]


def usuarios_con_actividad() -> list[Usuario]:
    """Quiénes aparecen en la bitácora, para armar el desplegable.

    Se filtra con `IN (subconsulta)` y no con `JOIN ... GROUP BY`. El `JOIN` era
    correcto en SQLite y **fallaba en PostgreSQL**: desde que `Usuario.rol` se carga
    con `lazy="joined"`, SQLAlchemy suma las columnas del rol a la consulta y
    PostgreSQL exige que todas estén en el `GROUP BY`. SQLite no lo exige, así que el
    defecto solo aparecía en producción.

    Con la subconsulta el problema desaparece de raíz: no hay agrupamiento que
    mantener sincronizado con las columnas que la carga de relaciones agregue después.
    """
    return list(
        db.session.scalars(
            select(Usuario)
            .where(
                Usuario.id.in_(
                    select(RegistroAuditoria.usuario_id).where(
                        RegistroAuditoria.usuario_id.is_not(None)
                    )
                )
            )
            .order_by(Usuario.nombre)
        )
    )


@dataclass(frozen=True)
class Resumen:
    eventos: int
    fallidos: int
    usuarios_activos: int
    dias: int


def resumen(dias: int = DIAS_RESUMEN) -> Resumen:
    """Actividad de los últimos días, para la cabecera de la pantalla."""
    desde = datetime.now().astimezone() - timedelta(days=dias)
    recientes = select(RegistroAuditoria).where(RegistroAuditoria.ocurrido_en >= desde).subquery()

    return Resumen(
        eventos=db.session.scalar(select(func.count()).select_from(recientes)) or 0,
        fallidos=db.session.scalar(
            select(func.count()).select_from(recientes).where(recientes.c.exito.is_(False))
        ) or 0,
        usuarios_activos=db.session.scalar(
            select(func.count(func.distinct(recientes.c.usuario_id))).select_from(recientes)
        ) or 0,
        dias=dias,
    )


def historia_de(entidad: str, entidad_id: int | str, limite: int = 30) -> list[RegistroAuditoria]:
    """Todo lo que se le hizo a un registro concreto. Responde «quién tocó esto»."""
    return list(
        db.session.scalars(
            select(RegistroAuditoria)
            .where(
                RegistroAuditoria.entidad == entidad,
                RegistroAuditoria.entidad_id == str(entidad_id),
            )
            .order_by(RegistroAuditoria.ocurrido_en.desc())
            .limit(limite)
        )
    )
