"""Servicio de visuales guardados (paneles BI)."""

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import AuditWriteError, _persist_audit_log
from app.core.config import settings
from app.core.security import mask_query_rows, mask_value
from app.modules.auth.models import User
from app.modules.bi import sql_builder
from app.modules.bi.governance import (
    BIAccessDenied,
    BIContext,
    BITableMeta,
    load_context,
    validate_table,
)
from app.modules.bi.models import BIPanel, BIVisual
from app.modules.bi.schemas import (
    BIFilter,
    BIPanelFilter,
    BIPanelFiltersUpdate,
    BIPanelOut,
    BIPanelRename,
    BIQueryResult,
    BIVisualCreate,
    BIVisualOut,
    BIVisualReorder,
    BIVisualSpec,
    BIVisualUpdate,
)
from app.modules.chat_engine.ast_validator import ASTValidationError, ASTValidator
from app.modules.chat_engine.sql_executor import SQLExecutor

MAX_VISUALS_PER_USER = 100


def _visual_out(visual: BIVisual) -> BIVisualOut:
    try:
        spec = BIVisualSpec(**json.loads(visual.spec_json))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El visual guardado tiene una definición inválida: {exc}",
        )
    return BIVisualOut(
        id=visual.id,
        title=visual.title,
        panel_name=visual.panel_name,
        position=visual.position or 0,
        spec=spec,
        created_at=visual.created_at.isoformat() if visual.created_at else "",
    )


def _audit_visual(
    db: Session,
    user: User,
    context: BIContext,
    title: str,
    sql: Optional[str],
    validation_status: str,
    elapsed_ms: int,
    rows_returned: int = 0,
    error_message: Optional[str] = None,
    *,
    required: bool,
) -> Optional[int]:
    try:
        return _persist_audit_log(
            db=db,
            user_id=user.id,
            username=user.username,
            user_role=context.role_name,
            question_prompt=f"Panel BI: {title or 'Visual'}",
            sql_generated=sql,
            validation_status=validation_status,
            target_database=context.connection.name or "",
            execution_time_ms=elapsed_ms,
            rows_returned=rows_returned,
            error_message=error_message,
            required=required,
        )
    except AuditWriteError:
        raise
    except Exception:
        if required:
            raise
        return None


