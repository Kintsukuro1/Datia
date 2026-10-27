import csv
import io
import datetime
import math
from typing import Optional, Any
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, load_only
from app.api.deps import get_db, get_current_admin
from app.core.security import sanitize_spreadsheet_value
from app.core.time import utcnow_naive
from app.modules.auth.models import User
from app.modules.telemetry_audit.models import AuditLog
from app.modules.telemetry_audit.schemas import AuditLogsPage, AuditLogOut

router = APIRouter()

# Tope de filas del export CSV. `audit_logs` crece para siempre y no hay politica
# de retencion, asi que un export sin limite construye el archivo entero en
# memoria antes de devolverlo. El listado paginado de arriba si usa
# `offset`/`limit` en SQL y no tiene este problema.
EXPORT_MAX_ROWS = 50_000

def _apply_audit_filters(
    query,
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    username: Optional[str] = None,
    target_database: Optional[str] = None,
    validation_status: Optional[str] = None
):
    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.filter(AuditLog.timestamp <= end_date)
    if username:
        query = query.filter(AuditLog.username.ilike(f"%{username.strip()}%"))
    if target_database:
        query = query.filter(AuditLog.target_database.ilike(f"%{target_database.strip()}%"))
    if validation_status:
        query = query.filter(AuditLog.validation_status.ilike(f"%{validation_status.strip()}%"))
    return query

@router.get("", response_model=AuditLogsPage)
def get_audit_logs(
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    username: Optional[str] = None,
    target_database: Optional[str] = None,
    validation_status: Optional[str] = None,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """
    Returns paginated audit logs filtered by date range, user, target database or status.
    Protected by Administrator role.
    """
    base_query = db.query(AuditLog)
    filtered_query = _apply_audit_filters(
        base_query,
        start_date=start_date,
        end_date=end_date,
        username=username,
        target_database=target_database,
        validation_status=validation_status
    )

    total = filtered_query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    items = filtered_query.order_by(AuditLog.timestamp.desc()) \
        .offset((page - 1) * page_size) \
        .limit(page_size) \
        .all()

    # El detalle se agrega solo para eventos de permisos (ver `AuditLogOut`):
    # incluirlo en todas las filas multiplicaria el payload de la pagina por
    # snapshots de consultas que pueden pesar 100 KB cada uno. `model_validate`
    # copia el atributo del ORM, asi que hay que LIMPIARLO en el resto.
    items_out = []
    for item in items:
        out = AuditLogOut.model_validate(item)
        if not (item.validation_status or "").startswith("PERMISOS_"):
            out.result_snapshot = None
        items_out.append(out)

    return AuditLogsPage(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=items_out
    )

@router.get("/export")
def export_audit_logs_csv(
    start_date: Optional[datetime.datetime] = None,
    end_date: Optional[datetime.datetime] = None,
    username: Optional[str] = None,
    target_database: Optional[str] = None,
    validation_status: Optional[str] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> StreamingResponse:
    """
    Generates and streams a downloadable CSV report of filtered audit logs.
    Protected by Administrator role.
    """
    base_query = db.query(AuditLog)
    filtered_query = _apply_audit_filters(
        base_query,
        start_date=start_date,
        end_date=end_date,
        username=username,
        target_database=target_database,
        validation_status=validation_status
    )

    total = filtered_query.count()
    truncated = total > EXPORT_MAX_ROWS

    # `load_only` evita traer `result_snapshot` (hasta ~108 KB por fila) de las
    # 50.000 filas del tope. El CSV no lo usa para las consultas; el detalle se
    # pide aparte solo para los eventos de permisos, que son los unicos que lo
    # necesitan. Sin esto, un export grande carga el snapshot completo en RAM
    # aunque no lo escriba.
    logs = filtered_query.options(load_only(
        AuditLog.id,
        AuditLog.timestamp,
        AuditLog.username,
        AuditLog.user_role,
        AuditLog.question_prompt,
        AuditLog.sql_generated,
        AuditLog.validation_status,
        AuditLog.target_database,
        AuditLog.execution_time_ms,
        AuditLog.rows_returned,
        AuditLog.error_message,
    )).order_by(AuditLog.timestamp.desc()).limit(EXPORT_MAX_ROWS).all()

    permission_ids = [
        log.id for log in logs
        if (log.validation_status or "").startswith("PERMISOS_")
    ]
    snapshots = {}
    if permission_ids:
        snapshots = {
            row[0]: row[1]
            for row in db.query(AuditLog.id, AuditLog.result_snapshot)
            .filter(AuditLog.id.in_(permission_ids))
            .all()
        }

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    writer.writerow([
        "ID",
        "Fecha UTC",
        "Usuario",
        "Rol",
        "Pregunta / Prompt",
        "SQL Generado",
        "Estado Validación",
        "Base de Datos",
        "Tiempo Ejecución (ms)",
        "Filas Devueltas",
        "Mensaje de Error",
        "Detalle del Evento"
    ])

    for log in logs:
        writer.writerow([
            log.id,
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "",
            sanitize_spreadsheet_value(log.username or ""),
            sanitize_spreadsheet_value(log.user_role or ""),
            sanitize_spreadsheet_value(log.question_prompt or ""),
            sanitize_spreadsheet_value(log.sql_generated or ""),
            sanitize_spreadsheet_value(log.validation_status or ""),
            sanitize_spreadsheet_value(log.target_database or ""),
            log.execution_time_ms or 0,
            log.rows_returned or 0,
            sanitize_spreadsheet_value(log.error_message or ""),
            sanitize_spreadsheet_value(snapshots.get(log.id) or "")
        ])

    # El tope es una proteccion de memoria, no una promesa de export completo.
    # Si se corta, el CSV lo dice: un documento de compliance que se presenta
    # como total cuando no lo es vuelve inutil el rastro.
    if truncated:
        writer.writerow([
            "AVISO", "", "", "", "", "", "", "", "", "", "",
            (
                f"Exportación truncada: se exportaron {EXPORT_MAX_ROWS} de {total} "
                "eventos. Acote el rango de fechas o los filtros."
            ),
        ])

    csv_data = output.getvalue()
    output.close()

    filename = f"datia_audit_logs_{utcnow_naive().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        io.BytesIO(csv_data.encode('utf-8-sig')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )
