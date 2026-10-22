import asyncio
import logging
import json
import time
from typing import Any, Dict, Optional, List, Set
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session


from app.api.deps import (
    get_db, get_current_user, get_current_user_optional, get_current_admin,
    # El gate de "la cuenta tiene un rol" vive en `deps` porque lo necesitan
    # tambien `admin_catalog` y `system`. `_persist_audit_log` se re-exporta con
    # su nombre viejo porque `tests/test_transaction_hygiene.py` lo importa desde
    # aca y esa ruta es parte del contrato del modulo.
    _persist_audit_log, require_assigned_role as _require_assigned_role,
)
from app.core.database import SessionLocal
from app.core.time import utcnow_naive
from app.modules.catalog.services.schema_inspector import SchemaInspector
from app.modules.auth.models import User
from app.modules.telemetry_audit.models import AuditLog
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.chat_engine.models import ChatConversation, QueryLearningMemory, DashboardWidget
from app.modules.chat_engine.schemas import (
    QueryRequest, QueryResponse, SuggestionsResponse,
    ChatThreadCreate, ChatThreadSummary, ChatThreadDetail,
    ChatFeedbackRequest, ChatFeedbackResponse,
    DashboardWidgetCreate, DashboardWidgetOut,
    GoldenQueryRequest, GoldenQueryList, GoldenQueryOut,
    PredictionRequest, PredictionResponse,
    ForecastCard, RetentionReport, RetentionTier, RetentionClient
)
from app.modules.chat_engine import forecast_service
from app.modules.chat_engine.engine import QueryEngine
from app.modules.chat_engine.sql_executor import SQLExecutor
from app.modules.chat_engine.llm_service import LLMService
from app.modules.chat_engine.llm_diagnostic_router import (
    llm_diagnostic_router,
    _resolve_llm_base_url,
)

from app.core.constants import ADMIN_ROLES, ROLE_ADMINISTRADOR
from app.core.database import discard_failed_transaction
from app.core.security import mask_query_rows
from app.modules.chat_engine.ast_validator import ASTValidator, ASTValidationError
from app.modules.chat_engine.governance_guard import GovernanceGuard

router = APIRouter()
logger = logging.getLogger(__name__)

# Mount LLM diagnostic and testing routes
router.include_router(llm_diagnostic_router)

def _resolve_target_database(db: Session, connection_id: int) -> str:
    """Finds friendly target database name for audit log."""
    try:
        conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
        if conn and conn.name:
            return conn.name
    except Exception:
        # Es una etiqueta para el audit log: si falla, se usa el nombre por defecto.
        # Sin rollback, la transaccion abortada hacia que TODO lo que viniera
        # despues en esta request (`QueryEngine.execute_query`) fallara con
        # `InFailedSqlTransaction`, blaming a una etiqueta de auditoria.
        discard_failed_transaction(db)
    return "demo_corporativa.db"

# `_persist_audit_log` y `_require_assigned_role` viven en `app.api.deps` y se
# importan arriba: son el piso comun de los endpoints que tocan datos corporativos,
# y el gate tiene que ser UNA sola definicion. Cuando cada router tenia su propia
# copia del fallback "sin rol -> Usuario Consultor", los tres llamadores
# divergieron sobre la misma cuenta (`/chat/*` la cortaba, `/catalog/
# data-dictionary` y `/system/anomalies` no).


def _validated_request_llm_base_url(query_in: QueryRequest) -> Optional[str]:
    """Config LLM de Ajustes que trae la request, pasada por la allowlist de LOOPBACK.

    Una sola definicion para `/query` y `/query/stream`: el 400 sale ANTES de
    tocar el motor (en el stream, antes de abrir el stream: adentro de un
    generador ya no hay status HTTP que devolver).

    Por que `_resolve_llm_base_url` y no `validate_llm_base_url`
    ------------------------------------------------------------
    `validate_llm_base_url` (la del sink) acepta ademas RFC1918 y
    `host.docker.internal` porque tiene que cubrir la URL CONFIGURADA POR EL
    DESPLIEGUE (docker-compose: `OLLAMA_BASE_URL=http://host.docker.internal:11434`,
    o un Ollama en otra maquina de la LAN). Eso es politica del operador, no del
    usuario: aca la URL la elige cualquiera con sesion, y aceptar una IP privada
    significa que el backend emite el POST con el prompt a un host que el usuario
    controla (SSRF/exfiltracion). El cliente solo puede elegir el LLM de su propia
    maquina, o sea loopback.

    El sink sigue validando con su propia politica (`validate_llm_base_url`):
    loopback pasa por ahi tambien, y la URL del servidor no cambia.
    """
    if not query_in.llm_base_url:
        return None
    # Levanta HTTPException(400) sin emitir ninguna request.
    return _resolve_llm_base_url(query_in.llm_base_url)


def _audit_response(
    db: Session,
    response: QueryResponse,
    *,
    user_id: int,
    username: str,
    user_role: str,
    question_prompt: str,
    target_database: str,
) -> None:
    """Persiste la auditoria de una respuesta y le estampa su id.

    Un solo camino para `/query` y `/query/stream`: un stream no puede dejar de
    registrar lo que el usuario leyo, es el mismo dato corporativo y la misma
    evidencia de compliance que la consulta normal.

    Sin trazabilidad NO se inventa un estado: se persiste `None`. Este antes era
    un `else "APROBADO"` que escribia una validacion que nadie hacia, y como
    `validation_status` era NOT NULL (ver telemetry_audit/models.py) era la
    unica opcion del modelo. Los exporters ya saben pintar "Sin registro de
    validacion" cuando el campo viene vacio.
    """
    sql_gen = response.traceability.sql_executed if response.traceability else None
    v_status = response.traceability.validation_status if response.traceability else None
    exec_time = response.traceability.execution_time_ms if response.traceability else 0
    rows_ret = response.traceability.rows_returned if response.traceability else len(response.data_rows)
    err_msg = response.summary_text if (
        (v_status or "").startswith("RECHAZADO") or response.response_type == "error"
    ) else None

    try:
        snapshot_json = response.model_dump_json()
    except Exception:
        snapshot_json = None

    audit_id = _persist_audit_log(
        db=db,
        user_id=user_id,
        username=username,
        user_role=user_role,
        question_prompt=question_prompt,
        sql_generated=sql_gen,
        validation_status=v_status,
        target_database=target_database,
        execution_time_ms=exec_time,
        rows_returned=rows_ret,
        error_message=err_msg,
        result_snapshot=snapshot_json
    )

    if audit_id:
        if response.traceability:
            response.traceability.audit_log_id = audit_id
        response.audit_log_id = audit_id


