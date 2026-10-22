import io
from typing import List, Optional, Any, Dict, Set
from fastapi import APIRouter, Depends, status, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    get_db, get_current_user, get_current_admin, require_assigned_role,
    _persist_audit_log, display_role_name,
)
from app.modules.auth.models import User
from app.modules.admin_catalog.schemas import (
    SemanticCatalogCreate, SemanticCatalogUpdate, SemanticCatalogOut,
    DataDictionaryResponse, AutoEnrichRequest, AutoEnrichResponse,
    EnrichEstimateResponse,
    CorporateConnectionCreate, CorporateConnectionUpdate, CorporateConnectionOut,
    ConnectionTestRequest, ConnectionTestResult, MetadataDBTestRequest,
    ReportExportRequest, NullsAuditResponse, ApplyNullPolicyRequest,
    GovernanceCoverageResponse, GovernanceCoverageTable, GovernanceCoverageSummary
)
from app.modules.admin_catalog.models import (
    CorporateConnection, RoleTablePermission, RoleColumnPermission
)
from app.modules.auth.models import Role
from app.modules.catalog.services.catalog_service import CatalogDomainService
from app.modules.catalog.services.connector_service import ConnectorDomainService
from app.modules.catalog.services.enrich_estimator import EnrichEstimator
from app.modules.catalog.services.null_manager import NullManagerService
from app.modules.catalog.services.schema_inspector import SchemaIntrospectionError
from app.modules.reports.generator import ReportGeneratorService
from app.modules.system.health_service import HealthService
# Misma validacion anti-SSRF que /system/health y /llm/test-*: el base_url que
# llega del cliente se filtra por el allowlist de loopback antes de tocar la red.
from app.modules.chat_engine.llm_diagnostic_router import _resolve_llm_base_url
from fastapi import HTTPException

router = APIRouter()

# =========================================================================
# SEMANTIC CATALOG ENDPOINTS (/catalog)
# =========================================================================