def run_visual(
    *,
    db: Session,
    user: User,
    spec: BIVisualSpec,
    title: str = "",
    context: Optional[BIContext] = None,
    panel_filters: Optional[List[BIFilter]] = None,
) -> BIQueryResult:
    """Ejecuta la consulta de un visual y la audita.

    La consulta se recalcula siempre: si los permisos o los datos cambiaron, el
    visual muestra lo que el usuario puede ver hoy, no una foto vieja.

    `panel_filters` son los filtros compartidos de la pestaña que matchean la
    tabla/base de ESTE visual; la vista previa del constructor no los pasa.
    """
    context = context or load_context(
        db, user, spec.connection_id, f"Panel BI: {title or spec.table}"
    )
    started = time.perf_counter()
    table: Optional[BITableMeta] = None
    secured_sql: Optional[str] = None

    try:
        table = validate_table(context, spec.table)
        raw_sql, mask_label = sql_builder.build_visual_sql(
            table=table,
            spec=spec,
            blocked_columns=context.blocked_columns,
            is_admin=context.is_admin,
            panel_filters=panel_filters,
        )
        _valid, secured_sql, _meta = ASTValidator.validate_and_secure_sql(
            raw_sql,
            dialect="postgres",
            allowed_tables=context.allowed_tables,
            blocked_columns=context.blocked_columns,
            table_columns=context.table_columns,
            max_limit=settings.DEFAULT_ROW_LIMIT,
            is_admin=context.is_admin,
        )
        rows = SQLExecutor.execute_raw_sql(
            context.connection, secured_sql, dialect="postgres"
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        clean_rows: List[Dict[str, Any]] = []
        for row in rows:
            label = row.get("label")
            if mask_label:
                # Una etiqueta MASKED no es un identificador: no se conservan
                # los últimos dígitos. Se tapa completa para no revelar un
                # valor corto (por ejemplo, 1000) al agrupar.
                label = mask_value(label, keep_last_digits=0)
            value = row.get("value")
            if isinstance(value, float):
                value = round(value, 2)
            if "label" in row:
                entry = {"label": label, "value": value}
                if "series" in row:
                    # Apiladas: la serie se validó al construir el SQL; un
                    # no-admin nunca llega aquí con una serie confidencial
                    # (build_visual_sql la deniega antes de ejecutar).
                    entry["series"] = row["series"]
                clean_rows.append(entry)
            else:
                clean_rows.append({"value": value})

        # Defensa en profundidad: aunque el SELECT ya excluye las columnas
        # sensibles, todo camino que devuelve filas pasa por el sink de masking.
        # La medida MASKED solo puede ser count/count_distinct (validado en
        # `build_visual_sql`); su resultado es un conteo, no el valor sensible,
        # así que se excluye del sink para no convertir "4" en "***". Las
        # dimensiones MASKED sí se siguen enmascarando (y además se tapan de
        # forma completa arriba).
        masked_for_rows = set(context.masked_columns)
        measure_name = (spec.measure or "").lower()
        if measure_name and measure_name != "count":
            masked_for_rows.discard(measure_name)
        mask_query_rows(
            clean_rows,
            masked_for_rows,
            secured_sql,
            dialect="postgres",
        )

        audit_id = _audit_visual(
            db,
            user,
            context,
            title or spec.table,
            secured_sql,
            "APROBADO",
            elapsed_ms,
            rows_returned=len(clean_rows),
            required=True,
        )
        return BIQueryResult(
            rows=clean_rows,
            sql=secured_sql,
            execution_time_ms=elapsed_ms,
            masked_label=mask_label,
            audit_log_id=audit_id,
        )
    except AuditWriteError:
        # Sin auditoría no se devuelven datos del cliente.
        raise
    except BIAccessDenied as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit_visual(
            db,
            user,
            context,
            title or spec.table,
            secured_sql,
            "RECHAZADO_RBAC",
            elapsed_ms,
            error_message=str(exc.detail),
            required=False,
        )
        raise
    except ASTValidationError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit_visual(
            db,
            user,
            context,
            title or spec.table,
            secured_sql,
            "RECHAZADO_RBAC",
            elapsed_ms,
            error_message=str(exc),
            required=False,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"La consulta del visual fue rechazada: {exc}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit_visual(
            db,
            user,
            context,
            title or spec.table,
            secured_sql,
            "ERROR_EJECUCION",
            elapsed_ms,
            error_message=str(exc),
            required=False,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo ejecutar el visual: {exc}",
        )


def preview_visual(db: Session, user: User, spec: BIVisualSpec) -> BIQueryResult:
    return run_visual(db=db, user=user, spec=spec)


def list_visuals(
    db: Session, user: User, panel_name: Optional[str] = None
) -> List[BIVisualOut]:
    query = db.query(BIVisual).filter(BIVisual.user_id == user.id)
    if panel_name:
        query = query.filter(BIVisual.panel_name == panel_name)
    visuals = (
        query.order_by(
            BIVisual.panel_name.asc(),
            BIVisual.position.asc(),
            BIVisual.id.asc(),
        )
        .all()
    )
    return [_visual_out(visual) for visual in visuals]


def create_visual(
    db: Session, user: User, body: BIVisualCreate
) -> BIVisualOut:
    count = db.query(BIVisual).filter(BIVisual.user_id == user.id).count()
    if count >= MAX_VISUALS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Máximo {MAX_VISUALS_PER_USER} visuales por usuario.",
        )

    # No se guarda un visual que el usuario no puede ver hoy.
    run_visual(db=db, user=user, spec=body.spec, title=body.title)

    visual = BIVisual(
        user_id=user.id,
        connection_id=body.spec.connection_id,
        panel_name=body.panel_name.strip() or "Mi panel",
        title=body.title.strip(),
        spec_json=body.spec.model_dump_json(),
        position=count,
    )
    try:
        db.add(visual)
        db.commit()
        db.refresh(visual)
    except Exception:
        db.rollback()
        raise
    return _visual_out(visual)