@router.post("/query", response_model=QueryResponse)
async def process_chat_query(
    query_in: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """
    Processes natural language or suggestion chip query against target database.
    Invokes Local LLM, applies RBAC permissions & AST Guardrail validation.
    Persists audit log of approval or rejection with result snapshot.
    """
    request_llm_base_url = _validated_request_llm_base_url(query_in)
    conn_id = query_in.connection_id or 1
    target_db_name = _resolve_target_database(db, conn_id)
    user_role_name = _require_assigned_role(
        current_user, db, query_in.question, target_db_name
    )

    try:
        # La config LLM viaja por ContextVar alrededor de TODO el motor: sus
        # 7 call-sites de `LLMService.generate_completion/stream_completion`
        # la leen como default sin threadear parametros por cada uno.
        with LLMService.request_config(request_llm_base_url, query_in.llm_model):
            response = await QueryEngine.execute_query(
                question=query_in.question,
                user_role=user_role_name,
                is_admin=current_user.is_admin,
                db=db,
                role_id=current_user.role_id,
                connection_id=conn_id,
                conversation_history=query_in.conversation_history
            )


        _audit_response(
            db=db,
            response=response,
            user_id=current_user.id,
            username=current_user.username,
            user_role=user_role_name,
            question_prompt=query_in.question,
            target_database=target_db_name
        )

        return response
    except Exception as e:
        _persist_audit_log(
            db=db,
            user_id=current_user.id,
            username=current_user.username,
            user_role=user_role_name,
            question_prompt=query_in.question,
            sql_generated=None,
            validation_status="ERROR_EJECUCION",
            target_database=target_db_name,
            execution_time_ms=0,
            rows_returned=0,
            error_message=str(e),
            result_snapshot=None
        )
        raise


@router.post("/query/stream")
async def process_chat_query_stream(
    query_in: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> StreamingResponse:
    """
    Variante stremeada de `POST /chat/query`. Misma autenticacion, misma
    gobernanza, misma auditoria, misma respuesta final.

    Por que un endpoint NUEVO y no un flag en el de hoy
    ---------------------------------------------------
    `POST /chat/query` queda intacto y es la red de seguridad. Si el stream
    falla, se corta a mitad, o el navegador no soporta la lectura por chunks, el
    cliente repite por el camino de siempre y obtiene la respuesta completa. Un
    endpoint unico que ahora stremea dejaria de existir ese camino.

    Que se emite
    ------------
    Tres eventos, y solo tres:
      - `meta`   : una vez al abrir. Dice que arranco. No dice "cuanto falta".
      - `delta`  : pedazos de la NARRATIVA, texto real del LLM. Nunca SQL,
                   nunca `rows`, nunca `rows_returned`.
      - `result` : la MISMA `QueryResponse` que devuelve `POST /chat/query`.

    El evento `result` es lo que el cliente usa como verdad. Los `delta` son solo
    lectura anticipada: si el stream se corta antes, el cliente tiene texto
    parcial pero NO tiene resultado, y por lo tantoTodavia no commiteo nada al
    hilo. Esa es la garantia de que no queda media respuesta pegada en el chat.

    Autenticacion
    ------------
    `get_current_user` lee el header `Authorization`, igual que el resto de la
    API. No hay token en la query string: `EventSource` no soporta POST ni
    headers, pero el cliente no usa `EventSource` sino `fetch` + `getReader()`,
    que si los soporta. Un JWT en la URL quedaria en los logs del servidor y en
    el historial del navegador; no hace falta.
    """
    conn_id = query_in.connection_id or 1
    # Validar ANTES de abrir el stream: adentro del generador las cabeceras ya
    # se enviaron. Un id explicito inexistente tiene que ser un 404 HTTP, no un
    # evento `error` que el cliente confunde con "el servidor se cayo".
    SchemaInspector.resolve_connection(db, conn_id)
    request_llm_base_url = _validated_request_llm_base_url(query_in)
    # Antes de abrir el stream: el corte de rol tiene que ser un 403 HTTP de
    # verdad. Adentro del generador las cabeceras ya se enviaron y un rechazo
    # solo puede viajar como evento `error`, que el cliente no distingue de
    # "el servidor se cayo".
    user_role_name = _require_assigned_role(
        current_user, db, query_in.question, _resolve_target_database(db, conn_id)
    )

    # Los datos del usuario y la sesion se leen ANTES de abrir el stream. Durante
    # la generacion el generador corre sobre su propia sesion: `current_user`
    # queda desasociado al cerrarse la de la request, y `user_role_name` /
    # `is_admin` son valores planos que sobreviven.
    is_admin = current_user.is_admin
    user_id = current_user.id
    username = current_user.username
    role_id = current_user.role_id

    async def event_stream():
        stream_db = SessionLocal()
        queue: asyncio.Queue = asyncio.Queue()

        async def push_delta(text: str) -> None:
            await queue.put(("delta", text))

        async def run_query() -> None:
            """Corre el motor y publica el resultado. Nunca propaga excepciones."""
            try:
                target_db_name = _resolve_target_database(stream_db, conn_id)
                # El `with` va DENTRO del task y no en el handler: el handler ya
                # retorno cuando este generador corre. El ContextVar setea el
                # contexto de ESTE task, que es donde corren los 7 call-sites.
                with LLMService.request_config(request_llm_base_url, query_in.llm_model):
                    response = await QueryEngine.execute_query(
                        question=query_in.question,
                        user_role=user_role_name,
                        is_admin=is_admin,
                        db=stream_db,
                        role_id=role_id,
                        connection_id=conn_id,
                        conversation_history=query_in.conversation_history,
                        narrative_sink=push_delta
                    )

                # La auditoria se persiste sobre la MISMA logica que usa
                # `POST /chat/query`. Un stream no puede dejar de registrar lo que
                # el usuario leyo: es el mismo dato corporativo y la misma
                # evidencia de compliance.
                _audit_response(
                    db=stream_db,
                    response=response,
                    user_id=user_id,
                    username=username,
                    user_role=user_role_name,
                    question_prompt=query_in.question,
                    target_database=target_db_name
                )

                await queue.put(("result", response))
            except Exception as exc:
                logger.warning(f"Error en stream de chat: {exc}")
                await queue.put(("error", str(exc)))
            finally:
                await queue.put(("done", None))

        task = asyncio.create_task(run_query())

        try:
            yield _sse("meta", {"streaming": True})
            while True:
                kind, payload = await queue.get()
                if kind == "done":
                    break
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                elif kind == "result":
                    yield _sse("result", json.loads(payload.model_dump_json()))
                elif kind == "error":
                    # El error viaja como evento, no como HTTP status: las
                    # cabeceras ya se enviaron al empezar el stream. El cliente
                    # lo trata como "no hay resultado" y cae a `POST /chat/query`.
                    yield _sse("error", {"message": payload})
        finally:
            # El cliente se fue (cancelo o timeout). El trabajo del servidor NO se
            # deshizo: el motor local puede seguir hasta terminar. Solo se deja
            # de escribir en una conexion que ya no esta.
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            stream_db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Sin buffering en proxies: `nginx` por defecto acumula la respuesta
            # y el usuario no veria los deltas hasta el final, que es justo lo
            # que este endpoint existe para evitar.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: Any) -> str:
    """Serializa un evento SSE.

    El `data` va en una sola linea porque el parser del cliente parte por lineas.
    `json.dumps` escapa los saltos de linea reales dentro del string, asi que una
    narrativa con parrafos no rompe el framing.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/predict", response_model=PredictionResponse)
async def run_prediction(
    payload: PredictionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """
    Predicciones: forecast del proximo periodo y score de retencion por entidad.

    Por que un endpoint y no el chat
    --------------------------------
    `/chat/query` deja que el LLM escriba el SQL. Para una prediccion eso agrega
    una fuente de error que no hace falta: el modelo tiene que acertar el nombre
    de la tabla y de las columnas, y con 7B cuantizado no es confiable. Aca el
    SQL lo arma codigo desde los metadatos reales de la BD y despues pasa por el
    MISMO `ASTValidator` y el MISMO `SQLExecutor` que el chat — misma
    gobernanza, misma lectura, mismo enmascarado. Lo unico que cambia es quien
    escribe la consulta.

    El LLM no interviene, y por eso la respuesta no puede alucinar: cada numero
    sale de `forecast_calculator` sobre filas reales. Un forecast publicado sin
    su banda de error seria un numero sin informacion; `ForecastCard.mape` y
    `band_pct` viajan siempre junto al punto.

    Que responda "no disponible" y no un numero
    ------------------------------------------
    Si la serie tiene menos periodos que los necesarios, la respuesta es
    `available: False` con el motivo y la serie a la vista. Fabricar un forecast
    con 3 puntos no es una aproximacion: es una cifra con mas decimales que
    evidencia.
    """
    conn_id = payload.connection_id or 1
    target_db_name = _resolve_target_database(db, conn_id)

    # --- Gate 1: la cuenta no tiene ningun rol asignado.
    # `/predict` genera el SQL por código, pero lee la misma base corporativa que
    # el chat, asi que corta por la MISMA razon y con el mismo helper que
    # `/chat/query`: una sola definicion del corte, no dos que puedan divergir.
    #
    # El corte es "no tiene rol", no "es el Usuario Consultor": el Consultor es un
    # perfil del catálogo con lectura mínima declarada y sin tablas de negocio, así
    # que si pregunta por ventas lo frena la matriz de permisos, con un mensaje que
    # explica el motivo. Acá no hay rol que explicar.
    user_role_name = _require_assigned_role(
        current_user, db, payload.question or "Prediccion: forecast y retencion", target_db_name
    )

    # --- Gate 2: gobernanza de dominio. Idéntico a `engine.py:164-167`.
    #
    # Por qué se pasa el conjunto de tablas y no una pregunta
    # -------------------------------------------------------
    # `check_domain_governance` es un guard de KEYWORDS sobre el texto de la
    # consulta, y `/predict` no recibe una pregunta en lenguaje natural que
    # consultar. Inventar una pseudo-pregunta ("¿cuánto vendremos?") sería un
    # bypass disfrazado: dependería de una frase que el cliente elige y que
    # puede no mandar. Lo que `/predict` SÍ decide por código es contra qué
    # tablas lee — las del rol — y su SQL es siempre una agregación de ingresos
    # (`income_only`) sobre la fact table. El dominio real de la consulta es,
    # por tanto, el dominio de esas tablas, y es lo que se le pasa al guard.
    #
    # El guard ya reconoce nombres de tabla en sus dos listas de keywords
    # (`fact_ventas`, `fact_ingresos_costos`, `kna1_clientes` del lado
    # financiero; `dim_servidores`, `fact_incidentes_ti` del lado TI), así que no
    # hace falta ninguna ruta nueva en el guard: se le da el sujeto que de
    # verdad se está leyendo.
    #
    # Sin esto, un rol TI con `fact_ventas` en su matriz RBAC se llevaba
    # agregados de ingresos por acá, mientras la MISMA pregunta escrita en el
    # chat salía denegada por `governance_guard.py:193`. El endpoint no es un
    # camino privilegiado alrededor de la gobernanza.

    # Cache de la request. Nace acá porque el gate de dominio ya resolvió las
    # tablas autorizadas: sembrarlas evita que `_load_fact_table` y
    # `_run_guarded_sql` vuelvan a pegarle a la metadata DB por lo mismo.
    budget: Dict[str, Any] = {}

    if not current_user.is_admin and user_role_name not in ADMIN_ROLES:
        allowed_tables = GovernanceGuard.get_allowed_tables_for_role(
            user_role=user_role_name, is_admin=current_user.is_admin, db=db,
            role_id=current_user.role_id, connection_id=conn_id,
        )
        budget["allowed_tables"] = allowed_tables
        domain_denial = GovernanceGuard.check_domain_governance(
            " ".join(sorted(allowed_tables)), user_role_name, allowed_tables
        )
        if domain_denial:
            _persist_audit_log(
                db=db,
                user_id=current_user.id,
                username=current_user.username,
                user_role=user_role_name,
                question_prompt=payload.question or "Prediccion: forecast y retencion",
                sql_generated=None,
                validation_status="RECHAZADO_RBAC",
                target_database=target_db_name,
                error_message=domain_denial,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=domain_denial)

    started_at = time.perf_counter()

    forecast_data: Optional[Dict[str, Any]] = None
    retention_data: Optional[Dict[str, Any]] = None
    quality: List[Dict[str, Any]] = []
    errors: List[str] = []

    async def _run(label: str, fn, *args, quietly: bool = False):
        """Ejecuta una prediccion en un hilo, aislando el fallo.

        Un forecast que no se puede calcular no puede tumbar a la retencion que
        si se puede: son preguntas distintas sobre la misma base. El motivo se
        acumula en `errors` para que quede a la vista en vez de convertirse en un
        bloque en null que el usuario no puede distinguir de "no aplica".

        Por que un hilo: los tres bloques son SQLAlchemy/DB SINCRONO dentro de un
        `async def`, asi que en serie bloqueaban el event loop y cualquier otra
        request del servidor esperaba a que terminara el forecast. Los tres son
        independientes (misma conexion, tablas distintas), asi que `to_thread` los
        superpone sin tocar como funcionan por dentro.

        Cada bloque abre su PROPIA sesion: una `Session` de SQLAlchemy no es
        thread-safe, y compartir la de la request entre los tres hilos seria una
        carrera silenciosa. La sesion de la request sigue usandose, en serie, para
        la auditoria del final.
        """
        def _call():
            session = SessionLocal()
            try:
                return fn(session, *args)
            finally:
                session.close()

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            logger.warning("Prediccion no disponible (%s): %s", label, exc)
            if not quietly:
                errors.append(f"{label}: {exc}")
            return None

    bloques = {}
    if payload.include_forecast:
        bloques["forecast"] = _run(
            "Pronóstico", forecast_service.run_forecast, conn_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget,
        )
    if payload.include_retention:
        bloques["retention"] = _run(
            "Retención", forecast_service.run_retention, conn_id, user_role_name,
            current_user.is_admin, current_user.role_id, payload.top_limit, budget,
        )
    if payload.include_data_quality:
        # Best-effort: si un check no corre, el reporte entero sigue sirviendo.
        bloques["quality"] = _run(
            "Auditoria de calidad", forecast_service.audit_data_quality, conn_id, user_role_name,
            current_user.is_admin, current_user.role_id, budget, quietly=True,
        )

    resultados = dict(zip(bloques, await asyncio.gather(*bloques.values())))
    forecast_data = resultados.get("forecast")
    retention_data = resultados.get("retention")
    quality = resultados.get("quality") or []

    forecast_card = None
    if forecast_data is not None:
        forecast_card = ForecastCard(**{
            k: v for k, v in forecast_data.items()
            if k in ForecastCard.model_fields
        })

    retention_report = None
    if retention_data is not None:
        # `score_retention` devuelve `tiers` como DICT indexado por nombre
        # ('unico', 'recurrente', 'fiel') y el valor NO incluye la clave. Hay que
        # inyectarla: `RetentionTier(**valor)` sin esto levanta `Field required:
        # tier` y el endpoint responde 500 en TODAS las llamadas de retencion.
        raw_tiers = retention_data.get("tiers") or {}
        if isinstance(raw_tiers, dict):
            tiers = [RetentionTier(**{"tier": name, **stats}) for name, stats in raw_tiers.items()]
        else:
            tiers = [RetentionTier(**t) for t in raw_tiers]
        retention_report = RetentionReport(**{
            "total_clients": retention_data.get("total_clients", 0),
            "total_revenue": retention_data.get("total_revenue", 0.0),
            "last_period": retention_data.get("last_period"),
            "tiers": tiers,
            "top": [RetentionClient(**c) for c in retention_data.get("top", [])],
            "truncated_by_limit": retention_data.get("truncated_by_limit", False),
            "entity_column": retention_data.get("entity_column"),
            "metric_column": retention_data.get("metric_column"),
            "date_column": retention_data.get("date_column"),
            "income_only": retention_data.get("income_only", True),
            "sql": retention_data.get("sql"),
            "reason": retention_data.get("reason"),
        })

    response = PredictionResponse(
        question=payload.question,
        forecast=forecast_card,
        retention=retention_report,
        errors=errors,
        data_quality=quality,
    )

    # La prediccion tambien deja rastro. Sin esto, un forecast publicado no tiene
    # la misma trazabilidad que un KPI y el compliance ve una cifra en la UI sin
    # registro de quien la pidio ni con que SQL.
    #
    # `execution_time_ms` se mide de verdad. Un 0 hardcodeado no es "instantanea":
    # es un campo de latencia falso en el CSV de compliance, y la prediccion
    # recorre N COUNT(*) sobre la fact table.
    response.audit_log_id = _persist_audit_log(
        db=db,
        user_id=current_user.id,
        username=current_user.username,
        user_role=user_role_name,
        question_prompt=payload.question or "Prediccion: forecast y retencion",
        sql_generated=(forecast_data or {}).get("sql") or (retention_data or {}).get("sql"),
        validation_status="PREDICCION_DETERMINISTA",
        target_database=target_db_name,
        execution_time_ms=int((time.perf_counter() - started_at) * 1000),
        rows_returned=len((forecast_data or {}).get("series", []) or []),
        result_snapshot=response.model_dump_json(),
    )
    return response


@router.get("/suggestions", response_model=SuggestionsResponse)
async def get_dynamic_suggestions(
    connection_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> SuggestionsResponse:
    """
    Returns role and table-specific question suggestions dynamically via LLM with fallback.
    """
    if current_user is None:
        generic_suggestions = [
            "📊 Resumen de registros y métricas principales",
            "📈 Tendencias y distribución de datos acumulados",
            "📋 Listado detallado de tablas autorizadas",
            "💡 Consultas analíticas para toma de decisiones"
        ]
        return SuggestionsResponse(
            user_role=None,
            allowed_tables=None,
            suggestions=generic_suggestions
        )

    # La conexion del pedido: un id explicito inexistente es 404 y no un
    # fallback silencioso a otra base. Sin id manda la activa, o la ultima.
    effective_conn_id = connection_id
    if db is not None:
        try:
            effective_conn_id = SchemaInspector.resolve_connection(db, connection_id).id
        except HTTPException:
            raise
        except Exception:
            # Sin conexion resuelta, `effective_conn_id` queda None y el prompt
            # degrada. Lo que no puede pasar es devolver la sesion abortada.
            discard_failed_transaction(db)

    # Mismo gate que `/chat/query`, `/chat/predict`, `/catalog/data-dictionary` y
    # `/system/anomalies`. Este endpoint resolvia el nombre con su propio fallback
    # ("sin rol -> Usuario Consultor") y despues lo devolvia EN EL CUERPO de la
    # respuesta: `user_role` y `allowed_tables` de la matriz del Consultor. No es
    # el leak de columnas del diccionario, pero es la misma confusion de
    # identidad: una cuenta sin perfil no puede ver las tablas de otro perfil.
    #
    # Va DESPUES de resolver `effective_conn_id` para poder nombrar la conexion en
    # la auditoria de la denegacion. La rama de `current_user is None` de mas
    # arriba NO pasa por aca: el anonimo recibe sugerencias genericas y
    # `allowed_tables=None`, y asi sigue.
    role_name = _require_assigned_role(
        current_user, db, "Sugerencias: GET /chat/suggestions",
        _resolve_target_database(db, effective_conn_id) if effective_conn_id else "demo_corporativa.db",
    )
    is_admin = current_user.is_admin or role_name in ADMIN_ROLES

    allowed_tables = QueryEngine.get_allowed_tables_for_role(
        user_role=role_name,
        is_admin=is_admin,
        db=db,
        role_id=current_user.role_id,
        connection_id=effective_conn_id
    )

    schema_prompt = ""
    try:
        from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
        s_info = DynamicSchemaPruningService.get_authorized_schema_prompt(
            db=db,
            user_role=role_name,
            role_id=current_user.role_id,
            is_admin=is_admin,
            connection_id=effective_conn_id
        )
        schema_prompt = s_info.get("schema_prompt", "")
    except Exception:
        # Sin prompt de esquema las sugerencias siguen siendo genericas: ese es el
        # best-effort. La sesion, en cambio, tiene que quedar limpia.
        discard_failed_transaction(db)

    suggestions = await QueryEngine.get_dynamic_suggestions_with_llm(
        user_role=role_name,
        allowed_tables=allowed_tables,
        schema_prompt=schema_prompt
    )

    return SuggestionsResponse(
        user_role=role_name,
        allowed_tables=list(allowed_tables),
        suggestions=suggestions
    )

# =========================================================================
# CHAT THREADS PERSISTENCE & HISTORY ENDPOINTS
# =========================================================================

_SHARED_TITLE = "Hilo compartido"
_SHARED_QUESTION = "Resultado compartido"
_SHARED_SUMMARY = (
    "Vista compartida: la tabla se generó de nuevo con la matriz RBAC de tu "
    "perfil. No se muestran gráficos, resúmenes, preguntas ni el SQL del dueño."
)


def _rebuild_shared_results(
    results: List[Any],
    *,
    allowed_tables: Set[str],
    blocked_columns: Set[str],
    masked_columns: Set[str],
    is_admin: bool,
    connection: CorporateConnection,
    target_database: str,
    db: Optional[Session] = None,
    current_user: Optional[User] = None,
    role_name: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Regenera la vista compartida desde el SQL, sin confiar en el cliente.

    El dueño guarda `results` tal como los recibió (o como quiera: el POST
    acepta JSON libre). Validar solo `traceability.sql_executed` no prueba que
    `data_rows`, gráficos o preguntas hayan salido de ese SQL. Por eso la vista
    compartida ignora todo el contenido del cliente y vuelve a ejecutar el SQL
    con la matriz RBAC del lector; las filas frescas son las únicas que se
    enmascaran y se devuelven.

    Cualquier resultado sin SQL verificable se deniega: un resumen o un gráfico
    sin trazabilidad no se puede revalidar. La respuesta es solo tabla; los
    artefactos derivados del dueño no se sirven.

    ponytail: una consulta por resultado al abrir el hilo. Si un hilo con
    decenas de resultados vuelve lenta la vista, el siguiente paso es generar
    un snapshot de servidor al compartir (opción B), no aflojar la regeneración.
    """
    rebuilt: List[Dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permisos para ver este hilo compartido: "
                    "contiene un resultado en un formato que no se puede regenerar."
                ),
            )

        traceability = result.get("traceability")
        sql = traceability.get("sql_executed") if isinstance(traceability, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permisos para ver este hilo compartido: "
                    "contiene un resultado sin SQL verificable. No se sirven "
                    "resúmenes ni gráficos sin trazabilidad."
                ),
            )

        try:
            _, secured_sql, metadata = ASTValidator.validate_and_secure_sql(
                sql,
                dialect="postgres",
                allowed_tables=allowed_tables,
                blocked_columns=blocked_columns,
                is_admin=is_admin,
            )
        except ASTValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "No tienes permisos para ver este hilo compartido: "
                    f"su consulta usa datos fuera de tu matriz RBAC. {exc}"
                ),
            ) from exc

        started = time.time()
        try:
            rows = SQLExecutor.execute_raw_sql(connection, secured_sql, dialect="postgres")
        except Exception as exc:
            logger.exception(
                "No se pudo regenerar un resultado del hilo compartido %s",
                getattr(connection, "id", "?"),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "No se pudo regenerar la tabla del hilo compartido. "
                    "No se muestran datos guardados sin verificación."
                ),
            ) from exc

        if masked_columns and rows:
            mask_query_rows(rows, masked_columns, secured_sql, dialect="postgres")

        rebuilt.append({
            "question": _SHARED_QUESTION,
            "summary_text": _SHARED_SUMMARY,
            "executive_report": None,
            "kpis": [],
            "chart_type": "none",
            "chart_option": {},
            "data_columns": list(rows[0].keys()) if rows else [],
            "data_rows": rows,
            "response_type": "data_analysis",
            "conversational_response": None,
            "grounding_info": None,
            "presentation_hints": {
                "show_executive_report": False,
                "show_kpis": False,
                "show_chart": False,
                "preferred_view": "table",
                "summary_style": "concise",
            },
            "suggested_questions": [],
            "clarification_options": [],
            "anomalies_detected": [],
            "sql_explanation": None,
            "nulls_detected": None,
            "traceability": {
                # El SQL del dueño puede contener literales con datos. La vista
                # compartida no lo devuelve; la UI oculta el bloque si está vacío.
                "sql_executed": "",
                "execution_time_ms": int((time.time() - started) * 1000),
                "rows_returned": len(rows),
                "validation_status": "REVALIDADO",
                "schema_tables_used": metadata.get("tables_used") or [],
                "explanation": "Tabla regenerada con la matriz RBAC del lector.",
                "target_database": target_database,
            },
            "audit_log_id": None,
        })

    # La lectura compartida regenera datos frescos y no devuelve el SQL del
    # dueño, pero sigue siendo una consulta corporativa: sin este registro, el
    # acceso a la tabla no dejaba huella en `audit_logs`. Se escribe una sola
    # fila por apertura y se propaga su id a los resultados para que la UI y el
    # export de compliance puedan enlazarla.
    if db is not None and current_user is not None and rebuilt:
        audit_id = _persist_audit_log(
            db=db,
            user_id=current_user.id,
            username=current_user.username,
            user_role=(
                role_name
                or getattr(getattr(current_user, "role", None), "name", None)
            ),
            question_prompt=(
                f"Lectura de hilo compartido '{thread_id or '?'}' "
                f"({len(rebuilt)} resultado(s) regenerado(s))."
            ),
            sql_generated=None,
            validation_status="HILO_COMPARTIDO_REVALIDADO",
            target_database=target_database,
            rows_returned=sum(
                len(item.get("data_rows") or []) for item in rebuilt
            ),
        )
        if audit_id is not None:
            for item in rebuilt:
                item["audit_log_id"] = audit_id

    return rebuilt

@router.get("/threads", response_model=List[ChatThreadSummary])
def list_chat_threads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Lists saved chat conversation threads for the current authenticated user."""
    threads = db.query(ChatConversation).filter(
        ChatConversation.user_id == current_user.id
    ).order_by(ChatConversation.updated_at.desc()).all()

    summaries = []
    for t in threads:
        try:
            msgs = json.loads(t.messages_json or "[]")
            msg_count = len(msgs)
        except Exception:
            msg_count = 0
        summaries.append(ChatThreadSummary(
            id=t.id,
            title=t.title,
            connection_id=t.connection_id or 1,
            message_count=msg_count,
            updated_at=t.updated_at.isoformat() if t.updated_at else ""
        ))
    return summaries

@router.get("/threads/{thread_id}", response_model=ChatThreadDetail)
def get_chat_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Gets details and all QueryResult messages for a specific conversation thread."""
    thread = db.query(ChatConversation).filter(
        ChatConversation.id == thread_id,
        ChatConversation.user_id == current_user.id
    ).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Hilo de conversación no encontrado")
    try:
        results = json.loads(thread.messages_json or "[]")
    except Exception:
        results = []
    return ChatThreadDetail(
        id=thread.id,
        title=thread.title,
        connection_id=thread.connection_id or 1,
        results=results,
        created_at=thread.created_at.isoformat() if thread.created_at else "",
        updated_at=thread.updated_at.isoformat() if thread.updated_at else ""
    )

