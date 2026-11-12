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
import re
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
from app.modules.catalog.services.schema_inspector import SchemaInspector
from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast.lexicon import DIMENSION_QUESTION_PATTERN
from app.modules.ml_forecast.policy import POLICY
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
from app.modules.ml_forecast.timeframe import (
    parse_growth_scenario,
    parse_question_timeframe,
)
from app.modules.telemetry_audit.models import AuditLog

router = APIRouter()


async def _in_thread(fn, *args, **kwargs):
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
            return fn(session, *args, **kwargs)
        finally:
            session.close()

    return await asyncio.to_thread(_call)


def _resolve_projection_connection(db: Session, requested_id: Optional[int]):
    """Conexión de proyección: explícita, o la ACTIVA; nunca la 1 por default."""
    if requested_id is not None:
        return SchemaInspector.resolve_connection(db, requested_id)
    conn = SchemaInspector.active_connection(db)
    if conn is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hay ninguna conexión de datos activa para proyectar.",
        )
    return conn


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

    El horizonte sale de la pregunta ("un año" = 12 pasos) para que la barra de
    progreso no asuma un valor que nadie pidio. El parseo es el mismo del run.
    """
    timeframe = parse_question_timeframe(payload.question)
    # El escenario explicito manda sobre el texto: el cliente ya decidio la tasa,
    # y el parseo de la pregunta queda como respaldo de los clientes viejos.
    scenario_growth = (
        payload.scenario_growth_pct
        if payload.scenario_growth_pct is not None
        else parse_growth_scenario(payload.question)
    )
    if payload.horizon is not None:
        horizon = payload.horizon
    elif scenario_growth is not None and timeframe.requested_steps is not None:
        horizon = timeframe.requested_steps
    else:
        horizon = timeframe.steps
    return ml_service.estimate_seconds(payload.question, horizon)


_UNIT_LABEL = {
    "day": "días",
    "week": "semanas",
    "month": "meses",
    "quarter": "trimestres",
}

# La pregunta pide un grupo (un producto, una categoria) y no el total. Es una
# lista corta a proposito: cada palabra de mas convierte en "ranking" preguntas
# que solo querian un numero.
_DIMENSION_QUESTION_RE = re.compile(
    DIMENSION_QUESTION_PATTERN,
    re.IGNORECASE,
)


def _answer_focus(question: str, dimension_column: Optional[str]) -> str:
    """"dimension" solo si la pregunta la pide Y hay desglose que la responda.

    Una pregunta por el "producto que mas vende" sin `dimension_column` se
    contesta con el total: marcar `dimension` ahi promete un desglose que la
    respuesta no publico.
    """
    if dimension_column and _DIMENSION_QUESTION_RE.search(question or ""):
        return "dimension"
    return "total"


def _truncation_note(
    requested: Optional[int], published: int, granularity: str
) -> Optional[str]:
    """Frase que declara el recorte cuando la pregunta pide mas que el tope.

    El backend no puede publicar 10 anos con 46 meses de historia y un tope de
    36 pasos; lo que NO puede hacer es presentar 36 meses como si fueran la
    respuesta a 10 anos. Esta nota va delante de la narrativa.
    """
    if requested is None or requested <= published:
        return None
    unit = _UNIT_LABEL.get(granularity, "pasos")
    requested_label = f"{requested} {unit}"
    if granularity == "month" and requested % 12 == 0:
        years = requested // 12
        requested_label += f" ({years} {'año' if years == 1 else 'años'})"
    return (
        f"Pediste {requested_label}; el máximo publicable es {published} {unit}, "
        f"así que esta proyección cubre {published} {unit}."
    )


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

    El horizonte sale de la pregunta y del modo. En el modo estadistico el tope
    es 36 pasos y el analisis puede ajustarlo; en un escenario de crecimiento
    ("10% anual en 10 años") se publican los 120 meses del supuesto, sin backtest
    ni banda. El origen viaja en `horizon_source`.
    """
    conn_record = _resolve_projection_connection(db, payload.connection_id)
    conn_id = conn_record.id
    target_db_name, user_role_name, budget = _projection_gates(
        current_user, db, payload.question, conn_id
    )

    started_at = time.perf_counter()
    context: Dict[str, Any] = {}
    analysis: Optional[Dict[str, Any]] = None
    # Se inicializa aca y no dentro del `try`: el camino de `available=False`
    # tambien publica `answer_focus`, y una variable sin asignar seria un 500 en
    # el unico camino que tiene que explicar un motivo.
    dimension_column: Optional[str] = None

    # La pregunta manda: "un año" = 12 pasos mensuales, "6 semanas" = 6
    # semanales. El payload explicito sigue ganando (compatibilidad), pero la UI
    # nueva manda null y deja que el texto decida.
    timeframe = parse_question_timeframe(payload.question)
    # El escenario explicito del cliente manda sobre el texto. Con escenario, el
    # horizonte pedido puede superar `MAX_HORIZON` y se respeta tal cual (hasta
    # `max_scenario_horizon`): los pasos del supuesto no tienen banda que recortar.
    scenario_growth = (
        payload.scenario_growth_pct
        if payload.scenario_growth_pct is not None
        else parse_growth_scenario(payload.question)
    )
    requested_horizon = (
        payload.horizon
        if payload.horizon is not None
        else (
            timeframe.requested_steps
            if scenario_growth is not None and timeframe.requested_steps is not None
            else timeframe.steps
        )
    )
    granularity = payload.granularity or timeframe.granularity or "month"
    horizon = requested_horizon
    if payload.horizon is not None:
        horizon_origin: Optional[str] = "pedido"
    elif scenario_growth is not None:
        horizon_origin = "pregunta"
    elif timeframe.steps is None:
        horizon_origin = None
    elif (
        timeframe.requested_steps is not None
        and timeframe.requested_steps > timeframe.steps
    ):
        horizon_origin = "pregunta_recortada"
    else:
        horizon_origin = "pregunta"

    try:
        context = await _in_thread(
            ml_service.load_fact_context, conn_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget,
            question=payload.question, table_name=payload.table_name,
        )
        analysis = await ml_service.analyze_question(
            payload.question, context["table"], context["candidate_cols"],
            context["heuristic"], requested_horizon,
            payload.metric_column, payload.date_column,
            resolution=context.get("resolution"),
            requested_agg=payload.metric_agg,
        )
        horizon = analysis["horizon"]
        dimension_column = (
            None
            if scenario_growth is not None
            else (payload.dimension_column or analysis.get("dimension_column"))
        )
        data = await _in_thread(
            ml_service.run_projection, conn_id, context["table"], context["cols"],
            analysis["metric_column"], analysis["date_column"], horizon,
            user_role_name, current_user.is_admin, current_user.role_id, budget,
            context["candidate_cols"], payload.include_partial_period,
            dimension_column,
            resolution=context.get("resolution"),
            metric_agg=analysis.get("metric_agg"),
            granularity=granularity,
            scenario_growth_pct=scenario_growth,
            connection=context.get("connection"),
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
                horizon_origin
                or (analysis or {}).get("horizon_source")
                or "default"
            ),
            table=context.get("table"),
            table_source=context.get("table_source"),
            metric_column=(analysis or {}).get("metric_column"),
            metric_agg=(
                (analysis or {}).get("metric_agg")
                or payload.metric_agg
                or "sum"
            ),
            granularity=granularity,
            date_column=(analysis or {}).get("date_column"),
            connection_id=conn_id,
            analysis_source=(analysis or {}).get("source"),
            answer_focus=_answer_focus(payload.question, dimension_column),
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
            required=True,
        )
        return response
    except Exception as exc:
        # Un fallo inesperado (conector caido, bug de calculo) tambien tiene que
        # dejar rastro. Antes producia 500 sin fila en audit_logs.
        reason = f"ERROR_EJECUCION: {type(exc).__name__}"
        logger.exception("Proyeccion ML fallo de forma inesperada: %s", exc)
        _persist_audit_log(
            db=db,
            user_id=current_user.id,
            username=current_user.username,
            user_role=user_role_name,
            question_prompt=payload.question,
            sql_generated=None,
            validation_status="ERROR_EJECUCION",
            target_database=target_db_name,
            execution_time_ms=int((time.perf_counter() - started_at) * 1000),
            rows_returned=0,
            error_message=reason,
            result_snapshot=None,
            required=True,
        )
        raise

    response = ProjectionResponse(
        **{k: v for k, v in data.items() if k in ProjectionResponse.model_fields},
        question=payload.question,
        # El origen del analisis (LLM o heuristica) se publica: quien lee tiene que
        # poder saber si la metrica la eligio un modelo de 7B o el fallback por
        # nombre/tipo, sin abrir los logs. El origen del horizonte se publica por
        # la misma razon: Auto puede haberlo cambiado.
        analysis_source=analysis.get("source"),
        horizon_source=horizon_origin or analysis.get("horizon_source"),
        answer_focus=_answer_focus(payload.question, dimension_column),
        connection_id=conn_id,
        table_source=context.get("table_source"),
    )

    if data["available"]:
        # La narrativa se arma SOLO cuando hay numeros que explicar. Sobre un
        # `available=False` seria una lectura de una proyeccion que no existe;
        # el motivo ya lo dice `reason`.
        #
        # La plantilla es determinista: un fallo suyo no puede tirar una
        # proyeccion valida. Los numeros ya estan calculados y auditados; se
        # publica sin prosa antes que devolver un 500.
        try:
            narration, narration_source = await ml_service.narrate(data)
            notes = [] if data.get("scenario") else [
                note for note in (
                    _truncation_note(timeframe.requested_steps, horizon, granularity),
                    ml_service._derived_metric_note(
                        payload.question, analysis.get("metric_column")
                    ),
                ) if note
            ]
            response.narration = (" ".join(notes) + " " if notes else "") + narration
            response.narration_source = narration_source
        except Exception as exc:
            logger.warning("Narracion de proyeccion no disponible: %s", exc)
            response.narration = None
            response.narration_source = None

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
        required=True,
    )
    return response