def update_visual(
    db: Session, user: User, visual_id: int, body: BIVisualUpdate
) -> BIVisualOut:
    """Edita un visual guardado (título, panel y/o definición).

    Igual que al crearlo: la consulta se ejecuta y se audita ANTES de aceptar
    el cambio, así nunca queda guardada una definición que el usuario no puede
    ver con los permisos de hoy. La posición no se toca: reordenar es otra
    acción y tiene su propio endpoint.
    """
    visual = (
        db.query(BIVisual)
        .filter(BIVisual.id == visual_id, BIVisual.user_id == user.id)
        .first()
    )
    if not visual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visual no encontrado.",
        )

    run_visual(db=db, user=user, spec=body.spec, title=body.title)

    try:
        visual.title = body.title.strip()
        visual.panel_name = body.panel_name.strip() or "Mi panel"
        visual.connection_id = body.spec.connection_id
        visual.spec_json = body.spec.model_dump_json()
        db.commit()
        db.refresh(visual)
    except Exception:
        db.rollback()
        raise
    return _visual_out(visual)


def reorder_visuals(
    db: Session, user: User, body: BIVisualReorder
) -> List[BIVisualOut]:
    """Fija el orden de los visuales de un panel.

    El request trae la lista COMPLETA de ids del panel, en el orden final. Con
    eso el endpoint es idempotente y un fallo de red deja el orden anterior en
    vez de un orden intermedio. Es metadata del propio usuario: no consulta la
    base corporativa y no genera fila de auditoría.
    """
    panel_visuals = (
        db.query(BIVisual)
        .filter(
            BIVisual.user_id == user.id,
            BIVisual.panel_name == body.panel_name,
        )
        .all()
    )
    by_id = {visual.id: visual for visual in panel_visuals}

    missing = [visual_id for visual_id in body.ids if visual_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El panel tiene visuales que no son tuyos o ya no existen.",
        )
    if len(set(body.ids)) != len(body.ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La lista de orden repite un visual.",
        )
    if set(body.ids) != set(by_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La lista debe incluir TODOS los visuales del panel.",
        )

    try:
        for position, visual_id in enumerate(body.ids):
            by_id[visual_id].position = position
        db.commit()
    except Exception:
        db.rollback()
        raise
    return list_visuals(db, user, body.panel_name)


def get_visual_data(db: Session, user: User, visual_id: int) -> BIQueryResult:
    visual = (
        db.query(BIVisual)
        .filter(BIVisual.id == visual_id, BIVisual.user_id == user.id)
        .first()
    )
    if not visual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visual no encontrado.",
        )
    try:
        spec = BIVisualSpec(**json.loads(visual.spec_json))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El visual guardado tiene una definición inválida: {exc}",
        )
    # Filtros compartidos de la pestaña: se mergean en memoria, por request,
    # con los permisos de hoy. El SQL guardado (spec_json) no cambia.
    panel_row = (
        db.query(BIPanel)
        .filter(BIPanel.user_id == user.id, BIPanel.name == visual.panel_name)
        .first()
    )
    panel_filters = _matching_filters(panel_row, visual.connection_id, spec.table)
    return run_visual(
        db=db,
        user=user,
        spec=spec,
        title=visual.title,
        panel_filters=panel_filters,
    )


def delete_visual(db: Session, user: User, visual_id: int) -> None:
    visual = (
        db.query(BIVisual)
        .filter(BIVisual.id == visual_id, BIVisual.user_id == user.id)
        .first()
    )
    if not visual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visual no encontrado.",
        )
    db.delete(visual)
    db.commit()


# --- Paneles (pestañas) ---------------------------------------------------------


def _panel_filters(row: Optional[BIPanel]) -> List[BIPanelFilter]:
    """Parsea los filtros guardados de un panel. Fila corrupta/ausente → vacío.

    `filters_json` siempre lo escribe este módulo tras validar con Pydantic.
    Si aún así se corrompe, el visual se ejecuta con SUS filtros propios: no
    otorga acceso extra, porque los permisos del visual se validan igual.
    """
    if not row:
        return []
    try:
        raw = json.loads(row.filters_json or "[]")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    parsed: List[BIPanelFilter] = []
    for item in raw:
        try:
            parsed.append(BIPanelFilter(**item))
        except Exception:
            continue
    return parsed