@router.get("/threads/shared/{thread_id}", response_model=ChatThreadDetail)
def get_shared_chat_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Gets details and all QueryResult messages for a conversation thread the owner marked as shared.

    Solo se sirve si `is_shared` es True. Antes el endpoint filtraba unicamente por
    id, asi que cualquier usuario autenticado que adivinara un id podia leer los
    `data_rows` corporativos de otro. Los mensajes contienen filas reales de la BD
    del cliente, no un resumen.

    Compartir NO es una excepcion a la gobernanza: el SQL guardado se revalida
    contra la matriz RBAC del LECTOR y se vuelve a ejecutar con esa misma matriz.
    Se devuelve solo la tabla fresca: las filas, graficos, resumenes y preguntas
    que guardo el dueño no se sirven porque no se puede probar que hayan salido
    de ese SQL. Un hilo sin SQL verificable devuelve 403; si la regeneracion
    falla, 503, nunca una caida a los datos guardados.
    """
    thread = db.query(ChatConversation).filter(
        ChatConversation.id == thread_id,
        ChatConversation.is_shared.is_(True),
    ).first()
    if not thread:
        # 404 y no 403: no revelamos si el hilo existe pero es privado.
        raise HTTPException(status_code=404, detail="Hilo de conversación no encontrado")

    connection_id = thread.connection_id or 1
    target_database = _resolve_target_database(db, connection_id)
    role_name = _require_assigned_role(
        current_user,
        db,
        f"Shared thread: GET /chat/threads/shared/{thread_id}",
        target_database,
    )
    is_admin = _is_admin(current_user)
    allowed_tables = GovernanceGuard.get_allowed_tables_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id, connection_id=connection_id,
    )
    blocked_columns = GovernanceGuard.get_blocked_columns_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id, connection_id=connection_id,
    )
    masked_columns = GovernanceGuard.get_masked_columns_for_role(
        role_name, is_admin, db=db, role_id=current_user.role_id, connection_id=connection_id,
    )

    # La vista compartida no usa `data_rows` del cliente: necesita la conexión
    # real para regenerar la tabla. Un conector inexistente es 404; uno que no
    # se puede abrir se falla cerrado más abajo (503), nunca con datos guardados.
    try:
        connection = SchemaInspector.resolve_connection(db, connection_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Shared thread: no se pudo resolver la conexión %s", connection_id
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo abrir la conexión corporativa del hilo compartido.",
        ) from exc

    try:
        results = json.loads(thread.messages_json or "[]")
    except Exception:
        results = []
    results = _rebuild_shared_results(
        results,
        allowed_tables=allowed_tables,
        blocked_columns=blocked_columns,
        masked_columns=masked_columns,
        is_admin=is_admin,
        connection=connection,
        target_database=target_database,
        db=db,
        current_user=current_user,
        role_name=role_name,
        thread_id=thread.id,
    )
    return ChatThreadDetail(
        id=thread.id,
        title=_SHARED_TITLE,
        connection_id=connection_id,
        results=results,
        created_at=thread.created_at.isoformat() if thread.created_at else "",
        updated_at=thread.updated_at.isoformat() if thread.updated_at else ""
    )

@router.post("/threads", response_model=ChatThreadDetail)
def save_chat_thread(
    thread_in: ChatThreadCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Creates or updates a persistent chat thread with its full results stream."""
    # Query by (id, user_id): el id lo elige el cliente, asi que buscar solo por id
    # permitia que un usuario enviara el id de un hilo ajeno, lo sobreescribiera y
    # además se quedara con el (`thread.user_id = current_user.id` de abajo).
    thread = db.query(ChatConversation).filter(
        ChatConversation.id == thread_in.id,
        ChatConversation.user_id == current_user.id,
    ).first()

    # `results` es lo que el cliente dice haber visto; se guarda para el dueño.
    # La vista compartida NO lo usa: `_rebuild_shared_results` re-ejecuta el SQL
    # con el RBAC del lector. No agregar acá una validación de filas que despues
    # se olvide en el endpoint compartido.
    msgs_json = json.dumps(thread_in.results)

    if thread:
        thread.title = thread_in.title
        thread.connection_id = thread_in.connection_id or 1
        thread.messages_json = msgs_json
        thread.is_shared = bool(getattr(thread_in, "is_shared", False))
        thread.updated_at = utcnow_naive()
    else:
        thread = ChatConversation(
            id=thread_in.id,
            user_id=current_user.id,
            title=thread_in.title,
            connection_id=thread_in.connection_id or 1,
            messages_json=msgs_json,
            is_shared=bool(getattr(thread_in, "is_shared", False)),
            created_at=utcnow_naive(),
            updated_at=utcnow_naive()
        )
        db.add(thread)

    try:
        db.commit()
    except Exception:
        db.rollback()
        # Carrera real: otra peticion creo el hilo con el mismo id entre nuestro
        # SELECT y nuestro INSERT. Se recupera SOLO el hilo propio.
        #
        # Este bloque era la segunda puerta del secuestro de hilos: buscaba por id
        # sin user_id y asignaba `thread.user_id = current_user.id`, asi que un
        # INSERT con el id de otro (y el choque de primary key) terminaba
        # apropiandose del hilo ajeno. El filtro por user_id lo cierra.
        thread = db.query(ChatConversation).filter(
            ChatConversation.id == thread_in.id,
            ChatConversation.user_id == current_user.id,
        ).first()
        if thread:
            thread.title = thread_in.title
            thread.connection_id = thread_in.connection_id or 1
            thread.messages_json = msgs_json
            thread.is_shared = bool(getattr(thread_in, "is_shared", False))
            thread.updated_at = utcnow_naive()
            db.commit()

    if thread is None or not getattr(thread, "id", None):
        # El id ya existe y es de otro usuario: es un intento de sobrescritura sobre
        # un hilo ajeno. No se revela nada mas alla del conflicto de clave primaria.
        raise HTTPException(status_code=409, detail="El identificador de hilo ya está en uso.")

    if thread:
        db.refresh(thread)

    return ChatThreadDetail(
        id=thread.id,
        title=thread.title,
        connection_id=thread.connection_id or 1,
        results=thread_in.results,
        created_at=thread.created_at.isoformat() if thread.created_at else "",
        updated_at=thread.updated_at.isoformat() if thread.updated_at else ""
    )