@router.get("/catalog", response_model=List[SemanticCatalogOut])
def list_catalog(
    connection_id: Optional[int] = Query(None, description="Filtrar por ID de conexión"),
    table_name: Optional[str] = Query(None, description="Filtrar por nombre de tabla"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Lists semantic catalog rules and data dictionary definitions."""
    return CatalogDomainService.list_catalog(db, connection_id=connection_id, table_name=table_name)

@router.post("/catalog", response_model=SemanticCatalogOut, status_code=status.HTTP_201_CREATED)
def create_catalog_item(
    item_in: SemanticCatalogCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Creates or updates a semantic catalog entry (Admin only)."""
    return CatalogDomainService.create_catalog_item(db, item_in)

@router.put("/catalog/{item_id}", response_model=SemanticCatalogOut)
def update_catalog_item(
    item_id: int,
    item_in: SemanticCatalogUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Updates an existing semantic catalog entry (Admin only)."""
    return CatalogDomainService.update_catalog_item(db, item_id, item_in)

@router.delete("/catalog/{item_id}", status_code=status.HTTP_200_OK)
def delete_catalog_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Deletes a semantic catalog entry (Admin only)."""
    return CatalogDomainService.delete_catalog_item(db, item_id)

@router.get("/catalog/data-dictionary", response_model=DataDictionaryResponse)
def get_data_dictionary(
    connection_id: Optional[int] = Query(None, description="ID de conexión a inspeccionar"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Introspects target database schema dynamically."""
    # Los valores de muestra son datos reales de la BD del cliente. Antes de pasar
    # el rol, este endpoint devolvia valores de columnas BLOCKED (token de tarjeta,
    # salario) y MASKED (RUT/DNI) a cualquier usuario autenticado, esquivando el
    # validador AST porque no es SQL de usuario sino introspeccion.
    from app.core.constants import ADMIN_ROLES
    from app.modules.chat_engine.governance_guard import GovernanceGuard

    target_conn_id = connection_id
    if target_conn_id is None:
        active = db.query(CorporateConnection).filter(
            CorporateConnection.is_active == True
        ).order_by(CorporateConnection.id.desc()).first()
        target_conn_id = active.id if active else 1

    # Mismo corte que `/chat/query` y `/predict`, y por el MISMO helper. Antes
    # este endpoint resolvia el nombre con `... if current_user.role else
    # ROLE_USUARIO`: una cuenta con `role_id = NULL, is_admin = False` caia en
    # "Usuario Consultor", que es un rol real y valido del catalogo, asi que el
    # guard le resolvia la matriz DEL CONSULTOR y le servia el diccionario con
    # las columnas sensibles que ese perfil tiene permitidas (medido: 7 BLOCKED y
    # 2 MASKED, sobre `allowed_tables = ['dim_categorias', 'dim_productos']`).
    # El corte va DESPUES de resolver `target_conn_id` y no antes: la resolucion
    # es una query de metadata, no lee el esquema del cliente.
    audit_target = db.query(CorporateConnection.name).filter(
        CorporateConnection.id == target_conn_id
    ).scalar() or "desconocida"
    role_name = require_assigned_role(
        current_user, db, "Data dictionary: GET /catalog/data-dictionary", audit_target,
    )
    is_admin = current_user.is_admin or role_name in ADMIN_ROLES

    blocked_columns = GovernanceGuard.get_blocked_columns_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id,
        connection_id=target_conn_id,
    )
    masked_columns = GovernanceGuard.get_masked_columns_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id,
        connection_id=target_conn_id,
    )
    allowed_tables = GovernanceGuard.get_allowed_tables_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id,
        connection_id=target_conn_id,
    )

    return CatalogDomainService.get_data_dictionary(
        db,
        connection_id=target_conn_id,
        blocked_columns=blocked_columns,
        masked_columns=masked_columns,
        allowed_tables=allowed_tables,
    )

@router.post("/catalog/auto-enrich", response_model=AutoEnrichResponse)
async def auto_enrich_catalog(
    req: Optional[AutoEnrichRequest] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Intelligently inspects schema and auto-generates semantic descriptions."""
    return await CatalogDomainService.auto_enrich_catalog(db, req)

@router.get("/catalog/connections/{connection_id}/enrich-estimate", response_model=EnrichEstimateResponse)
async def estimate_enrich_duration(
    connection_id: int,
    base_url: Optional[str] = Query(None),
    model_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Estimates (measured, not guessed) how long AI enrichment will take.

    El probe de velocidad es una inferencia corta real: cuesta unos segundos en
    CPU la primera vez y queda cacheado por (base_url, modelo).
    """
    conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    if base_url:
        # Host fuera del loopback -> 400 y ni una request sale.
        base_url = _resolve_llm_base_url(base_url)
    try:
        return await EnrichEstimator.estimate_for_connection(
            conn, base_url=base_url, model_name=model_name
        )
    except SchemaIntrospectionError as ex:
        # Mismo criterio que /catalog/data-dictionary: la peticion es valida, la
        # base de la conexion no se pudo leer (502, no un "0 tablas" falso).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(ex)
        )

@router.get("/catalog/connections/{connection_id}/nulls-audit", response_model=NullsAuditResponse)
def audit_connection_nulls(
    connection_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Audits tables and columns for NULL values in the target connection (Admin only)."""
    conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    try:
        return NullManagerService.audit_connection_nulls(conn)
    except SchemaIntrospectionError as ex:
        # Mismo criterio que /catalog/data-dictionary: la peticion es valida, la
        # base de la conexion no se pudo leer. Sin este catch daba 500; antes de
        # que el inspector dejara de tragarse el fallo, respondia 200 con
        # has_nulls=false ("base limpia") sobre una base que no se abrio.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(ex)
        )

@router.post("/catalog/connections/{connection_id}/nulls-policy")
def apply_connection_null_policy(
    connection_id: int,
    req: ApplyNullPolicyRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Applies the selected null remediation policy (Admin only).

    Las politicas destructivas exigen `confirm=true` server-side: el clic en la
    UI no viaja al backend. Toda aplicacion queda en `audit_logs` porque la
    mutacion es fisica e irreversible.
    """
    conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    try:
        result = NullManagerService.apply_null_policy(conn, req.policy, db, confirm=req.confirm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _persist_audit_log(
        db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Política de nulos '{req.policy}' sobre la conexión '{conn.name}': "
            f"{result.get('message', '')}"
        ),
        sql_generated=None,
        validation_status=(
            "NULL_POLICY_APLICADA"
            if result.get("status") == "success"
            else "NULL_POLICY_SIN_CAMBIOS"
        ),
        target_database=conn.database_name or conn.name,
        rows_returned=result.get("rows_affected") or 0
    )
    return result

# =========================================================================
# CORPORATE CONNECTORS ENDPOINTS (/connectors)
# =========================================================================

@router.get("/connectors", response_model=List[CorporateConnectionOut])
def list_connectors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Lists all registered corporate database connections."""
    return ConnectorDomainService.list_connectors(db)

@router.post("/connectors", response_model=CorporateConnectionOut, status_code=status.HTTP_201_CREATED)
def create_connector(
    conn_in: CorporateConnectionCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Registers a new corporate database connection (Admin only)."""
    return ConnectorDomainService.create_connector(db, conn_in)

@router.post("/connectors/upload", response_model=CorporateConnectionOut, status_code=status.HTTP_201_CREATED)
async def upload_database_file(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Uploads a SQLite, Excel or CSV file (Admin only)."""
    return await ConnectorDomainService.upload_database_file(db, file, name)

@router.put("/connectors/{conn_id}", response_model=CorporateConnectionOut)
def update_connector(
    conn_id: int,
    conn_in: CorporateConnectionUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Updates an existing corporate database connection (Admin only)."""
    return ConnectorDomainService.update_connector(db, conn_id, conn_in)

@router.post("/connectors/{conn_id}/toggle-active", response_model=CorporateConnectionOut)
def toggle_connector_active(
    conn_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Toggles active status of a corporate database connection (Admin only)."""
    return ConnectorDomainService.toggle_connector_active(db, conn_id)

@router.delete("/connectors/{conn_id}", status_code=status.HTTP_200_OK)
def delete_connector(
    conn_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Deletes a corporate database connection (Admin only)."""
    return ConnectorDomainService.delete_connector(db, conn_id)

# =========================================================================
# PERMISOS DE TABLA POR ROL (default-deny)
# =========================================================================
# Ruta `/permissions` y no `/catalog/permissions`: esta se parsea contra
# `/catalog/{item_id}` (declarado mas arriba) y FastSQL devuelve 422 por un
# item_id no numerico. Los permisos son la matriz RBAC de toda la plataforma, no
# una entrada del catalogo semantico.

@router.get("/permissions")
def list_role_table_permissions(
    connection_id: Optional[int] = Query(None, description="Filtrar por ID de conexión"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Devuelve la matriz de permisos de tabla (Admin only).

    Un dataset recien subido aparece con `detected_tables` y sin ninguna fila acá:
    eso es default-deny, no un fallo. El admin concede con el PUT de abajo.
    """
    return ConnectorDomainService.list_role_table_permissions(db, connection_id=connection_id)

@router.put("/permissions")
def set_role_table_permissions(
    connection_id: int = Query(..., description="ID de la conexión"),
    role_id: int = Query(..., description="ID del rol"),
    table_names: List[str] = Query(..., description="Tablas a conceder o revocar"),
    is_allowed: bool = Query(True, description="True concede acceso, False lo revoca"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Concede o revoca acceso de un rol a un conjunto de tablas (Admin only).

    Es la unica via de granting: al subir un dataset no se concede nada. Un rol
    sin fila para una tabla NO tiene acceso a ella, y eso lo distingue de "la tabla
    existe pero no hay datos".
    """
    perms = ConnectorDomainService.set_role_table_permissions(
        db, connection_id=connection_id, role_id=role_id,
        table_names=table_names, is_allowed=is_allowed,
    )
    return {
        "connection_id": connection_id,
        "role_id": role_id,
        "is_allowed": is_allowed,
        "permissions": [
            {"id": p.id, "table_name": p.table_name, "schema_name": p.schema_name,
             "is_allowed": bool(p.is_allowed)}
            for p in perms
        ],
    }

@router.get("/permissions/coverage", response_model=GovernanceCoverageResponse)
def get_governance_coverage(
    connection_id: int = Query(..., description="ID de la conexión a auditar"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Cobertura de gobernanza: qué de esta conexión NO ve ningún rol (Admin only).

    Es DERIVADO, cero estado nuevo: se recalcula en cada GET desde la misma fuente
    que usa el chat (`GovernanceGuard` / `DynamicSchemaPruningService`), para que
    este número no pueda decir lo contrario de lo que el guardarraí­l realmente
    permite. No reimplementa la resolución de permisos: la pregunta.

    `visible_to_roles` sale de `get_allowed_tables_for_role` por rol, o sea de las
    tablas que el prompt del chat le mostraría a ese usuario. Un rol al que solo
    se le denegó el acceso NO cuenta como visible; y si el guard no pudo resolver
    (fail-closed) tampoco. Ambigüedad = huérfana, que es la postura del proyecto.

    Los roles de administrador NO se cuentan como lectores: el admin es quien
    audita, y verlo como "visible para alguien" dejaría `orphaned_tables` siempre
    en cero, que es justo el dato que esta vista existe para dar.
    """
    from app.core.constants import ADMIN_ROLES
    from app.modules.chat_engine.governance_guard import GovernanceGuard
    from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService

    conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")

    # El universo de la conexión: lo que el admin ve de ella (introspección física
    # más catálogo semántico). Es el mismo criterio que usa el prompt, así que no
    # hay una lista de tablas "de verdad" paralela por mantener.
    universe = DynamicSchemaPruningService.get_authorized_schema_prompt(
        db=db, connection_id=connection_id, is_admin=True,
    ).get("allowed_tables", set()) or set()

    # Quién tocó cada tabla. `is_allowed` NO se mira: un DENY es una decisión del
    # admin sobre esa tabla y por eso la cuenta como asignada, aunque no la haga
    # legible. Lo que decide si es huérfana es `visible_to_roles`, abajo.
    assigned_roles: Dict[str, Set[str]] = {}
    rows = db.query(RoleTablePermission, Role.name).join(
        Role, Role.id == RoleTablePermission.role_id
    ).filter(RoleTablePermission.connection_id == connection_id).all()
    for perm, role_name in rows:
        assigned_roles.setdefault(perm.table_name, set()).add(role_name)

    # Una pasada por rol No-Admin. Los tres helpers comparten el `_schema_cache`
    # de `DynamicSchemaPruningService` con la misma key, así que en la práctica se
    # resuelve el esquema una vez por rol, no tres.
    visible_to: Dict[str, Set[str]] = {}
    blocked_by_role: Dict[str, Set[str]] = {}
    masked_by_role: Dict[str, Set[str]] = {}
    non_admin_roles = [r for r in db.query(Role).all() if r.name not in ADMIN_ROLES]
    for role in non_admin_roles:
        allowed = GovernanceGuard.get_allowed_tables_for_role(
            role.name, False, db=db, role_id=role.id, connection_id=connection_id,
        ) or set()
        for table in allowed:
            visible_to.setdefault(table, set()).add(role.name)
        blocked_by_role[role.name] = GovernanceGuard.get_blocked_columns_for_role(
            role.name, False, db=db, role_id=role.id, connection_id=connection_id,
        ) or set()
        masked_by_role[role.name] = GovernanceGuard.get_masked_columns_for_role(
            role.name, False, db=db, role_id=role.id, connection_id=connection_id,
        ) or set()

    # `GovernanceGuard` devuelve los nombres de columna SIN calificar por tabla (los
    # usa para|prender del prompt entero). Para reportarlos por tabla hace falta
    # saber a cuál pertenece cada uno: eso lo dice `RoleColumnPermission`, y acá
    # se usa SOLO para atribuir, no para decidir. El sí/no de bloqueada o
    # enmascarada sigue siendo el del guard, nunca una regla propia.
    columns_of: Dict[str, Set[str]] = {}
    for cp in db.query(RoleColumnPermission).filter(
        RoleColumnPermission.connection_id == connection_id
    ).all():
        columns_of.setdefault(cp.table_name, set()).add(cp.column_name)

    tables = []
    for table in sorted(universe):
        readers = visible_to.get(table, set())
        # "Denegada a todos" = bloqueada para cada rol que la puede leer. Sin
        # lectores la columna no está en juego: no se informa nada de ella.
        readers_list = sorted(readers)
        own_columns = columns_of.get(table, set())
        blocked = sorted(own_columns & set.intersection(*(blocked_by_role[r] for r in readers_list))) if readers_list else []
        masked = sorted(own_columns & set.intersection(*(masked_by_role[r] for r in readers_list))) if readers_list else []
        tables.append(GovernanceCoverageTable(
            table=table,
            assigned_roles=sorted(assigned_roles.get(table, set())),
            visible_to_roles=readers_list,
            coverage="assigned" if readers else "orphaned",
            blocked_columns=blocked,
            masked_columns=masked,
        ))

    orphaned = sum(1 for t in tables if t.coverage == "orphaned")
    return GovernanceCoverageResponse(
        connection_id=connection_id,
        connection_name=conn.name,
        tables=tables,
        summary=GovernanceCoverageSummary(
            total_tables=len(tables),
            assigned_tables=len(tables) - orphaned,
            orphaned_tables=orphaned,
        ),
    )

@router.post("/connectors/test", response_model=ConnectionTestResult)
def test_connection_connectivity(
    test_in: ConnectionTestRequest,
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Tests real network TCP socket connectivity (Admin only).

    Antes bastaba estar autenticado: cualquier perfil podía hacer que el backend
    abriera un socket TCP a un host/puerto arbitrario (SSRF hacia la red local).
    """
    return ConnectorDomainService.test_connection_connectivity(test_in)

@router.post("/connectors/test-metadata-db", response_model=ConnectionTestResult)
def test_metadata_db_connectivity(
    test_in: MetadataDBTestRequest,
    current_admin: User = Depends(get_current_admin)
) -> Any:
    """Tests real connectivity to target PostgreSQL metadata database (Admin only)."""
    result = HealthService.check_db_connectivity(
        host=test_in.server,
        port=test_in.port,
        db_type="POSTGRESQL",
        database_name=test_in.db_name
    )
    return ConnectionTestResult(
        success=result["success"],
        message=result["message"],
        latency_ms=result["latency_ms"]
    )

# =========================================================================
# REPORT EXPORT ENDPOINTS (/reports/export/pdf & /reports/export/excel)
# =========================================================================

@router.post("/reports/export/pdf")
def export_report_pdf(
    req: ReportExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    pdf_bytes, filename = ReportGeneratorService.export_pdf(db, current_user, req)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.post("/reports/export/excel")
def export_report_excel(
    req: ReportExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    excel_bytes, filename = ReportGeneratorService.export_excel(db, current_user, req)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