@router.get("/projections/context", response_model=ProjectionContextResponse)
async def projection_context(
    connection_id: Optional[int] = Query(None),
    table_name: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Columnas permitidas para el selector manual de tabla, metrica, eje y dimension.

    Pasa por los mismos gates que la corrida: el selector no es un inventario
    del esquema, es la lista de lo que este rol puede leer en esta conexion.
    `table_name` fija la tabla; sin el, se elige por pregunta o la mayor.
    """
    question = "Contexto de columnas para proyeccion"
    conn_record = _resolve_projection_connection(db, connection_id)
    connection_id = conn_record.id
    _, user_role_name, budget = _projection_gates(
        current_user, db, question, connection_id
    )
    try:
        data = await _in_thread(
            ml_service.projection_context, connection_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget,
            table_name=table_name,
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
    limit: int = Query(
        POLICY.history_default_limit, ge=1, le=POLICY.history_max_limit
    ),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Ultimas proyecciones del usuario autenticado, solo las propias."""
    items = await _in_thread(ml_service.list_projection_history, current_user.id, limit)
    return ProjectionHistoryResponse(items=items)


def _owned_projection_audit(
    db: Session, audit_log_id: int, user_id: int
) -> AuditLog:
    """Fila de auditoria de una proyeccion del usuario, o 404."""
    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.id == audit_log_id,
            AuditLog.user_id == user_id,
            AuditLog.validation_status == "PROYECCION_ML",
        )
        .first()
    )
    if audit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe una proyeccion auditada tuya con ese id.",
        )
    return audit


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
    audit = _owned_projection_audit(db, audit_log_id, current_user.id)

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


@router.get("/projections/{audit_log_id}", response_model=ProjectionResponse)
async def get_ml_projection(
    audit_log_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Devuelve el resultado publicado de una proyeccion propia ya auditada.

    El historial solo lista un resumen; para revisar los pasos, la banda y la
    lectura de una corrida anterior se sirve el snapshot tal como se publico.
    Solo el dueño del registro puede leerlo; el snapshot no se recalcula ni se
    completa con datos actuales (para eso esta `/verification`).
    """
    audit = _owned_projection_audit(db, audit_log_id, current_user.id)

    snapshot = ml_service.parse_projection_snapshot(audit.result_snapshot)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El registro de auditoria no tiene un snapshot de proyeccion legible.",
        )
    # Los snapshots viejos no guardaban su propio id; la fila de auditoria si lo
    # tiene y es el que necesitan el export y la verificacion.
    if snapshot.audit_log_id is None:
        snapshot.audit_log_id = audit.id
    return snapshot