@router.delete("/threads/{thread_id}")
def delete_chat_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Deletes a single chat conversation thread."""
    thread = db.query(ChatConversation).filter(
        ChatConversation.id == thread_id,
        ChatConversation.user_id == current_user.id
    ).first()
    if not thread:
        # Antes devolvia success=True tambien cuando el hilo no existia (o era
        # de otro usuario), asi que el llamador no podia distinguir borrado de
        # no-hallado y un DELETE reintentado parecia haber hecho algo.
        raise HTTPException(status_code=404, detail="Hilo de conversación no encontrado.")
    db.delete(thread)
    db.commit()
    return {"success": True, "message": "Hilo eliminado correctamente"}

@router.delete("/threads")
def clear_chat_threads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Clears all conversation threads for the current user.

    A diferencia de `DELETE /threads/{id}`, acá NO hay 404: la semantica es "borra
    todo lo mio" y no haber nada que borrar es un no-op honesto, no un error (si no,
    un segundo click del usuario — o un retry de red — fallaria). Lo que si se hace
    es devolver `deleted_count`, para que el llamador pueda distinguir "borre 3" de
    "no habia nada" en vez de leer un `success: True` indistinguible.
    """
    deleted = db.query(ChatConversation).filter(
        ChatConversation.user_id == current_user.id
    ).delete(synchronize_session=False)
    db.commit()
    if deleted:
        msg = f"{deleted} hilo(s) eliminado(s)"
    else:
        msg = "No había hilos para eliminar"
    return {"success": True, "message": msg, "deleted_count": deleted}

