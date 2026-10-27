"""HTTP de las proyecciones ML: estimacion instantanea y corrida completa.

Dos endpoints, y la diferencia entre ellos es que uno NO toca nada:
`/projections/estimate` responde con la velocidad que ya se midio (cache del
asistente de enriquecimiento) y no lee la base ni llama al LLM. `/projections/run`
corre la cadena completa con los MISMOS gates de datos que `/chat/predict`: rol
asignado, gobernanza de dominio, fact table autorizada por el rol, el mismo
`ASTValidator` (que LEVANTA `ASTValidationError` para columnas bloqueadas) y el
mismo enmascarado. Una proyeccion no es un camino privilegiado alrededor de la
gobernanza.

Que NO es un 500
----------------
`ForecastUnavailable` ("no hay columna de fecha", "la conexion no existe") y
`ASTValidationError` ("tu rol no puede leer esa columna") se convierten en la
MISMA respuesta con `available=False` y su motivo, auditadas igual. Un 500 seria
peor que no proyectar: el usuario veria "error interno" donde la respuesta real
es "esta pregunta necesita una columna que tu perfil no puede leer".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    _persist_audit_log,
    get_current_user,
    get_db,
    require_assigned_role as _require_assigned_role,
)
from app.core.constants import ADMIN_ROLES
from app.core.database import SessionLocal
from app.core.logging import logger
from app.modules.auth.models import User
from app.modules.chat_engine.ast_validator import ASTValidationError
from app.modules.chat_engine.forecast_service import ForecastUnavailable
from app.modules.chat_engine.governance_guard import GovernanceGuard
# `_resolve_target_database` es privada A PROPOSITO: es la etiqueta de auditoria
# y tiene que ser UNA sola definicion. Una copia aca podria divergir y el mismo
# hecho quedaria en dos bases distintas del CSV de compliance.
from app.modules.chat_engine.router import _resolve_target_database
from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast.schemas import (
    DEFAULT_HORIZON,
    ProjectionContextResponse,
    ProjectionEstimateRequest,
    ProjectionEstimateResponse,
    ProjectionHistoryResponse,
    ProjectionRequest,
    ProjectionResponse,
    ProjectionVerificationResponse,
)
from app.modules.telemetry_audit.models import AuditLog

router = APIRouter()


async def _in_thread(fn, *args):
    """Corre un bloque sincronico (SQLAlchemy + backtest) en un hilo con SU sesion.

    El SQL y el backtest son sincronos: dentro de un `async def` en serie
    bloquean el event loop y cualquier otra request del servidor espera a que
    termine el backtest. Cada bloque abre su propia `SessionLocal` porque una
    `Session` de SQLAlchemy no es thread-safe y compartir la de la request con el
    hilo seria una carrera silenciosa. La sesion de la request se sigue usando,
    en serie, para la auditoria del final (mismo patron que el `_run` de
    `/chat/predict`).
    """
    def _call():
        session = SessionLocal()
        try:
            return fn(session, *args)
        finally:
            session.close()

    return await asyncio.to_thread(_call)


def _projection_gates(
    current_user: User,
    db: Session,
    question: str,
    connection_id: int,
):
    """Gates de datos comunes a corrida, contexto y verificacion.

    Una sola definicion de "rol asignado" y "dominio permitido": si cada endpoint
    tuviera su copia, el mismo rechazo podria quedar auditado distinto o no
    quedar. Devuelve la etiqueta de auditoria, el rol y el cache de la request.
    """
    target_db_name = _resolve_target_database(db, connection_id)
    user_role_name = _require_assigned_role(current_user, db, question, target_db_name)
    budget: Dict[str, Any] = {}
    if not current_user.is_admin and user_role_name not in ADMIN_ROLES:
        allowed_tables = GovernanceGuard.get_allowed_tables_for_role(
            user_role=user_role_name, is_admin=current_user.is_admin, db=db,
            role_id=current_user.role_id, connection_id=connection_id,
        )
        budget["allowed_tables"] = allowed_tables
        domain_denial = GovernanceGuard.check_domain_governance(
            question, user_role_name, allowed_tables, is_admin=current_user.is_admin
        )
        if domain_denial:
            _persist_audit_log(
                db=db,
                user_id=current_user.id,
                username=current_user.username,
                user_role=user_role_name,
                question_prompt=question,
                sql_generated=None,
                validation_status="RECHAZADO_RBAC",
                target_database=target_db_name,
                error_message=domain_denial,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=domain_denial)
    return target_db_name, user_role_name, budget


@router.post("/projections/estimate", response_model=ProjectionEstimateResponse)
async def estimate_ml_projection(
    payload: ProjectionEstimateRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    """Cuanto va a tardar la corrida, segun la velocidad YA medida del LLM.

    Instantaneo a proposito: no lee la base del cliente ni llama al LLM. Lo que
    responde sale de la cache de tokens/segundo que dejo el asistente de
    enriquecimiento (TTL de una hora); si no hay ninguna medicion valida, dice
    `source="fallback"` con la espera conservadora en vez de inventar una
    medicion. Este endpoint no proyecta nada y por eso no pasa por los gates de
    datos: no hay dato que entregar. El unico requisito es estar autenticado.
    """
    return ml_service.estimate_seconds(payload.question, payload.horizon)


@router.post("/projections/run", response_model=ProjectionResponse)
async def run_ml_projection(
    payload: ProjectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Proyeccion a varios pasos sobre la fact table autorizada del rol.

    Orden: gates de datos -> contexto de la fact table -> analisis de la pregunta
    -> proyeccion -> narrativa -> auditoria. El LLM interviene DOS veces y
    ninguna de las dos decide un numero: elige entre columnas que existen
    (validado contra los metadatos) y redacta sobre numeros ya calculados por
    `ml_models`. La serie, la banda y el MAPE salen de filas reales.

    El horizonte explicito del pedido (3, 6 o 12) manda siempre. Con
    `horizon=null` (Auto) se acepta el que proponga el analisis si esta en
    {3, 6, 12}; si no, queda `DEFAULT_HORIZON`. El origen viaja en
    `horizon_source`.
    """
    conn_id = payload.connection_id or 1
    target_db_name, user_role_name, budget = _projection_gates(
        current_user, db, payload.question, conn_id
    )

    started_at = time.perf_counter()
    context: Dict[str, Any] = {}
    analysis: Optional[Dict[str, Any]] = None
    horizon = payload.horizon

    try:
        context = await _in_thread(
            ml_service.load_fact_context, conn_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget,
        )
        analysis = await ml_service.analyze_question(
            payload.question, context["table"], context["candidate_cols"],
            context["heuristic"], horizon,
            payload.metric_column, payload.date_column,
        )
        horizon = analysis["horizon"]
        data = await _in_thread(
            ml_service.run_projection, conn_id, context["table"], context["cols"],
            analysis["metric_column"], analysis["date_column"], horizon,
            user_role_name, current_user.is_admin, current_user.role_id, budget,
            context["candidate_cols"], payload.include_partial_period,
            payload.dimension_column,
        )
    except (ForecastUnavailable, ASTValidationError) as exc:
        # "No se puede calcular" no es un error del servidor: es una respuesta con
        # su motivo. La serie no existe todavia, pero la tabla y las columnas
        # elegidas SI, y viajan: sin ellas el lector no puede accionar ("falta la
        # columna de fecha en esta tabla") y volveria a pedir la misma corrida.
        reason = str(exc)
        logger.warning("Proyeccion ML no disponible: %s", reason)
        response = ProjectionResponse(
            available=False,
            reason=reason,
            question=payload.question,
            horizon=horizon if horizon is not None else DEFAULT_HORIZON,
            horizon_source=(
                (analysis or {}).get("horizon_source")
                or ("pedido" if payload.horizon is not None else "default")
            ),
            table=context.get("table"),
            metric_column=(analysis or {}).get("metric_column"),
            date_column=(analysis or {}).get("date_column"),
            connection_id=conn_id,
            analysis_source=(analysis or {}).get("source"),
            # Si el validador rechazo el SQL, el SQL intentado viaja: negar sin
            # decir QUE se intento leer deja la auditoria de compliance sin el
            # unico registro de lo que la gobernanza corto.
            sql=getattr(exc, "attempted_sql", None),
        )
        response.audit_log_id = _persist_audit_log(
            db=db,
            user_id=current_user.id,
            username=current_user.username,
            user_role=user_role_name,
            question_prompt=payload.question,
            sql_generated=response.sql,
            validation_status="PROYECCION_ML",
            target_database=target_db_name,
            execution_time_ms=int((time.perf_counter() - started_at) * 1000),
            rows_returned=0,
            error_message=reason,
            result_snapshot=response.model_dump_json(),
        )
        return response

    response = ProjectionResponse(
        **{k: v for k, v in data.items() if k in ProjectionResponse.model_fields},
        question=payload.question,
        # El origen del analisis (LLM o heuristica) se publica: quien lee tiene que
        # poder saber si la metrica la eligio un modelo de 7B o el fallback por
        # nombre/tipo, sin abrir los logs. El origen del horizonte se publica por
        # la misma razon: Auto puede haberlo cambiado.
        analysis_source=analysis.get("source"),
        horizon_source=analysis.get("horizon_source"),
        connection_id=conn_id,
    )

    if data["available"]:
        # La narrativa se pide SOLO cuando hay numeros que explicar. Sobre un
        # `available=False` el texto del LLM seria una opinion sobre una
        # proyeccion que no existe; el motivo ya lo dice `reason`.
        narration, narration_source = await ml_service.narrate(data)
        response.narration = narration
        response.narration_source = narration_source

    # La proyeccion tambien deja rastro, igual que el forecast del chat: un numero
    # publicado sin registro de quien lo pidio y con que SQL no tiene trazabilidad.
    # `execution_time_ms` se mide de verdad (no un 0 hardcodeado) porque el tiempo
    # real incluye los COUNT(*) de la fact table y el backtest walk-forward.
    response.audit_log_id = _persist_audit_log(
        db=db,
        user_id=current_user.id,
        username=current_user.username,
        user_role=user_role_name,
        question_prompt=payload.question,
        sql_generated=data.get("sql"),
        validation_status="PROYECCION_ML",
        target_database=target_db_name,
        execution_time_ms=int((time.perf_counter() - started_at) * 1000),
        rows_returned=len(data.get("series") or []),
        result_snapshot=response.model_dump_json(),
    )
    return response


