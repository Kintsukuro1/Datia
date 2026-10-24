"""Proyecciones: contexto de la fact table, analisis por LLM y narrativa.

Que aporta este modulo sobre lo que ya existe
--------------------------------------------
La serie la arma `forecast_service` (misma fact table autorizada, mismo
`ASTValidator`, mismo `SQLExecutor`, mismo enmascarado) y la proyeccion la
calcula `ml_models` (tres metodos, backtest walk-forward, banda por paso).
Aca viven las tres piezas que faltaban para publicarlas por HTTP:

1. `load_fact_context`: resuelve la fact table del rol y sus columnas REALES.
2. `analyze_question`: deja que el LLM elija la metrica y la fecha —con una lista
   CERRADA de columnas y validacion del lado del backend—. El modelo propone, el
   backend decide: lo que viaja al SQL y a la respuesta es siempre un nombre que
   existe en los metadatos, nunca el texto que devolvio el modelo.
3. `narrate`: redacta la lectura de numeros YA calculados, con un contrato
   explicito ("solo podes citar numeros que estan en este payload") y una
   plantilla determinista como respaldo. La narrativa no es el dato: si el LLM
   no responde, la respuesta se queda sin lectura pero nunca sin los hechos.

Por que el SQL lo sigue armando el codigo: un modelo de 7B que tiene que
inventar el nombre de una tabla y de dos columnas para producir una proyeccion
agrega una fuente de error que no hace falta. El LLM solo elige entre columnas
que ya existen, y esa eleccion se valida contra la lista real antes de usarse.
"""

from __future__ import annotations

import calendar
import json
import math
import re
import statistics
import time
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.core.time import utcnow_naive
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.catalog.services import enrich_estimator
from app.modules.chat_engine.ast_validator import ASTValidationError
from app.modules.chat_engine.governance_guard import GovernanceGuard
from app.modules.chat_engine.forecast_calculator import (
    next_period,
    normalize_period,
    period_distance,
)
from app.modules.chat_engine.forecast_service import (
    ForecastUnavailable,
    _is_date_col,
    _is_dimension_col,
    _is_num_col,
    # Los helpers privados se importan A PROPOSITO, no se copian: la fact table
    # tiene que ser la MISMA que la del forecast y del chat. Dos implementaciones
    # divergen en el primer arreglo y el mismo rol termina leyendo otra tabla
    # segun por que endpoint entro.
    _load_fact_table,
    _motivo_sin_eje_temporal,
    _motivo_sin_metrica,
    _run_guarded_sql,
    build_group_series_sql,
    build_group_totals_sql,
    build_series_sql,
    looks_like_metric,
    resolve_columns,
)
from app.modules.chat_engine.llm_service import LLMService
from app.modules.ml_forecast.ml_models import METHODS, MIN_BACKTESTS_RELIABLE, build_projection
from app.modules.ml_forecast.schemas import (
    DEFAULT_HORIZON,
    SUPPORTED_HORIZONS,
    ProjectionResponse,
)
from app.modules.telemetry_audit.models import AuditLog

# Techo de columnas en el prompt del analisis. Cada linea son ~12 tokens, asi que
# 120 columnas son ~1.500 tokens de prompt: con mas, el costo por request crece
# sin agregar informacion (una fact table con mas de 120 columnas es el caso
# raro). `ponytail:` lo que queda afuera de la lista NO queda afuera de la
# eleccion: el fallback heuristico ve todas las columnas CANDIDATAS (las que el
# rol puede leer, ya sin BLOCKED/MASKED), y un nombre que el modelo no vio
# simplemente no lo va a proponer.
_MAX_PROMPT_COLUMNS = 120

# Top de grupos del desglose. Se elige por volumen historico y no se agrega
# "Otros": una categoria residual que suma grupos no proyectados es un numero
# que parece total y no lo es.
BREAKDOWN_TOP_N = 5

# Presupuesto de la respuesta del analisis: el JSON son tres claves cortas. Un
# techo bajo evita que el modelo "explique" antes del JSON y gaste la latencia.
_ANALYSIS_MAX_TOKENS = 120
# 420 y no 300: con 300 el modelo cortaba la tercera frase a mitad ("...la
# proyeccion" sin terminar) y ese texto truncado viaja a la UI y al Informe. El
# costo son ~15 s mas en CPU; el presupuesto real lo acotan el system prompt y el
# pedido de 2 o 3 parrafos.
_NARRATION_MAX_TOKENS = 420

_ANALYSIS_SYSTEM_PROMPT = (
    "Eres un asistente de analisis de datos. Recibes una pregunta de negocio y la "
    "lista CERRADA de columnas de una tabla. Solo podes elegir nombres que esten "
    "en esa lista: un nombre que no figure invalida tu respuesta. Responde "
    "UNICAMENTE con un objeto JSON."
)

# Contrato de la narrativa: los numeros ya estan calculados por el backend. El
# modelo redacta, no calcula. El texto prohibe explicitamente citar cifras que no
# esten en el payload porque eso es exactamente lo que un 7B hace si se le pide
# "explicar el forecast" sobre una tabla de numeros.
_NARRATION_SYSTEM_PROMPT = (
    "Eres el redactor de un informe de proyeccion. Recibes un JSON con numeros YA "
    "calculados por el backend. Reglas: solo podes citar numeros que aparezcan en "
    "el JSON; no inventes cifras ni prometas precision; explica las limitaciones "
    "(historia corta, MAPE medido, huecos en la serie, pasos sin evidencia "
    "suficiente); no contradigas el metodo publicado ni el hecho de que se "
    "probaron todos los metodos candidatos. Responde en espanol, en 2 o 3 "
    "parrafos."
)

# --- Estimacion de duracion ---------------------------------------------------
# La estimacion NO mide: reusa la velocidad que el asistente de enriquecimiento
# ya midio (un probe real al LLM) y la deja cacheada. Sin esa medicion no hay
# numero creible, y se declara `fallback` en vez de inventar uno.
_FALLBACK_SECONDS = 60.0
# Piso de la estimacion medida: menos que esto se lee como una promesa rota. Dos
# llamadas al LLM local en CPU nunca bajan de ahi, y una estimacion de 0,4 s
# promete una velocidad que el hardware no tiene.
_MEASURED_FLOOR_S = 5.0
# Carga de trabajo de UNA corrida: dos llamadas cortas al LLM (elegir columnas y
# redactar) y el trabajo local del SQL y el backtest.
_ANALYSIS_OUTPUT_TOKENS = float(_ANALYSIS_MAX_TOKENS)
_NARRATION_OUTPUT_TOKENS = float(_NARRATION_MAX_TOKENS)
_PROMPT_BASE_TOKENS = 400.0  # Pregunta + lista de columnas de la fact table.
_PROMPT_TOKENS_PER_STEP = 25.0  # Payload de la narrativa, un registro por paso.
# Mismo factor que `enrich_estimator`: en CPU el prompt se procesa varias veces
# mas rapido que la generacion. Compartir el factor hace comparables las dos
# estimaciones del producto.
_PROMPT_SPEED_FACTOR = 4.0
# SQL + backtest walk-forward (que ajusta los metodos por origen) + banda por paso.
# Corre en el servidor, sin LLM, y no depende de la velocidad del modelo.
_LOCAL_OVERHEAD_S = 2.0


# ----------------------------------------------------------------------------
# Contexto de la fact table
# ----------------------------------------------------------------------------

def load_fact_context(
    db: Session,
    connection_id: int,
    user_role: str,
    is_admin: bool,
    role_id: Optional[int],
    budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fact table, columnas fisicas y columnas CANDIDATAS del rol.

    No decide la metrica final: eso es `analyze_question`. Aca se resuelve el
    contexto contra el que esa decision se valida (la lista cerrada de nombres
    reales) y se deja el fallback heuristico ya calculado.

    `cols` viaja COMPLETO al guardarrail: `_run_guarded_sql` necesita el esquema
    fisico para validar el SQL. La eleccion del LLM y la heuristica usan
    `candidate_cols`, que excluye BLOCKED y MASKED: el modelo no debe poder
    proponer una columna que el rol no puede leer, y una metrica enmascarada no
    sirve para SUM(). El filtro es defensa en profundidad, no un reemplazo del
    validador: si un nombre se colara, el AST y el enmascarado siguen ahi.

    `budget` es el cache de ESTA request (lo crea el router y lo comparten el
    conteo de tablas y el guardarrail), no un global mutable: lo cacheado son los
    permisos de UN usuario.
    """
    connection = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not connection:
        raise ForecastUnavailable(f"No existe la conexion {connection_id}.")
    if budget is None:
        budget = {}

    dialect = "postgres"
    table, cols = _load_fact_table(
        db, connection, is_admin, user_role, role_id, connection_id, dialect, budget
    )

    # Los dos conjuntos se cachean en el budget para no volver a pedir la matriz
    # de permisos en `_run_guarded_sql` (mismo request, mismo rol, misma conexion).
    blocked = budget.get("blocked_columns")
    if blocked is None:
        blocked = GovernanceGuard.get_blocked_columns_for_role(
            user_role=user_role, is_admin=is_admin, db=db, role_id=role_id,
            connection_id=connection_id,
        )
        budget["blocked_columns"] = blocked
    masked = budget.get("masked_columns")
    if masked is None:
        masked = GovernanceGuard.get_masked_columns_for_role(
            user_role=user_role, is_admin=is_admin, db=db, role_id=role_id,
            connection_id=connection_id,
        )
        budget["masked_columns"] = masked

    hidden = {str(name).lower() for name in set(blocked) | set(masked)}
    candidate_cols = [
        col for col in cols
        if str(col.get("name", "")).lower() not in hidden
    ]
    return {
        "table": table,
        "cols": cols,
        "candidate_cols": candidate_cols,
        "heuristic": resolve_columns(candidate_cols),
    }


def projection_context(
    db: Session,
    connection_id: int,
    user_role: str,
    is_admin: bool,
    role_id: Optional[int],
    budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Listas cerradas para el selector manual, ya filtradas por el rol.

    No expone el esquema completo: `candidate_cols` excluye BLOCKED y MASKED y
    las listas solo incluyen tipos utilizables. Una columna de texto se ofrece
    como dimension; no se ofrece como metrica ni como eje.
    """
    context = load_fact_context(db, connection_id, user_role, is_admin, role_id, budget)
    candidates = context["candidate_cols"]
    return {
        "table": context["table"],
        "metric_columns": [
            str(col["name"]) for col in candidates
            if _is_num_col(col) and looks_like_metric(col)
        ],
        "date_columns": [
            str(col["name"]) for col in candidates if _is_date_col(col)
        ],
        "dimension_columns": [
            str(col["name"]) for col in candidates if _is_dimension_col(col)
        ],
    }


# ----------------------------------------------------------------------------
# Analisis de la pregunta (LLM con lista cerrada, fallback heuristico)
# ----------------------------------------------------------------------------

def _canonical_column(value: Any, by_lower: Dict[str, Dict[str, Any]], predicate) -> Optional[str]:
    """Nombre REAL de `cols` para lo que eligio el LLM, o None.

    Se compara sin distinguir mayusculas (el modelo escribe `Monto` por `monto`),
    pero SIEMPRE se devuelve el nombre de los metadatos: lo que viaja al SQL es
    un nombre que existe, nunca el texto crudo del modelo. El predicado de tipo
    (numerica / fecha) se exige aca: una columna de texto como metrica daria un
    `SUM()` sobre texto, que o revienta en el motor o agrega basura.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    col = by_lower.get(value.strip().lower())
    if col is None or not predicate(col):
        return None
    return str(col.get("name"))


def _validate_llm_choice(raw: Any, cols: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Eleccion del LLM validada contra las columnas reales; None solo si `raw` no es un objeto.

    Los dos nombres se validan por separado: quedan en None los que no existen
    en los metadatos o no pasan el predicado de tipo (y el llamador decide la
    heuristica para ambos: media eleccion del LLM no se mezcla). El horizonte es
    INDEPENDIENTE de los nombres: un modelo que eligio mal la columna de fecha
    igual puede haber leido bien "los proximos 12 meses", y descartar esa
    lectura entera por un nombre es tirar la parte util de la respuesta.
    """
    if not isinstance(raw, dict):
        return None
    by_lower = {str(c.get("name", "")).lower(): c for c in cols if c.get("name")}
    metric = _canonical_column(raw.get("metric_column"), by_lower, _is_num_col)
    date = _canonical_column(raw.get("date_column"), by_lower, _is_date_col)

    # Si el modelo propone un 18, el pedido del usuario sigue siendo valido y se
    # usa ese. No se recorta a 12 ni se redondea: se ignora.
    horizon = raw.get("horizon")
    if isinstance(horizon, str) and horizon.strip().isdigit():
        horizon = int(horizon.strip())
    if horizon not in SUPPORTED_HORIZONS:
        horizon = None
    return {"metric_column": metric, "date_column": date, "horizon": horizon}


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Primer objeto JSON plano del texto, o None.

    El modelo local a veces envuelve la respuesta en ```json o agrega una frase
    antes del objeto. Extraer el primer `{...}` cubre los dos casos; el contrato
    es un objeto PLANO (tres claves con valores simples), y no se intenta
    "arreglar" un JSON roto: si no parsea, el llamador cae a la heuristica, que
    es un resultado honesto y no una respuesta a medio construir.
    """
    if not text:
        return None
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _ask_llm_for_columns(
    question: str, cols: List[Dict[str, Any]], horizon: int
) -> Optional[Dict[str, Any]]:
    """Una llamada corta al LLM que devuelve el objeto JSON parseado (o None)."""
    visibles = cols[:_MAX_PROMPT_COLUMNS]
    listing = "\n".join(f"- {c.get('name')} ({c.get('type')})" for c in visibles)
    if len(cols) > len(visibles):
        listing += f"\n[{len(cols) - len(visibles)} columnas mas no listadas por el tope del prompt]"

    prompt = (
        f"Pregunta de negocio: {question}\n\n"
        "Columnas de la tabla (lista CERRADA; solo podes elegir de aca):\n"
        f"{listing}\n\n"
        "Elegi la columna numerica que representa la metrica que hay que proyectar "
        "y la columna de fecha que define el periodo. Si no podes decidir con esta "
        "lista, usa null en ese campo (el backend decidira por su cuenta).\n"
        f"Horizonte: cuantos periodos proyectar, 3, 6 o 12. Si la pregunta no pide "
        f"otro, responde {horizon}.\n\n"
        "Responde UNICAMENTE este objeto JSON:\n"
        '{"metric_column": "<nombre exacto de la lista>", "date_column": "<nombre exacto de la lista>", "horizon": <3, 6 o 12>}'
    )
    text = await LLMService.generate_completion(
        prompt,
        system_prompt=_ANALYSIS_SYSTEM_PROMPT,
        temperature=0.05,
        max_tokens=_ANALYSIS_MAX_TOKENS,
    )
    return _parse_json_object(text or "")


def _resolve_horizon(
    choice_horizon: Optional[int], requested_horizon: Optional[int]
) -> Tuple[int, str]:
    """(horizonte que se publica, origen). Un 3/6/12 explicito no se negocia.

    `requested_horizon=None` es Auto: ahi si manda el LLM cuando propone un
    horizonte valido. Sin ninguna de las dos cosas queda el default 6, declarado
    como `default` para que la UI no lo confunda con una eleccion del usuario.
    """
    if requested_horizon is not None:
        return requested_horizon, "pedido"
    if choice_horizon is not None:
        return choice_horizon, "llm"
    return DEFAULT_HORIZON, "default"


async def analyze_question(
    question: str,
    table: str,
    cols: List[Dict[str, Any]],
    heuristic: Dict[str, Any],
    requested_horizon: Optional[int] = None,
    requested_metric: Optional[str] = None,
    requested_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Metrica, fecha y horizonte: manual > explicito > LLM (Auto) > default 6.

    Los overrides manuales se validan contra las columnas REALES y permitidas;
    un nombre invalido es una peticion mal armada y se corta con un motivo, no
    se reemplaza en silencio. Si vienen metrica, fecha y horizonte explicito, no
    se llama al LLM: el usuario ya decidio las tres cosas. Con Auto y ambos
    overrides, el LLM solo resuelve el horizonte.

    Cualquier violacion del LLM —nombre inventado, tipo equivocado, JSON roto,
    respuesta vacia o excepcion/timeout— cae al fallback heuristico. El
    horizonte explicito sobrevive a todo; en Auto se conserva el horizonte
    valido del LLM aunque las columnas caigan a la heuristica.
    """
    by_lower = {str(c.get("name", "")).lower(): c for c in cols if c.get("name")}
    manual: Dict[str, str] = {}
    if requested_metric is not None:
        metric = _canonical_column(
            requested_metric, by_lower,
            lambda c: _is_num_col(c) and looks_like_metric(c),
        )
        if metric is None:
            raise ForecastUnavailable(
                f"La columna '{requested_metric}' no es una metrica numerica permitida "
                f"en la tabla '{table}'."
            )
        manual["metric_column"] = metric
    if requested_date is not None:
        date_col = _canonical_column(requested_date, by_lower, _is_date_col)
        if date_col is None:
            raise ForecastUnavailable(
                f"La columna '{requested_date}' no es una columna de fecha permitida "
                f"en la tabla '{table}'."
            )
        manual["date_column"] = date_col

    # Con las tres decisiones tomadas no hay nada que preguntarle al modelo.
    if manual.get("metric_column") and manual.get("date_column") and requested_horizon is not None:
        return {
            "table": table,
            **manual,
            "horizon": requested_horizon,
            "horizon_source": "pedido",
            "source": "manual",
        }

    suggested_horizon = (
        requested_horizon if requested_horizon is not None else DEFAULT_HORIZON
    )
    fallback = {
        "table": table,
        "metric_column": manual.get("metric_column", heuristic.get("value_col")),
        "date_column": manual.get("date_column", heuristic.get("date_col")),
        "horizon": suggested_horizon,
        "horizon_source": "pedido" if requested_horizon is not None else "default",
        "source": "manual" if manual else "heuristica",
    }

    try:
        raw = await _ask_llm_for_columns(question, cols, suggested_horizon)
    except Exception as exc:
        # El LLM local apagado o lento es el caso NORMAL, no el excepcional: la
        # proyeccion no depende de el para existir.
        logger.info("Analisis de columnas por LLM no disponible (%s); se usa la heuristica.", exc)
        return fallback

    choice = _validate_llm_choice(raw, cols)
    if choice is None:
        logger.info("El LLM no devolvio un objeto utilizable; se usa la heuristica.")
        return fallback

    horizon, horizon_source = _resolve_horizon(choice["horizon"], requested_horizon)
    fallback["horizon"] = horizon
    fallback["horizon_source"] = horizon_source

    # Con ambos overrides, la unica decision que queda es el horizonte.
    if manual.get("metric_column") and manual.get("date_column"):
        return fallback

    if not (choice["metric_column"] and choice["date_column"]):
        # Un nombre invalido tumba las DOS columnas del LLM (no se mezcla media
        # eleccion con media heuristica), pero un override manual sobrevive.
        logger.info(
            "El LLM eligio columnas no utilizables (metrica=%r, fecha=%r); columnas por heuristica.",
            choice["metric_column"], choice["date_column"],
        )
        return fallback

    return {
        "table": table,
        "metric_column": manual.get("metric_column") or choice["metric_column"],
        "date_column": manual.get("date_column") or choice["date_column"],
        "horizon": horizon,
        "horizon_source": horizon_source,
        "source": "manual" if manual else "llm",
    }


# ----------------------------------------------------------------------------
# Proyeccion (serie + modelo + metadatos)
# ----------------------------------------------------------------------------

def _has_gaps(normalized: List[Optional[str]]) -> bool:
    """True si el eje de periodos no es continuo, o si no se pudo leer.

    Se compara la distancia entre el primero y el ultimo periodo contra la
    cantidad de periodos: una serie mensual completa da exactamente `len - 1`.
    Un periodo que `normalize_period` no puede leer deja el eje sin verificar, y
    eso se declara como hueco: asumir continuidad sobre un eje que no se pudo
    leer seria afirmar una regularidad que nadie midio.

    Una serie vacia no tiene huecos: no tiene eje. El motivo de "no alcanza" ya
    lo dice `build_projection` con la cuenta de periodos.
    """
    if not normalized:
        return False
    if any(period is None for period in normalized):
        return True
    return period_distance(normalized[0], normalized[-1]) != len(normalized) - 1


def _with_periods(
    steps: List[Dict[str, Any]], normalized: List[Optional[str]]
) -> List[Dict[str, Any]]:
    """Cada paso con su periodo futuro, calculado con `next_period` de la calculadora.

    La aritmetica de calendario NO se escribe aca: es la de `forecast_calculator`
    (la misma que publica el forecast del proximo periodo). Dos implementaciones
    divergen en el primer cruce de ano, y el mismo paso quedaria con dos fechas
    distintas segun por que endpoint se calculo.

    `period=None` cuando los periodos no son normalizables: inventar el eje
    temporal es justo lo que `normalize_period` se niega a hacer.
    """
    last = normalized[-1] if normalized else None
    with_period: List[Dict[str, Any]] = []
    for step in steps:
        period = None
        if last is not None:
            period = last
            for _ in range(int(step.get("step") or 0)):
                period = next_period(period)
        with_period.append({**step, "period": period})
    return with_period


def _partial_period(
    last_row: Dict[str, Any],
    previous_rows: List[Dict[str, Any]],
    today: date,
) -> Optional[str]:
    """Periodo en curso si parece INCOMPLETO, o None.

    Solo se excluye el mes actual, y solo cuando la tabla se comporta como
    transaccional: los meses anteriores tienen al menos 2 dias distintos con
    datos (una tabla de fotos mensuales tiene 1 y no se toca). Se compara la
    cantidad de dias del mes en curso contra la mediana historica: si es menos
    del 70%, es un mes a medias y no una caida de negocio. Cerca del cierre
    (<10% del mes restante) ya es historia suficiente y no se excluye.

    `ponytail:` es una heuristica, no un calendario de cierres contables. Un mes
    con huelga o con carga atrasada puede parecer incompleto; el escape hatch
    `include_partial_period=true` permite auditarlo. Si aparece un calendario
    fiscal real, se reemplaza esta regla por el.
    """
    raw_last = str(last_row.get("ultima_fecha") or "")[:10]
    try:
        last_day = date.fromisoformat(raw_last)
        distinct_days = int(last_row.get("dias_con_datos") or 0)
    except (TypeError, ValueError):
        return None
    if distinct_days <= 0:
        return None
    if (last_day.year, last_day.month) != (today.year, today.month):
        return None

    previous_days: List[int] = []
    for row in previous_rows:
        try:
            days = int(row.get("dias_con_datos") or 0)
        except (TypeError, ValueError):
            continue
        if days > 0:
            previous_days.append(days)
    if not previous_days:
        return None
    median_days = statistics.median(previous_days)
    if median_days < 2:
        # Granularidad mensual (una foto por mes), no un mes en curso a medias.
        return None

    days_in_month = calendar.monthrange(today.year, today.month)[1]
    if today.day / days_in_month >= 0.9:
        return None
    if distinct_days >= 0.7 * median_days:
        return None
    return normalize_period(str(last_row.get("periodo")))


def _prepare_period_rows(
    rows: List[Dict[str, Any]],
    include_partial_period: bool = False,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Descarta periodos futuros y, salvo pedido explicito, el mes en curso incompleto.

    El futuro no es historia: un periodo con fecha posterior al mes actual no
    puede entrar al backtest. El mes en curso, si esta a medias, hunde el ultimo
    punto y el backtest lo lee como una caida que no ocurrio; se excluye por
    defecto y se declara en `partial_period_excluded`. La serie que viaja al
    usuario es la misma que alimento el motor, no la cruda.
    """
    current = today or utcnow_naive().date()
    current_month = current.strftime("%Y-%m")

    kept: List[Dict[str, Any]] = []
    future: List[str] = []
    for row in rows:
        period = normalize_period(str(row.get("periodo")))
        if period is not None and period > current_month:
            future.append(period)
            continue
        kept.append(row)

    partial: Optional[str] = None
    if kept and not include_partial_period:
        last_period = normalize_period(str(kept[-1].get("periodo")))
        if last_period == current_month and _partial_period(kept[-1], kept[:-1], current):
            partial = last_period
            kept = kept[:-1]

    return {
        "rows": kept,
        "partial_period_excluded": partial,
        "future_periods_excluded": future,
    }


def _group_values(
    rows: List[Dict[str, Any]], kept_periods: set[str]
) -> Tuple[List[Any], bool]:
    """Valores de un grupo alineados al eje historico que uso el total.

    Un periodo que el grupo no tiene es un hueco, no un cero: sumar cero seria
    afirmar que ese grupo no vendio nada ese mes. Un periodo no normalizable
    tambien deja el eje sin verificar.
    """
    values: List[Any] = []
    normalized: List[str] = []
    unreadable = False
    for row in rows:
        raw = row.get("periodo")
        if raw is None:
            unreadable = True
            continue
        period = normalize_period(str(raw))
        if period is None:
            unreadable = True
            continue
        if period not in kept_periods:
            continue
        values.append(row.get("valor"))
        normalized.append(period)
    has_gaps = unreadable or _has_gaps(normalized) or set(normalized) != kept_periods
    return values, has_gaps


def _build_breakdown(
    db: Session,
    connection: CorporateConnection,
    table: str,
    cols: List[Dict[str, Any]],
    metric_column: str,
    date_column: str,
    dimension_column: str,
    horizon: int,
    kept_periods: set[str],
    user_role: str,
    is_admin: bool,
    role_id: Optional[int],
    connection_id: int,
    budget: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Top de grupos proyectado por separado; un fallo no tumba la principal."""
    try:
        totals_sql = build_group_totals_sql(
            table, date_column, metric_column, dimension_column,
            dialect="postgres", top_n=BREAKDOWN_TOP_N,
        )
        group_rows, _ = _run_guarded_sql(
            db, connection, totals_sql, "postgres", user_role, is_admin,
            role_id, connection_id, table, cols, budget,
        )
    except (ForecastUnavailable, ASTValidationError) as exc:
        logger.warning("Desglose ML no disponible: %s", exc)
        return {
            "available": False,
            "reason": str(exc),
            "dimension_column": dimension_column,
            "top_n": BREAKDOWN_TOP_N,
            "groups": [],
        }

    groups: List[Dict[str, Any]] = []
    for group_row in group_rows[:BREAKDOWN_TOP_N]:
        raw_value = group_row.get("grupo")
        if raw_value is None:
            continue
        value = str(raw_value)
        try:
            series_sql = build_group_series_sql(
                table, date_column, metric_column, dimension_column,
                value, dialect="postgres",
            )
            rows, _ = _run_guarded_sql(
                db, connection, series_sql, "postgres", user_role, is_admin,
                role_id, connection_id, table, cols, budget,
            )
            values, has_gaps = _group_values(rows, kept_periods)
            projection = build_projection(values, horizon, has_gaps=has_gaps)
            groups.append({
                "value": value,
                "available": projection["available"],
                "reason": projection["reason"],
                "method": projection["method"],
                "mape": projection["mape"],
                "n_periods": projection["n_periods"],
                "has_gaps": has_gaps,
                "total": projection["total"],
            })
        except (ForecastUnavailable, ASTValidationError) as exc:
            # El grupo queda declarado como no disponible; los demas y la
            # proyeccion principal siguen. No se inventa un total para el.
            groups.append({
                "value": value,
                "available": False,
                "reason": str(exc),
                "method": None,
                "mape": None,
                "n_periods": 0,
                "has_gaps": True,
                "total": None,
            })

    return {
        "available": True,
        "reason": None,
        "dimension_column": dimension_column,
        "top_n": BREAKDOWN_TOP_N,
        "groups": groups,
    }


def run_projection(
    db: Session,
    connection_id: int,
    table: str,
    cols: List[Dict[str, Any]],
    metric_column: Optional[str],
    date_column: Optional[str],
    horizon: int,
    user_role: str,
    is_admin: bool,
    role_id: Optional[int],
    budget: Optional[Dict[str, Any]] = None,
    candidate_cols: Optional[List[Dict[str, Any]]] = None,
    include_partial_period: bool = False,
    dimension_column: Optional[str] = None,
) -> Dict[str, Any]:
    """Serie real + proyeccion + metadatos. Sincrono: el router lo corre en un hilo.

    El SQL lo arma `build_series_sql` y pasa por `_run_guarded_sql`, que es el
    UNICO gate de ejecucion (ASTValidator + SQLExecutor + enmascarado). La lista
    de candidatas (`candidate_cols`) es la que ya filtro `load_fact_context`:
    aca se comprueba, como defensa en profundidad, que la eleccion no sea una
    columna que quedo fuera; un nombre colado no llega al SQL.

    El SQL pide diagnostico por periodo (`MAX(fecha)`, `COUNT(DISTINCT fecha)`)
    para poder detectar un mes en curso incompleto y periodos futuros. Esas
    columnas de diagnostico no se publican en `series`: la serie que viaja es
    `{periodo, valor}`, la misma que alimento el motor.

    Si `build_projection` devuelve `available=False`, la serie y el SQL YA
    construidos viajan igual: es lo que permite mostrar "la serie esta, no alcanza
    para proyectar" con el motivo, en vez de un payload vacio que se lee como una
    falla del sistema.
    """
    connection = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not connection:
        raise ForecastUnavailable(f"No existe la conexion {connection_id}.")

    # Los motivos vienen de `forecast_service` (misma fuente que `run_forecast`):
    # nombran la tabla y listan las columnas revisadas. Un "no hay datos" sin las
    # columnas obliga a adivinar cual falta. Se listan SOLO las candidatas: un
    # nombre BLOCKED/MASKED no tiene por que aparecer en la respuesta.
    allowed = candidate_cols if candidate_cols is not None else cols
    allowed_names = {str(c.get("name", "")).lower() for c in allowed if c.get("name")}
    if not date_column or str(date_column).lower() not in allowed_names:
        raise ForecastUnavailable(_motivo_sin_eje_temporal(table, allowed))
    if not metric_column or str(metric_column).lower() not in allowed_names:
        raise ForecastUnavailable(_motivo_sin_metrica(table, allowed))

    dialect = "postgres"
    sql = build_series_sql(
        table, date_column, metric_column, dialect, diagnostic_periods=True
    )
    try:
        rows, secured = _run_guarded_sql(
            db, connection, sql, dialect, user_role, is_admin, role_id, connection_id, table, cols, budget
        )
    except ASTValidationError as exc:
        # El SQL que el validador rechazo viaja CON la excepcion: el router lo
        # publica y lo audita ("que se intento leer"), que es el unico registro
        # de la consulta que la gobernanza nego. No se reenvuelve como
        # `ForecastUnavailable`: el router mapea las dos al mismo `available=False`.
        exc.attempted_sql = sql
        raise

    prepared = _prepare_period_rows(
        rows, include_partial_period=include_partial_period
    )
    rows = prepared["rows"]
    values = [row.get("valor") for row in rows]
    # `str()` sobre el periodo crudo a proposito: `normalize_period` acepta texto
    # y tiene que ser el unico lugar donde se decide que es un periodo valido.
    normalized = [normalize_period(str(row.get("periodo"))) for row in rows]
    has_gaps = _has_gaps(normalized)
    projection = build_projection(values, horizon, has_gaps=has_gaps)

    breakdown: Optional[Dict[str, Any]] = None
    if dimension_column:
        by_lower = {str(c.get("name", "")).lower(): c for c in allowed if c.get("name")}
        dim_col = by_lower.get(str(dimension_column).lower())
        if dim_col is None or not _is_dimension_col(dim_col):
            breakdown = {
                "available": False,
                "reason": (
                    f"La columna '{dimension_column}' no es una dimension de texto "
                    f"permitida en la tabla '{table}'."
                ),
                "dimension_column": None,
                "top_n": BREAKDOWN_TOP_N,
                "groups": [],
            }
        elif not projection["available"]:
            breakdown = {
                "available": False,
                "reason": "La proyeccion principal no esta disponible; no se desglosa.",
                "dimension_column": str(dim_col.get("name")),
                "top_n": BREAKDOWN_TOP_N,
                "groups": [],
            }
        else:
            kept_periods = {period for period in normalized if period}
            breakdown = _build_breakdown(
                db, connection, table, cols, metric_column, date_column,
                str(dim_col.get("name")), horizon, kept_periods,
                user_role, is_admin, role_id, connection_id, budget,
            )

    return {
        "available": projection["available"],
        "reason": projection["reason"],
        "horizon": horizon,
        "steps": _with_periods(projection["steps"], normalized),
        "total": projection["total"],
        "breakdown": breakdown,
        "method": projection["method"],
        "beats_naive": projection["beats_naive"],
        "mape": projection["mape"],
        "mape_by_method": projection["mape_by_method"],
        "n_periods": projection["n_periods"],
        "has_gaps": has_gaps,
        "partial_period_excluded": prepared["partial_period_excluded"],
        "future_periods_excluded": prepared["future_periods_excluded"],
        "table": table,
        "metric_column": metric_column,
        "date_column": date_column,
        "income_only": True,  # La serie publicada es de ingresos (`> 0`), no de neto.
        "series": [
            {"periodo": row.get("periodo"), "valor": row.get("valor")}
            for row in rows
        ],
        "sql": secured,
    }


# ----------------------------------------------------------------------------
# Historial y verificacion contra el dato actual
# ----------------------------------------------------------------------------

def parse_projection_snapshot(raw: Optional[str]) -> Optional[ProjectionResponse]:
    """Snapshot de auditoria a contrato de proyeccion, o None si no es legible.

    Un snapshot viejo o de otra forma no se "repara": se declara no verificable.
    """
    if not raw:
        return None
    try:
        return ProjectionResponse.model_validate_json(raw)
    except Exception:
        return None


def _number(value: Any) -> Optional[float]:
    """Numero finito o None. Un booleano no es una medicion."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _error_pct(actual: Optional[float], projected: Optional[float]) -> Optional[float]:
    if actual is None or projected is None or actual == 0:
        return None
    return abs(projected - actual) / abs(actual) * 100.0


def _inside_band(
    actual: Optional[float], lower: Optional[float], upper: Optional[float]
) -> Optional[bool]:
    if actual is None or lower is None or upper is None:
        return None
    return lower <= actual <= upper


def list_projection_history(
    db: Session, user_id: int, limit: int = 10
) -> List[Dict[str, Any]]:
    """Ultimas proyecciones auditadas del usuario. Nunca las de otro usuario."""
    limit = max(1, min(int(limit), 20))
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id == user_id,
            AuditLog.validation_status == "PROYECCION_ML",
        )
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    items: List[Dict[str, Any]] = []
    for log in logs:
        snapshot = parse_projection_snapshot(log.result_snapshot)
        if snapshot is None:
            items.append({
                "audit_log_id": log.id,
                "timestamp": log.timestamp or utcnow_naive(),
                "question": log.question_prompt,
                "horizon": DEFAULT_HORIZON,
                "verifiable": False,
                "reason": "El registro de auditoria no tiene un snapshot de proyeccion legible.",
            })
            continue

        reason = None
        if snapshot.connection_id is None:
            reason = "La proyeccion no guardo la conexion: no se puede releer el dato actual."
        elif not (snapshot.table and snapshot.metric_column and snapshot.date_column):
            reason = "La proyeccion no guardo tabla, metrica y eje temporal."
        elif not snapshot.steps:
            reason = "La proyeccion no publico pasos para verificar."
        items.append({
            "audit_log_id": log.id,
            "timestamp": log.timestamp or utcnow_naive(),
            "question": snapshot.question or log.question_prompt,
            "horizon": snapshot.horizon,
            "table": snapshot.table,
            "metric_column": snapshot.metric_column,
            "date_column": snapshot.date_column,
            "connection_id": snapshot.connection_id,
            "total_point": snapshot.total.point if snapshot.total else None,
            "verifiable": reason is None,
            "reason": reason,
        })
    return items


def verify_projection(
    db: Session,
    audit_log_id: int,
    user_id: int,
    user_role: str,
    is_admin: bool,
    role_id: Optional[int],
    budget: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Compara una proyeccion auditada contra el dato ACTUAL de periodos cerrados.

    Solo se leen periodos que ya cerraron (`periodo < mes actual`). El futuro no
    se compara y el total real solo se calcula si TODOS los pasos tienen dato:
    sumar un subconjunto y llamarlo "total real" seria otra afirmacion inventada.
    """
    log = (
        db.query(AuditLog)
        .filter(
            AuditLog.id == audit_log_id,
            AuditLog.user_id == user_id,
            AuditLog.validation_status == "PROYECCION_ML",
        )
        .first()
    )
    if log is None:
        return None

    base: Dict[str, Any] = {
        "available": True,
        "reason": None,
        "audit_log_id": log.id,
        "question": log.question_prompt,
        "table": None,
        "metric_column": None,
        "date_column": None,
        "horizon": DEFAULT_HORIZON,
        "method": None,
        "steps": [],
        "total": None,
        "measured_steps": 0,
        "pending_steps": 0,
        "missing_steps": 0,
        "inside_band_steps": 0,
        "outside_band_steps": 0,
        "data_note": None,
        "sql": None,
        "rows_returned": 0,
    }
    snapshot = parse_projection_snapshot(log.result_snapshot)
    if snapshot is None:
        return {
            **base,
            "available": False,
            "reason": "El registro de auditoria no tiene un snapshot de proyeccion legible.",
        }

    base.update({
        "question": snapshot.question or log.question_prompt,
        "table": snapshot.table,
        "metric_column": snapshot.metric_column,
        "date_column": snapshot.date_column,
        "horizon": snapshot.horizon,
        "method": snapshot.method,
    })
    if snapshot.connection_id is None:
        return {
            **base,
            "available": False,
            "reason": "La proyeccion no guardo la conexion: no se puede releer el dato actual.",
        }
    if not (snapshot.table and snapshot.metric_column and snapshot.date_column):
        return {
            **base,
            "available": False,
            "reason": "La proyeccion no guardo tabla, metrica y eje temporal.",
        }
    if not snapshot.steps:
        return {
            **base,
            "available": False,
            "reason": "La proyeccion no publico pasos para verificar.",
        }

    try:
        context = load_fact_context(
            db, snapshot.connection_id, user_role, is_admin, role_id, budget
        )
    except (ForecastUnavailable, ASTValidationError) as exc:
        return {**base, "available": False, "reason": str(exc)}

    if context["table"] != snapshot.table:
        return {
            **base,
            "available": False,
            "reason": (
                "La tabla autorizada de la conexion cambio desde que se guardo la "
                "proyeccion; la verificacion falla cerrada."
            ),
        }

    by_lower = {
        str(c.get("name", "")).lower(): c
        for c in context["candidate_cols"] if c.get("name")
    }
    metric_col = by_lower.get(str(snapshot.metric_column).lower())
    date_col = by_lower.get(str(snapshot.date_column).lower())
    if (
        metric_col is None or not _is_num_col(metric_col) or not looks_like_metric(metric_col)
        or date_col is None or not _is_date_col(date_col)
    ):
        return {
            **base,
            "available": False,
            "reason": (
                "La metrica o el eje de la proyeccion ya no estan permitidos para "
                "este rol; no se relee el dato actual."
            ),
        }

    current_month = utcnow_naive().date().strftime("%Y-%m")
    closed_steps = [
        step for step in snapshot.steps
        if step.period and normalize_period(step.period) is not None
        and normalize_period(step.period) < current_month
    ]
    actual_by_period: Dict[str, float] = {}
    secured_sql: Optional[str] = None
    if closed_steps:
        actual_sql = build_series_sql(
            snapshot.table, str(date_col["name"]), str(metric_col["name"]),
            "postgres", diagnostic_periods=False,
        )
        try:
            actual_rows, secured_sql = _run_guarded_sql(
                db, db.query(CorporateConnection).filter(
                    CorporateConnection.id == snapshot.connection_id
                ).first(),
                actual_sql, "postgres", user_role, is_admin, role_id,
                snapshot.connection_id, snapshot.table, context["cols"], budget,
            )
        except (ForecastUnavailable, ASTValidationError) as exc:
            attempted = getattr(exc, "attempted_sql", None) or actual_sql
            return {**base, "available": False, "reason": str(exc), "sql": attempted}
        for row in actual_rows:
            period = normalize_period(str(row.get("periodo")))
            value = _number(row.get("valor"))
            if period is not None and value is not None:
                actual_by_period[period] = value

    statuses: List[Dict[str, Any]] = []
    for step in snapshot.steps:
        period = normalize_period(step.period) if step.period else None
        projected = _number(step.point)
        if period is None:
            statuses.append({
                "step": step.step, "period": step.period, "projected": projected,
                "actual": None, "error_pct": None, "inside_band": None,
                "status": "sin_periodo",
            })
        elif period >= current_month:
            statuses.append({
                "step": step.step, "period": period, "projected": projected,
                "actual": None, "error_pct": None, "inside_band": None,
                "status": "pendiente",
            })
        elif period not in actual_by_period:
            statuses.append({
                "step": step.step, "period": period, "projected": projected,
                "actual": None, "error_pct": None, "inside_band": None,
                "status": "sin_dato",
            })
        else:
            actual = actual_by_period[period]
            statuses.append({
                "step": step.step, "period": period, "projected": projected,
                "actual": actual,
                "error_pct": _error_pct(actual, projected),
                "inside_band": _inside_band(actual, _number(step.lower), _number(step.upper)),
                "status": "medido",
            })

    measured = sum(1 for item in statuses if item["status"] == "medido")
    pending = sum(1 for item in statuses if item["status"] == "pendiente")
    missing = sum(1 for item in statuses if item["status"] in {"sin_dato", "sin_periodo"})
    inside = sum(
        1 for item in statuses
        if item["status"] == "medido" and item["inside_band"] is True
    )
    outside = sum(
        1 for item in statuses
        if item["status"] == "medido" and item["inside_band"] is False
    )
    all_measured = measured == len(statuses)
    total_projected = _number(snapshot.total.point) if snapshot.total else None
    total_lower = _number(snapshot.total.lower) if snapshot.total else None
    total_upper = _number(snapshot.total.upper) if snapshot.total else None
    total_actual = (
        sum(item["actual"] for item in statuses)
        if all_measured and all(item["actual"] is not None for item in statuses)
        else None
    )
    total_status = "medido" if all_measured and total_projected is not None else "incompleto"

    return {
        **base,
        "available": True,
        "steps": statuses,
        "total": {
            "projected": total_projected,
            "actual": total_actual,
            "error_pct": (
                _error_pct(total_actual, total_projected)
                if total_status == "medido" else None
            ),
            "inside_band": (
                _inside_band(total_actual, total_lower, total_upper)
                if total_status == "medido" else None
            ),
            "status": total_status,
        },
        "measured_steps": measured,
        "pending_steps": pending,
        "missing_steps": missing,
        "inside_band_steps": inside,
        "outside_band_steps": outside,
        "data_note": (
            "La comparacion usa el dato actual de la tabla autorizada para los "
            "periodos ya cerrados. No modifica la proyeccion auditada."
        ),
        "sql": secured_sql,
        "rows_returned": len(actual_by_period),
    }


# ----------------------------------------------------------------------------
# Narrativa (LLM con contrato + plantilla determinista)
# ----------------------------------------------------------------------------

def _listar_pasos(steps: Sequence[Any]) -> str:
    return ", ".join(str(s.get("step")) for s in steps if isinstance(s, dict) and s.get("step") is not None)


def _template_narration(payload: Dict[str, Any]) -> str:
    """Narrativa determinista, hecha en codigo y SIN LLM.

    Dice los mismos hechos que el LLM tiene permitido citar —metodo publicado, si
    le gano al naive y que se probaron los tres candidatos, periodos, MAPE,
    huecos y pasos sin evidencia—, asi que las dos narraciones son comparables
    entre si. Existe porque la lectura no es el dato: si el modelo local no
    responde, la respuesta se queda sin prosa pero nunca sin la lectura.
    """
    method = payload.get("method") or "naive"
    probados = ", ".join(METHODS)
    partes: List[str] = []

    if payload.get("beats_naive"):
        partes.append(
            f"Se probaron los tres metodos ({probados}) y el publicado es '{method}': le gano "
            "estrictamente al naive en el backtest walk-forward."
        )
    else:
        partes.append(
            f"Se probaron los tres metodos ({probados}) y ninguno le gano estrictamente al naive "
            f"(el valor del ultimo periodo): el publicado es '{method}', el mas simple de los que empatan."
        )

    n_periodos = payload.get("n_periods") or 0
    mape = payload.get("mape")
    if mape is None:
        partes.append(f"El backtest camino sobre {n_periodos} periodos de historia y no dejo un MAPE medible.")
    else:
        partes.append(
            f"El backtest camino sobre {n_periodos} periodos de historia con un MAPE de {mape}% "
            "en el metodo publicado."
        )

    if payload.get("has_gaps"):
        partes.append(
            "La serie tiene huecos: falta al menos un mes entre el primero y el ultimo periodo, "
            "asi que el eje no es regular."
        )

    steps = payload.get("steps") or []
    total = payload.get("total")
    if isinstance(total, dict) and total.get("point") is not None:
        total_txt = f"{float(total['point']):,.0f}".replace(",", ".")
        if total.get("extrapolated"):
            medidos = total.get("measured_steps")
            unidad = "paso" if medidos == 1 else "pasos"
            partes.append(
                f"El total de los {len(steps)} pasos es {total_txt}; su banda se hereda del total "
                f"de {medidos} {unidad}, asi que no es una medicion del horizonte completo."
            )
        elif total.get("lower") is None:
            partes.append(
                f"El total de los {len(steps)} pasos es {total_txt}, sin banda medida: "
                "no hubo un prefijo completo que medir."
            )
        else:
            partes.append(
                f"El total de los {len(steps)} pasos es {total_txt}, con banda medida sobre el horizonte completo."
            )
    extrapolados = [s for s in steps if isinstance(s, dict) and s.get("extrapolated")]
    flojos = [
        s for s in steps
        if isinstance(s, dict) and not s.get("extrapolated") and not s.get("reliable")
    ]
    if extrapolados:
        partes.append(
            f"Los pasos {_listar_pasos(extrapolados)} no tienen backtest propio: heredan la banda "
            "del ultimo paso medido, que no es una medicion de esos pasos."
        )
    if flojos:
        partes.append(
            f"Los pasos {_listar_pasos(flojos)} tienen menos de {MIN_BACKTESTS_RELIABLE} backtests: "
            "la banda de esos pasos es evidencia escasa y no se marca como confiable."
        )

    partes.append(
        "La banda publicada sale del error que la serie ya mostro; no es una promesa de precision futura."
    )
    return " ".join(partes)


def _narration_prompt(payload: Dict[str, Any]) -> str:
    """Prompt de la narrativa, SIN la serie ni el SQL.

    `series` es el historico completo (una fila por periodo) y `sql` es texto: los
    dos inflan el prompt sin agregar nada a la redaccion, porque los numeros que
    la narrativa puede citar son los ya calculados (punto, banda, MAPE). El resto
    de los hechos —incluidos los huecos y la evidencia por paso— viaja entero.
    """
    visible = {key: value for key, value in payload.items() if key not in ("series", "sql")}
    body = json.dumps(visible, ensure_ascii=False, default=str)
    return (
        "Numeros ya calculados (JSON):\n"
        f"{body}\n\n"
        "En cada paso, `mape` es el error medido de ESE paso (null = ese paso no "
        "tiene backtest propio) y `extrapolated` marca banda heredada del ultimo "
        "paso medido, que no es una medicion de ese paso; `n_backtests` cuenta los "
        "backtests de ESE paso, no la cantidad de datos historicos. Un MAPE mas "
        "bajo es mejor: el metodo publicado supera al naive solo si su MAPE es "
        "menor, y `beats_naive: true` es ese veredicto ya calculado. El campo "
        "`total` es la suma de los puntos publicados; si `extrapolated` es true, "
        "su banda se hereda de `measured_steps` pasos y NO es una medicion del "
        "horizonte completo.\n\n"
        "Redacta la lectura de esta proyeccion para el usuario, con su limitacion de "
        "confianza explicita."
    )


async def narrate(payload: Dict[str, Any]) -> Tuple[str, str]:
    """(narrativa, "llm"|"plantilla"). UNA llamada con los numeros YA calculados.

    El fallback es determinista y en espanol, hecho en codigo: una respuesta sin
    lectura es peor que una lectura mecanica, pero muchisimo mejor que un texto
    vacio o que un numero inventado por un modelo que no respondio.
    """
    plantilla = _template_narration(payload)
    try:
        text = await LLMService.generate_completion(
            _narration_prompt(payload),
            system_prompt=_NARRATION_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=_NARRATION_MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning("Narracion por LLM no disponible (%s); se publica la plantilla.", exc)
        return plantilla, "plantilla"

    text = (text or "").strip()
    if not text:
        return plantilla, "plantilla"
    return text, "llm"


# ----------------------------------------------------------------------------
# Estimacion de duracion
# ----------------------------------------------------------------------------

def _measured_tokens_per_second() -> Optional[float]:
    """Velocidad YA medida por el asistente de enriquecimiento, o None.

    JAMAS mide: `measure_tokens_per_second` es un probe real al LLM (segundos de
    inferencia) y esta estimacion responde mientras se abre el panel. Lo unico
    que se lee es la cache que dejo ese wizard, con su TTL de una hora.

    Se busca primero la clave `("", "")` porque es la que escribe el sitio de
    medicion cuando el wizard se abre sin query params, que es el caso normal.
    Despues la de `settings` (una instalacion con la config fija del `.env`),
    solo si difiere: es la misma medicion que alguien corrio con esos valores.
    """
    cache = enrich_estimator._SPEED_CACHE
    keys: List[Tuple[str, str]] = [("", "")]
    settings_key = (settings.OLLAMA_BASE_URL or "", settings.OLLAMA_MODEL or "")
    if settings_key != ("", ""):
        keys.append(settings_key)

    now = time.time()
    for key in keys:
        entry = cache.get(key)
        if not entry:
            continue
        tps, measured_at = float(entry[0]), float(entry[1])
        if now - measured_at >= enrich_estimator._SPEED_CACHE_TTL_S:
            continue  # Medicion vencida: la velocidad de un hardware cambia.
        if tps > 0:
            return tps
    return None


def estimate_seconds(question: str, horizon: Optional[int] = None) -> Dict[str, Any]:
    """{"estimated_seconds", "source", "horizon_used"} — instantaneo.

    Sin LLM y sin BD: solo la cache de velocidad del wizard. Con medicion, la
    duracion sale de los tokens de trabajo (dos llamadas cortas mas el prompt)
    sobre esa velocidad, con piso de `_MEASURED_FLOOR_S`; sin medicion, se
    declara `fallback` con una espera conservadora en vez de un numero inventado
    con formato de medicion.

    `horizon=None` es Auto: el LLM todavia no respondio, asi que se asume el
    horizonte mas largo de la lista (12) para no subestimar la espera. El
    horizonte asumido viaja en `horizon_used`.
    """
    assumed = horizon if horizon is not None else max(SUPPORTED_HORIZONS)
    tps = _measured_tokens_per_second()
    if tps is None:
        return {
            "estimated_seconds": _FALLBACK_SECONDS,
            "source": "fallback",
            "horizon_used": assumed,
        }

    prompt_tokens = (
        _PROMPT_BASE_TOKENS
        + len(question or "") / 4.0  # chars/4: aproximacion gruesa a tokens, igual que el wizard.
        + _PROMPT_TOKENS_PER_STEP * max(assumed, 0)
    )
    output_tokens = _ANALYSIS_OUTPUT_TOKENS + _NARRATION_OUTPUT_TOKENS
    seconds = (
        _LOCAL_OVERHEAD_S
        + output_tokens / tps
        + prompt_tokens / (tps * _PROMPT_SPEED_FACTOR)
    )
    return {
        "estimated_seconds": round(max(seconds, _MEASURED_FLOOR_S), 1),
        "source": "medido",
        "horizon_used": assumed,
    }