# =========================================================================
# QUERY FEEDBACK & SELF-LEARNING ENDPOINTS
# =========================================================================

def _is_admin(user: User) -> bool:
    """Admin segun la misma regla que `get_current_admin`, pero como predicado.

    Reutilizado por los dos endpoints de learning memory: la dependencia
    `get_current_admin` levanta 403 y sirve para todo el endpoint, pero
    `submit_query_feedback` tambien sirve para no-admin (su feedback de auditoria
    es legitimo), asi que necesita el corte por rol sin cortar el endpoint entero.
    """
    role_name = user.role.name if user.role else ""
    return bool(user.is_admin or role_name in ADMIN_ROLES)

@router.post("/feedback", response_model=ChatFeedbackResponse)
def submit_query_feedback(
    feedback_in: ChatFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """
    Records explicit user feedback (👍 / 👎) for a query and reinforces or demotes
    in few-shot learning memory.
    """
    learning_saved = False
    is_positive = feedback_in.rating.lower() == "positive"

    # `QueryLearningMemory` es un recurso COMPARTIDO por conexion: `sql_executor`
    # lo inyecta en el prompt de todos los usuarios de esa conexion (tag
    # "[Consulta Maestra Verificada]") y no tiene columna `user_id`, asi que no se
    # puede distinguir "mi feedback" de "la memoria de la plataforma". Un
    # "Usuario Consultor" podia POSTear aqui un SQL arbitrario con
    # `is_golden: true` y contaminar el prompt de toda la empresa (y pisar la
    # golden query curada por el admin). El SQL no se ejecuta — pasa por
    # ASTValidator y governance_guard — pero el hueco de autorizacion es real.
    #
    # Corte por rol: solo admin escribe la memoria compartida. Los demas siguen
    # pudiendo calificar su propia consulta (bloque 1, ya filtrado por
    # propietario) — lo que pierden es el refuerzo few-shot, que nunca fue suyo.
    is_admin_user = _is_admin(current_user)
    if feedback_in.is_golden and not is_admin_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Solo un Administrador puede marcar consultas maestras."
        )

    # 1. Update AuditLog if available
    if feedback_in.audit_log_id:
        # Filtro por propietario: `audit_log_id` lo elige el cliente, asi que sin
        # esto cualquier usuario autenticado reescribia el `validation_status` y el
        # `error_message` de la evidencia de auditoria de otro, y ese texto libre
        # terminaba en el CSV de compliance que abre el admin.
        audit = db.query(AuditLog).filter(
            AuditLog.id == feedback_in.audit_log_id,
            AuditLog.user_id == current_user.id,
        ).first()
        if audit:
            label = "[CALIFICADO_POSITIVO]" if is_positive else "[CALIFICADO_NEGATIVO]"
            if label not in (audit.validation_status or ""):
                audit.validation_status = f"{audit.validation_status or ''} {label}".strip()
            if feedback_in.comment:
                audit.error_message = f"{audit.error_message or ''} (Feedback: {feedback_in.comment})".strip()
            db.commit()

    # 2. If positive and SQL is present, strengthen learning memory.
    # Gate por rol: la memoria es compartida (ver nota arriba). Un no-admin puede
    # calificar su consulta pero no escribir SQL en el prompt de los demas.
    if is_positive and is_admin_user and feedback_in.sql and feedback_in.question:
        clean_q = feedback_in.question.strip().lower()
        existing_mem = db.query(QueryLearningMemory).filter(
            QueryLearningMemory.connection_id == feedback_in.connection_id,
            QueryLearningMemory.question_pattern == clean_q
        ).first()

        if existing_mem:
            existing_mem.execution_count = (existing_mem.execution_count or 1) + 5
            existing_mem.successful_sql = feedback_in.sql
            if feedback_in.is_golden:
                existing_mem.is_golden = True
        else:
            new_mem = QueryLearningMemory(
                question_pattern=clean_q,
                connection_id=feedback_in.connection_id,
                user_role=current_user.role.name if current_user.role else "Usuario",
                successful_sql=feedback_in.sql,
                execution_count=5,
                was_self_healed=False,
                is_golden=bool(feedback_in.is_golden)
            )
            db.add(new_mem)
        db.commit()
        learning_saved = True

    # El mensaje tiene que concordar con `learning_saved`: antes un no-admin recibia
    # "se reforzó en la memoria de aprendizaje" aunque no se hubiera escrito nada.
    if is_positive and learning_saved:
        msg = "¡Gracias! Esta consulta se reforzó en la memoria de aprendizaje de IA."
    elif is_positive:
        msg = "¡Gracias por tu calificación! Solo un Administrador puede reforzar la memoria compartida de consultas."
    else:
        msg = "Gracias por tu feedback. Lo utilizaremos para mejorar futuras respuestas."
    return ChatFeedbackResponse(success=True, message=msg, learning_saved=learning_saved)