def _matching_filters(
    row: Optional[BIPanel], connection_id: int, table: str
) -> List[BIFilter]:
    """Filtros del panel que aplican a esta base y tabla, como filtros simples."""
    return [
        BIFilter(column=item.column, op=item.op, value=item.value)
        for item in _panel_filters(row)
        if item.connection_id == connection_id and item.table == table
    ]


def list_panels(db: Session, user: User) -> List[BIPanelOut]:
    """Paneles del usuario: filas explícitas + nombres implícitos de visuales.

    Los paneles creados antes de `bi_panels` existen solo como string en los
    visuales y siguen apareciendo, con filtros vacíos.
    """
    rows = db.query(BIPanel).filter(BIPanel.user_id == user.id).all()
    by_name = {row.name: _panel_filters(row) for row in rows}
    visual_names = {
        name
        for (name,) in db.query(BIVisual.panel_name)
        .filter(BIVisual.user_id == user.id)
        .distinct()
        .all()
        if name
    }
    return [
        BIPanelOut(name=name, filters=by_name.get(name, []))
        for name in sorted(set(by_name) | visual_names)
    ]


def rename_panel(
    db: Session, user: User, body: BIPanelRename
) -> List[BIPanelOut]:
    """Renombra una pestaña y mueve el string de sus visuales en una transacción."""
    old = body.panel_name.strip()
    new = body.new_name.strip()
    if not new:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre nuevo no puede estar vacío.",
        )

    def exists(name: str) -> bool:
        has_visual = (
            db.query(BIVisual.id)
            .filter(BIVisual.user_id == user.id, BIVisual.panel_name == name)
            .first()
            is not None
        )
        has_row = (
            db.query(BIPanel.id)
            .filter(BIPanel.user_id == user.id, BIPanel.name == name)
            .first()
            is not None
        )
        return has_visual or has_row

    if not exists(old):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tienes un panel con ese nombre.",
        )
    if old != new and exists(new):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya tienes un panel llamado así.",
        )

    row = (
        db.query(BIPanel)
        .filter(BIPanel.user_id == user.id, BIPanel.name == old)
        .first()
    )
    try:
        db.query(BIVisual).filter(
            BIVisual.user_id == user.id, BIVisual.panel_name == old
        ).update({BIVisual.panel_name: new}, synchronize_session=False)
        if row:
            row.name = new
        db.commit()
    except Exception:
        db.rollback()
        raise
    return list_panels(db, user)


def update_panel_filters(
    db: Session, user: User, body: BIPanelFiltersUpdate
) -> List[BIPanelOut]:
    """Guarda los filtros compartidos de una pestaña (crea la fila si no existe).

    La forma la valida Pydantic aquí; la gobernanza de cada columna se aplica
    al EJECUTAR los visuales, con los permisos de hoy: una columna enmascarada
    de un no-admin rompe el visual con su mensaje de denegación, nunca se
    filtra en silencio.
    """
    name = body.panel_name.strip()
    row = (
        db.query(BIPanel)
        .filter(BIPanel.user_id == user.id, BIPanel.name == name)
        .first()
    )
    if not row:
        row = BIPanel(user_id=user.id, name=name, filters_json="[]")
        db.add(row)
    row.filters_json = json.dumps([item.model_dump() for item in body.filters])
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return list_panels(db, user)


def delete_panel(db: Session, user: User, panel_name: str) -> None:
    """Borra una pestaña con todos sus visuales y sus filtros."""
    row = (
        db.query(BIPanel)
        .filter(BIPanel.user_id == user.id, BIPanel.name == panel_name)
        .first()
    )
    visual_count = (
        db.query(BIVisual)
        .filter(BIVisual.user_id == user.id, BIVisual.panel_name == panel_name)
        .count()
    )
    if not row and visual_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tienes un panel con ese nombre.",
        )
    try:
        db.query(BIVisual).filter(
            BIVisual.user_id == user.id, BIVisual.panel_name == panel_name
        ).delete(synchronize_session=False)
        if row:
            db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
