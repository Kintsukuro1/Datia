"""Endpoints del informe BI y de los paneles guardados."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import AuditWriteError, get_current_user, get_db
from app.modules.auth.models import User
from app.modules.bi.governance import (
    list_authorized_connections,
    load_context,
)
from app.modules.bi.report_service import build_report
from app.modules.bi.schemas import (
    BIColumn,
    BIConnectionOut,
    BIQueryResult,
    BIReportResponse,
    BISchemaResponse,
    BITable,
    BIVisualCreate,
    BIVisualOut,
    BIVisualReorder,
    BIVisualSpec,
    BIVisualUpdate,
)
from app.modules.bi.visual_service import (
    create_visual,
    delete_visual,
    get_visual_data,
    list_visuals,
    preview_visual,
    reorder_visuals,
    update_visual,
)

router = APIRouter()


def _db_type(connection) -> str:
    value = getattr(connection.db_type, "value", connection.db_type)
    return str(value)


def _audit_guard(call):
    """Convierte un fallo de auditoría en 503, sin devolver datos."""
    try:
        return call()
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )


@router.get("/connections", response_model=List[BIConnectionOut])
def get_bi_connections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[BIConnectionOut]:
    """Bases incorporadas que este perfil puede usar en el módulo BI."""
    connections = list_authorized_connections(db, current_user)
    return [
        BIConnectionOut(
            id=connection.id,
            name=connection.name,
            db_type=_db_type(connection),
            is_active=bool(connection.is_active),
        )
        for connection in connections
    ]


@router.get("/schema", response_model=BISchemaResponse)
def get_bi_schema(
    connection_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BISchemaResponse:
    """Tablas y columnas que ESTE usuario puede usar en la base elegida."""
    context = load_context(
        db, current_user, connection_id, "Panel BI: ver esquema"
    )
    return BISchemaResponse(
        connection_id=context.connection.id,
        connection_name=context.connection.name,
        is_admin=context.is_admin,
        tables=[
            BITable(
                name=table.name,
                columns=[
                    BIColumn(
                        name=column.name,
                        type=column.type,
                        kind=column.kind,
                        masked=column.masked,
                    )
                    for column in table.columns
                ],
            )
            for table in context.tables
        ],
    )


@router.get("/report", response_model=BIReportResponse)
def get_bi_report(
    connection_id: int = Query(..., ge=1),
    table: str = Query(..., min_length=1, max_length=128),
    date_column: Optional[str] = Query(None, max_length=128),
    group_column: Optional[str] = Query(None, max_length=128),
    category_column: Optional[str] = Query(None, max_length=128),
    measure: Optional[str] = Query(None, max_length=128),
    year: Optional[int] = Query(None, ge=1900, le=2200),
    month: Optional[str] = Query(None, max_length=10),
    group_value: Optional[str] = Query(None, max_length=500),
    category_value: Optional[str] = Query(None, max_length=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BIReportResponse:
    """Todos los números del informe; cada consulta pasa por el guardarraíl."""
    return _audit_guard(
        lambda: build_report(
            db=db,
            user=current_user,
            connection_id=connection_id,
            table_name=table,
            date_column=date_column,
            group_column=group_column,
            category_column=category_column,
            measure=measure,
            year=year,
            month=month,
            group_value=group_value,
            category_value=category_value,
        )
    )


@router.post("/query", response_model=BIQueryResult)
def query_bi_visual(
    spec: BIVisualSpec,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BIQueryResult:
    """Vista previa de un visual sin guardarlo."""
    return _audit_guard(
        lambda: preview_visual(db=db, user=current_user, spec=spec)
    )


@router.get("/visuals", response_model=List[BIVisualOut])
def get_bi_visuals(
    panel_name: Optional[str] = Query(None, max_length=120),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[BIVisualOut]:
    """Visuales guardados del usuario actual."""
    return list_visuals(db, current_user, panel_name)


@router.post(
    "/visuals",
    response_model=BIVisualOut,
    status_code=status.HTTP_201_CREATED,
)
def create_bi_visual(
    body: BIVisualCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BIVisualOut:
    """Guarda un visual después de ejecutarlo con los permisos de hoy."""
    return _audit_guard(
        lambda: create_visual(db=db, user=current_user, body=body)
    )


@router.put("/visuals/positions", response_model=List[BIVisualOut])
def reorder_bi_visuals(
    body: BIVisualReorder,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[BIVisualOut]:
    """Fija el orden de los visuales de un panel y devuelve el panel ordenado.

    Se declara ANTES que `PUT /visuals/{visual_id}` a propósito: FastAPI
    resuelve las rutas en orden de registro y `{visual_id}` capturaría la
    palabra "positions" si se registrara primero.
    """
    reorder_visuals(db, current_user, body)
    return list_visuals(db, current_user, body.panel_name)


@router.put("/visuals/{visual_id}", response_model=BIVisualOut)
def update_bi_visual(
    visual_id: int,
    body: BIVisualUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BIVisualOut:
    """Edita un visual guardado tras re-ejecutarlo con los permisos de hoy."""
    return _audit_guard(
        lambda: update_visual(
            db=db, user=current_user, visual_id=visual_id, body=body
        )
    )


@router.post("/visuals/{visual_id}/data", response_model=BIQueryResult)
def get_bi_visual_data(
    visual_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BIQueryResult:
    """Recalcula un visual guardado con los datos y permisos actuales."""
    return _audit_guard(
        lambda: get_visual_data(db=db, user=current_user, visual_id=visual_id)
    )


@router.delete("/visuals/{visual_id}")
def delete_bi_visual(
    visual_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Borra un visual del usuario actual."""
    delete_visual(db, current_user, visual_id)
    return {"success": True}