@router.post("/golden-query")
def toggle_golden_query(
    item_in: GoldenQueryRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Marks or unmarks a query as a Golden Sample (highest priority few-shot reference).

    Solo Administrador. Este endpoint ESCRIBE el recurso compartido: ademas de no
    tener `user_id` la tabla, `toggle_golden_query` hace upsert por
    `(connection_id, question_pattern)`, asi que cualquiera que llegara aca podia
    pisar la golden query curada por el admin y suplantarla con un SQL propio, que
    despues `sql_executor` le inyecta a todos los usuarios de la conexion.
    """
    clean_q = item_in.question.strip().lower()
    mem = db.query(QueryLearningMemory).filter(
        QueryLearningMemory.connection_id == item_in.connection_id,
        QueryLearningMemory.question_pattern == clean_q
    ).first()
    if mem:
        mem.is_golden = item_in.is_golden
        mem.successful_sql = item_in.sql
    else:
        mem = QueryLearningMemory(
            question_pattern=clean_q,
            connection_id=item_in.connection_id,
            user_role=current_admin.role.name if current_admin.role else "Usuario",
            successful_sql=item_in.sql,
            execution_count=10,
            was_self_healed=False,
            is_golden=item_in.is_golden
        )
        db.add(mem)
    db.commit()
    msg = "Consulta marcada como Consulta Maestra (Golden Sample)." if item_in.is_golden else "Consulta desmarcada como Consulta Maestra."
    return {"success": True, "message": msg, "is_golden": item_in.is_golden}

@router.get("/golden-queries", response_model=GoldenQueryList)
def list_golden_queries(
    connection_id: int = Query(1, description="La memoria es compartida por conexion"),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Lista lo que el motor aprende e inyecta en el prompt, con su uso real.

    `QueryLearningMemory` es un recurso COMPARTIDO: `sql_executor` la usa como
    few-shot para TODOS los usuarios de la conexion y no tiene columna `user_id`.
    Es decir, un admin puede haber enseñado a toda la empresa un SQL equivocado y
    no habia forma de ver que hay ni de revertirlo. Este endpoint es la salida de
    inspeccion; el DELETE de abajo, la de deshacerlo.

    El filtro por `connection_id` es el aislamiento: lo que se aprendio de una base
    no se inyecta en otra, asi que tampoco se lista junto.
    """
    rows = db.query(QueryLearningMemory).filter(
        QueryLearningMemory.connection_id == connection_id
    ).order_by(
        # Primero las doradas (es lo que pesa en el prompt), luego las mas usadas:
        # el uso real es la senal que dice si esto sirve o es ruido acumulado.
        QueryLearningMemory.is_golden.desc(),
        QueryLearningMemory.execution_count.desc(),
    ).all()

    return GoldenQueryList(
        items=[
            GoldenQueryOut(
                id=m.id,
                question_pattern=m.question_pattern,
                successful_sql=m.successful_sql,
                user_role=m.user_role,
                # Columnas con default: pueden venir en NULL si la fila se creo
                # antes del default. `bool(None)`/`or 0` los tratan como lo que
                # el modelo ya significa con esos valores.
                is_golden=bool(m.is_golden),
                execution_count=m.execution_count or 0,
                was_self_healed=bool(m.was_self_healed),
                created_at=m.created_at.isoformat() if m.created_at else "",
                updated_at=m.updated_at.isoformat() if m.updated_at else "",
            )
            for m in rows
        ],
        total=len(rows),
    )

@router.delete("/golden-queries/{memory_id}")
def delete_golden_query(
    memory_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Borra una memoria compartida de la conexion.

    Admin nomas: la fila se inyecta en el prompt de todos los usuarios de la
    conexion, asi que borrarla es una decision con efecto global.

    404 si no existe, igual que `DELETE /threads/{id}`: un borrado silencioso con
    `success: True` hacia que un DELETE reintentado (o de una fila ya borrada) se
    leyera como "lo revirti", que es exactamente la accion que el admin queria
    verificar.
    """
    mem = db.query(QueryLearningMemory).filter(
        QueryLearningMemory.id == memory_id
    ).first()
    if not mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memoria de aprendizaje no encontrada."
        )
    db.delete(mem)
    db.commit()
    return {"success": True, "message": "Memoria de aprendizaje eliminada correctamente"}


# =========================================================================
# DASHBOARD WIDGETS (PIN TO DASHBOARD) ENDPOINTS
# =========================================================================

@router.get("/widgets", response_model=List[DashboardWidgetOut])
def list_dashboard_widgets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Lists all pinned dashboard widgets for the current user."""
    widgets = db.query(DashboardWidget).filter(
        DashboardWidget.user_id == current_user.id
    ).order_by(DashboardWidget.created_at.desc()).all()
    
    return [
        DashboardWidgetOut(
            id=w.id,
            user_id=w.user_id,
            title=w.title,
            connection_id=w.connection_id,
            chart_type=w.chart_type,
            chart_option_json=w.chart_option_json,
            kpis_json=w.kpis_json,
            query_text=w.query_text,
            created_at=w.created_at.isoformat() if w.created_at else ""
        )
        for w in widgets
    ]

@router.post("/widgets", response_model=DashboardWidgetOut, status_code=status.HTTP_201_CREATED)
def pin_dashboard_widget(
    widget_in: DashboardWidgetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Pins a new analytics widget to the user's executive dashboard."""
    new_widget = DashboardWidget(
        user_id=current_user.id,
        title=widget_in.title,
        connection_id=widget_in.connection_id or 1,
        chart_type=widget_in.chart_type,
        chart_option_json=widget_in.chart_option_json,
        kpis_json=widget_in.kpis_json,
        query_text=widget_in.query_text,
        created_at=utcnow_naive()
    )
    db.add(new_widget)
    db.commit()
    db.refresh(new_widget)
    return DashboardWidgetOut(
        id=new_widget.id,
        user_id=new_widget.user_id,
        title=new_widget.title,
        connection_id=new_widget.connection_id,
        chart_type=new_widget.chart_type,
        chart_option_json=new_widget.chart_option_json,
        kpis_json=new_widget.kpis_json,
        query_text=new_widget.query_text,
        created_at=new_widget.created_at.isoformat()
    )

@router.delete("/widgets/{widget_id}")
def unpin_dashboard_widget(
    widget_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Unpins / deletes a widget from the user's dashboard.

    Mismo patron que ya se corrigio en `delete_chat_thread`: el filtro por
    `user_id` evita el IDOR, pero el `if w:` sin `else` hacia que un widget
    inexistente (o ajeno) respondiera `success: True` igual. Aca la semantica SI
    es "borra ESTE widget", asi que no haberlo es 404.
    """
    w = db.query(DashboardWidget).filter(
        DashboardWidget.id == widget_id,
        DashboardWidget.user_id == current_user.id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget no encontrado")
    db.delete(w)
    db.commit()
    return {"success": True, "message": "Widget removido del tablero"}

