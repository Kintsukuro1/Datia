"""Gobernanza del módulo BI.

Toda la información que el informe o los paneles pueden usar sale de
`DynamicSchemaPruningService.get_authorized_schema_prompt`, la misma fuente que
usa el chat. No se consultan las tablas de permisos a mano y no se confía en
nombres que lleguen desde el navegador.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import is_admin_user, require_assigned_role
from app.core.constants import ADMIN_ROLES
from app.modules.admin_catalog.models import CorporateConnection, RoleTablePermission
from app.modules.auth.models import User
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService

# Columnas que nunca se muestran, ni siquiera al administrador. Es una lista
# local de defensa en profundidad: la política real de cada perfil vive en
# `role_column_permissions`.
HIDDEN_COLUMNS = frozenset({
    "password",
    "hashed_password",
    "encrypted_password",
    "token",
    "api_key",
    "secret",
    "secret_key",
})

# Nombres que el PHP usaba para no elegir una columna de período como
# "Agrupar por" o "Categoría". Se conservan para que la autoselección no
# proponga mes/año como dimensiones de negocio.
_PERIOD_NAME_RE = re.compile(
    r"periodo|period|^mes$|_mes$|anio|año|year|month|fecha|date|^id$|^id_|_id$",
    re.IGNORECASE,
)

_DATE_HINTS = ("DATE", "TIME")
_NUMERIC_HINTS = ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE", "MONEY", "SERIAL")


class BIAccessDenied(HTTPException):
    """403 de gobernanza del módulo BI.

    Se distingue de otras `HTTPException` para que el servicio pueda auditar la
    denegación exactamente una vez. El 403 de `require_assigned_role` ya deja su
    propio `RECHAZADO_RBAC` y no pasa por esta clase.
    """

    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


@dataclass(frozen=True)
class BIColumnMeta:
    name: str
    type: str
    kind: str
    masked: bool = False


@dataclass
class BITableMeta:
    name: str
    columns: List[BIColumnMeta] = field(default_factory=list)

    def column(self, name: str) -> Optional[BIColumnMeta]:
        lowered = (name or "").lower()
        for column in self.columns:
            if column.name.lower() == lowered:
                return column
        return None


@dataclass
class BIContext:
    connection: CorporateConnection
    role_name: str
    is_admin: bool
    tables: List[BITableMeta]
    physical_tables: Set[str]
    blocked_columns: Set[str]
    masked_columns: Set[str]
    allowed_tables: Set[str]
    table_columns: Dict[str, List[str]]

    def table(self, name: str) -> Optional[BITableMeta]:
        lowered = (name or "").lower()
        for table in self.tables:
            if table.name.lower() == lowered:
                return table
        return None


def column_kind(name: str, data_type: str) -> str:
    """Clasifica una columna como fecha, número o texto.

    Es la misma heurística que usaba el panel PHP, con la misma intención:
    proponer campos razonables sin consultar datos. El tipo manda; si el motor
    no lo informa, el nombre de la columna es la última pista.
    """
    type_upper = (data_type or "").upper()
    if any(hint in type_upper for hint in _DATE_HINTS):
        return "date"
    if any(hint in type_upper for hint in _NUMERIC_HINTS):
        return "number"
    if any(hint in (name or "").lower() for hint in ("fecha", "date")):
        return "date"
    return "text"


def is_period_like(name: str) -> bool:
    return bool(_PERIOD_NAME_RE.search(name or ""))


def list_authorized_connections(db: Session, user: User) -> List[CorporateConnection]:
    """Bases que el perfil puede usar en el módulo BI.

    Un no-admin solo ve las bases donde tiene al menos una tabla concedida. El
    admin ve todas las registradas. La comprobación es de PERMISOS, no de
    introspección: una base concedida pero sin tablas físicas se filtra después
    al leer el esquema y no se convierte en acceso a otra base.
    """
    role_name = require_assigned_role(user, db, "Panel BI: listar bases", "")
    is_admin = is_admin_user(user) or role_name in ADMIN_ROLES
    if is_admin:
        return (
            db.query(CorporateConnection)
            .order_by(CorporateConnection.id.asc())
            .all()
        )
    if user.role_id is None:
        return []
    connection_ids = {
        row[0]
        for row in db.query(RoleTablePermission.connection_id)
        .filter(
            RoleTablePermission.role_id == user.role_id,
            RoleTablePermission.is_allowed == True,  # noqa: E712
        )
        .distinct()
        .all()
    }
    if not connection_ids:
        return []
    return (
        db.query(CorporateConnection)
        .filter(CorporateConnection.id.in_(connection_ids))
        .order_by(CorporateConnection.id.asc())
        .all()
    )


def load_context(
    db: Session,
    user: User,
    connection_id: int,
    action: str,
) -> BIContext:
    """Resuelve conexión, rol y esquema autorizado para este usuario."""
    connection = (
        db.query(CorporateConnection)
        .filter(CorporateConnection.id == connection_id)
        .first()
    )
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La base de datos {connection_id} no está registrada en Datia.",
        )

    role_name = require_assigned_role(user, db, action, connection.name or "")
    is_admin = is_admin_user(user) or role_name in ADMIN_ROLES

    info: Dict[str, Any] = DynamicSchemaPruningService.get_authorized_schema_prompt(
        db=db,
        role_id=user.role_id,
        user_role=role_name,
        connection_id=connection.id,
        is_admin=is_admin,
    )

    physical_tables = {
        str(name).lower()
        for name in (DynamicSchemaPruningService.get_physical_db_tables(connection) or set())
        if name
    }
    # Sin introspección no se inventa un esquema: fail-closed.
    if not physical_tables:
        return BIContext(
            connection=connection,
            role_name=role_name,
            is_admin=is_admin,
            tables=[],
            physical_tables=set(),
            blocked_columns={c.lower() for c in HIDDEN_COLUMNS},
            masked_columns=set(),
            allowed_tables=set(),
            table_columns={},
        )

    table_columns_meta: Dict[str, List[Dict[str, Any]]] = info.get("table_columns") or {}
    tables: List[BITableMeta] = []
    for table_name, columns in table_columns_meta.items():
        if str(table_name).lower() not in physical_tables:
            continue
        visible: List[BIColumnMeta] = []
        for column in columns or []:
            name = str(column.get("name") or "")
            if not name or name.lower() in HIDDEN_COLUMNS:
                continue
            data_type = str(column.get("type") or "")
            visible.append(
                BIColumnMeta(
                    name=name,
                    type=data_type,
                    kind=column_kind(name, data_type),
                    masked=bool(column.get("is_masked")),
                )
            )
        if visible:
            tables.append(BITableMeta(name=str(table_name), columns=visible))

    tables.sort(key=lambda table: table.name.lower())

    blocked_columns = {
        str(name).lower() for name in (info.get("blocked_columns") or set())
    }
    # Las columnas ocultas también se bloquean en el AST, incluso para admin.
    blocked_columns |= set(HIDDEN_COLUMNS)
    masked_columns = {
        str(name).lower() for name in (info.get("masked_columns") or set())
    }

    return BIContext(
        connection=connection,
        role_name=role_name,
        is_admin=is_admin,
        tables=tables,
        physical_tables=physical_tables,
        blocked_columns=blocked_columns,
        masked_columns=masked_columns,
        allowed_tables={table.name.lower() for table in tables},
        table_columns={
            table.name.lower(): [column.name for column in table.columns]
            for table in tables
        },
    )


def validate_table(context: BIContext, table_name: str) -> BITableMeta:
    """Devuelve la tabla autorizada o corta con 403/404.

    Si la tabla existe físicamente pero el perfil no la tiene concedida, es 403
    (es un acceso denegado, no una tabla inexistente). Si no existe, es 404.
    """
    table = context.table(table_name)
    if table:
        return table
    lowered = (table_name or "").lower()
    if lowered in context.physical_tables:
        raise BIAccessDenied(
            f"No tienes permiso para consultar la tabla '{table_name}'."
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"La tabla '{table_name}' no existe en esta base de datos.",
    )


def validate_column(
    table: BITableMeta,
    column_name: str,
    *,
    role: str,
    allow_masked: bool,
) -> BIColumnMeta:
    """Valida una columna contra la tabla autorizada.

    `allow_masked=False` es para el informe automático de un no-admin: una
    columna MASKED no se ofrece como campo. El admin siempre puede usarla.
    """
    column = table.column(column_name)
    if not column:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La columna '{column_name}' no existe en la tabla '{table.name}'.",
        )
    if column.masked and not allow_masked:
        raise BIAccessDenied(
            f"No tienes permiso para usar la columna confidencial '{column.name}' "
            f"como {role}."
        )
    return column


def report_columns(table: BITableMeta, is_admin: bool) -> List[BIColumnMeta]:
    """Columnas que el informe automático puede ofrecer a este usuario."""
    return [column for column in table.columns if is_admin or not column.masked]