@router.get("/projections/context", response_model=ProjectionContextResponse)
async def projection_context(
    connection_id: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Columnas permitidas para el selector manual de metrica, eje y dimension.

    Pasa por los mismos gates que la corrida: el selector no es un inventario
    del esquema, es la lista de lo que este rol puede leer en esta conexion.
    """
    question = "Contexto de columnas para proyeccion"
    _, user_role_name, budget = _projection_gates(
        current_user, db, question, connection_id
    )
    try:
        data = await _in_thread(
            ml_service.projection_context, connection_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget,
        )
    except (ForecastUnavailable, ASTValidationError) as exc:
        return ProjectionContextResponse(
            available=False,
            reason=str(exc),
            connection_id=connection_id,
        )
    return ProjectionContextResponse(connection_id=connection_id, **data)


@router.get("/projections/history", response_model=ProjectionHistoryResponse)
async def projection_history(
    limit: int = Query(10, ge=1, le=20),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Ultimas proyecciones del usuario autenticado, solo las propias."""
    items = await _in_thread(ml_service.list_projection_history, current_user.id, limit)
    return ProjectionHistoryResponse(items=items)


@router.get(
    "/projections/{audit_log_id}/verification",
    response_model=ProjectionVerificationResponse,
)
async def verify_ml_projection(
    audit_log_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Compara una proyeccion auditada contra el dato actual de periodos cerrados."""
    started_at = time.perf_counter()
    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.id == audit_log_id,
            AuditLog.user_id == current_user.id,
            AuditLog.validation_status == "PROYECCION_ML",
        )
        .first()
    )
    if audit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe una proyeccion auditada tuya con ese id.",
        )

    snapshot = ml_service.parse_projection_snapshot(audit.result_snapshot)
    question = f"Verificacion de proyeccion #{audit_log_id}"
    if snapshot is None or snapshot.connection_id is None:
        reason = (
            "El registro de auditoria no tiene un snapshot de proyeccion legible."
            if snapshot is None
            else "La proyeccion no guardo la conexion: no se puede releer el dato actual."
        )
        response = ProjectionVerificationResponse(
            available=False,
            reason=reason,
            audit_log_id=audit.id,
            question=snapshot.question if snapshot else audit.question_prompt,
            table=snapshot.table if snapshot else None,
            metric_column=snapshot.metric_column if snapshot else None,
            date_column=snapshot.date_column if snapshot else None,
            horizon=snapshot.horizon if snapshot else DEFAULT_HORIZON,
            method=snapshot.method if snapshot else None,
        )
        _persist_audit_log(
            db=db,
            user_id=current_user.id,
            username=current_user.username,
            user_role=(current_user.role.name if current_user.role else None),
            question_prompt=question,
            sql_generated=None,
            validation_status="VERIFICACION_PROYECCION_ML",
            target_database=audit.target_database or "desconocida",
            execution_time_ms=int((time.perf_counter() - started_at) * 1000),
            rows_returned=0,
            error_message=reason,
            result_snapshot=response.model_dump_json(),
        )
        return response

    conn_id = snapshot.connection_id
    target_db_name, user_role_name, budget = _projection_gates(
        current_user, db, question, conn_id
    )
    data = await _in_thread(
        ml_service.verify_projection, audit.id, current_user.id, user_role_name,
        current_user.is_admin, current_user.role_id, budget,
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La proyeccion auditada ya no existe.",
        )

    response = ProjectionVerificationResponse(**data)
    _persist_audit_log(
        db=db,
        user_id=current_user.id,
        username=current_user.username,
        user_role=user_role_name,
        question_prompt=question,
        sql_generated=data.get("sql"),
        validation_status="VERIFICACION_PROYECCION_ML",
        target_database=target_db_name,
        execution_time_ms=int((time.perf_counter() - started_at) * 1000),
        rows_returned=int(data.get("rows_returned") or 0),
        error_message=response.reason if not response.available else None,
        result_snapshot=response.model_dump_json(),
    )
    return response
