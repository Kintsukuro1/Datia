"""Proyecciones: contexto de la fact table, analisis por LLM y narrativa.

Que aporta este modulo sobre lo que ya existe
--------------------------------------------
La serie la arma `forecast_service` (misma fact table autorizada, mismo
`ASTValidator`, mismo `SQLExecutor`, mismo enmascarado) y la proyeccion la
calcula `ml_models` (cuatro metodos, backtest walk-forward, banda por paso).
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

import json
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.catalog.services import enrich_estimator
from app.modules.chat_engine.ast_validator import ASTValidationError
from app.modules.chat_engine.forecast_calculator import (
    next_period,
    normalize_period,
    period_distance,
)
from app.modules.chat_engine.forecast_service import (
    ForecastUnavailable,
    _is_date_col,
    _is_num_col,
    # Los helpers privados se importan A PROPOSITO, no se copian: la fact table
    # tiene que ser la MISMA que la del forecast y del chat. Dos implementaciones
    # divergen en el primer arreglo y el mismo rol termina leyendo otra tabla
    # segun por que endpoint entro.
    _load_fact_table,
    _motivo_sin_eje_temporal,
    _motivo_sin_metrica,
    _run_guarded_sql,
    build_series_sql,
    resolve_columns,
)
from app.modules.chat_engine.llm_service import LLMService
from app.modules.ml_forecast.ml_models import METHODS, MIN_BACKTESTS_RELIABLE, build_projection
from app.modules.ml_forecast.schemas import SUPPORTED_HORIZONS

# Techo de columnas en el prompt del analisis. Cada linea son ~12 tokens, asi que
# 120 columnas son ~1.500 tokens de prompt: con mas, el costo por request crece
# sin agregar informacion (una fact table con mas de 120 columnas es el caso
# raro). `ponytail:` lo que queda afuera de la lista NO queda afuera de la
# eleccion: el fallback heuristico ve todas las columnas de `cols`, y un nombre
# que el modelo no vio simplemente no lo va a proponer.
_MAX_PROMPT_COLUMNS = 120

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
# SQL + backtest walk-forward (que ajusta Holt por origen) + banda por paso.
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
    """Fact table y columnas que el rol PUEDE ver, con la heuristica de columnas.

    No decide la metrica final: eso es `analyze_question`. Aca solo se resuelve
    el contexto contra el que esa decision se valida (la lista cerrada de
    nombres reales) y se deja el fallback heuristico ya calculado.

    `budget` es el cache de ESTA request (lo crea el router y lo comparten el
    conteo de tablas y el guardarrail), no un global mutable: lo cacheado son los
    permisos de UN usuario.
    """
    connection = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
    if not connection:
        raise ForecastUnavailable(f"No existe la conexion {connection_id}.")

    dialect = "postgres"
    table, cols = _load_fact_table(
        db, connection, is_admin, user_role, role_id, connection_id, dialect, budget
    )
    return {"table": table, "cols": cols, "heuristic": resolve_columns(cols)}


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


async def analyze_question(
    question: str,
    table: str,
    cols: List[Dict[str, Any]],
    heuristic: Dict[str, Any],
    horizon: int,
) -> Dict[str, Any]:
    """Metrica, fecha y horizonte para proyectar: los del LLM si son validos, los heuristicos si no.

    Cualquier violacion —nombre inventado, tipo equivocado, JSON roto, respuesta
    vacia o excepcion/timeout del LLM— cae al fallback heuristico con
    `source="heuristica"`. El horizonte es la excepcion: si el modelo lo extrajo
    bien ({3, 6, 12}), se conserva aunque las columnas hayan caido a la
    heuristica. El fallback puede no tener metrica ni fecha: devuelve None igual
    y `run_projection` dira el motivo nombrando la tabla y las columnas
    revisadas (el mismo mensaje honesto que el forecast del chat), en vez de
    inventar un nombre para poder responder algo.
    """
    fallback = {
        "table": table,
        "metric_column": heuristic.get("value_col"),
        "date_column": heuristic.get("date_col"),
        "horizon": horizon,
        "source": "heuristica",
    }

    try:
        raw = await _ask_llm_for_columns(question, cols, horizon)
    except Exception as exc:
        # El LLM local apagado o lento es el caso NORMAL, no el excepcional: la
        # proyeccion no depende de el para existir.
        logger.info("Analisis de columnas por LLM no disponible (%s); se usa la heuristica.", exc)
        return fallback

    choice = _validate_llm_choice(raw, cols)
    if choice is None:
        logger.info("El LLM no devolvio un objeto utilizable; se usa la heuristica.")
        return fallback

    if not (choice["metric_column"] and choice["date_column"]):
        # Un nombre invalido tumba las DOS columnas (no se mezcla media eleccion
        # del LLM con media heuristica), pero el horizonte sobrevive: es la
        # unica parte de la respuesta que no depende de los nombres.
        logger.info(
            "El LLM eligio columnas no utilizables (metrica=%r, fecha=%r); columnas por heuristica.",
            choice["metric_column"], choice["date_column"],
        )
        fallback["horizon"] = choice["horizon"] or horizon
        return fallback

    return {
        "table": table,
        "metric_column": choice["metric_column"],
        "date_column": choice["date_column"],
        # Horizonte del LLM solo si esta en {3, 6, 12}; si no, el del pedido.
        "horizon": choice["horizon"] or horizon,
        "source": "llm",
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
) -> Dict[str, Any]:
    """Serie real + proyeccion + metadatos. Sincrono: el router lo corre en un hilo.

    El SQL lo arma `build_series_sql` y pasa por `_run_guarded_sql`, que es el
    UNICO gate de columnas (ASTValidator + SQLExecutor + enmascarado). No hay
    chequeo propio de columnas bloqueadas: un segundo gate podria divergir del
    primero y dejar pasar lo que el validador corta.

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
    # columnas obliga a adivinar cual falta.
    if not date_column:
        raise ForecastUnavailable(_motivo_sin_eje_temporal(table, cols))
    if not metric_column:
        raise ForecastUnavailable(_motivo_sin_metrica(table, cols))

    dialect = "postgres"
    sql = build_series_sql(table, date_column, metric_column, dialect)
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

    values = [row.get("valor") for row in rows]
    # `str()` sobre el periodo crudo a proposito: `normalize_period` acepta texto
    # y tiene que ser el unico lugar donde se decide que es un periodo valido.
    normalized = [normalize_period(str(row.get("periodo"))) for row in rows]
    projection = build_projection(values, horizon)

    return {
        "available": projection["available"],
        "reason": projection["reason"],
        "horizon": horizon,
        "steps": _with_periods(projection["steps"], normalized),
        "method": projection["method"],
        "beats_naive": projection["beats_naive"],
        "mape": projection["mape"],
        "mape_by_method": projection["mape_by_method"],
        "n_periods": projection["n_periods"],
        "has_gaps": _has_gaps(normalized),
        "table": table,
        "metric_column": metric_column,
        "date_column": date_column,
        "income_only": True,  # La serie publicada es de ingresos (`> 0`), no de neto.
        "series": rows,
        "sql": secured,
    }


# ----------------------------------------------------------------------------
# Narrativa (LLM con contrato + plantilla determinista)
# ----------------------------------------------------------------------------

def _listar_pasos(steps: Sequence[Any]) -> str:
    return ", ".join(str(s.get("step")) for s in steps if isinstance(s, dict) and s.get("step") is not None)


def _template_narration(payload: Dict[str, Any]) -> str:
    """Narrativa determinista, hecha en codigo y SIN LLM.

    Dice los mismos hechos que el LLM tiene permitido citar —metodo publicado, si
    le gano al naive y que se probaron los cuatro candidatos, periodos, MAPE,
    huecos y pasos sin evidencia—, asi que las dos narraciones son comparables
    entre si. Existe porque la lectura no es el dato: si el modelo local no
    responde, la respuesta se queda sin prosa pero nunca sin la lectura.
    """
    method = payload.get("method") or "naive"
    probados = ", ".join(METHODS)
    partes: List[str] = []

    if payload.get("beats_naive"):
        partes.append(
            f"Se probaron los cuatro metodos ({probados}) y el publicado es '{method}': le gano "
            "estrictamente al naive en el backtest walk-forward."
        )
    else:
        partes.append(
            f"Se probaron los cuatro metodos ({probados}) y ninguno le gano estrictamente al naive "
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
        "menor, y `beats_naive: true` es ese veredicto ya calculado.\n\n"
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


def estimate_seconds(question: str, horizon: int) -> Dict[str, Any]:
    """{"estimated_seconds": float, "source": "medido"|"fallback"} — instantaneo.

    Sin LLM y sin BD: solo la cache de velocidad del wizard. Con medicion, la
    duracion sale de los tokens de trabajo (dos llamadas cortas mas el prompt)
    sobre esa velocidad, con piso de `_MEASURED_FLOOR_S`; sin medicion, se
    declara `fallback` con una espera conservadora en vez de un numero inventado
    con formato de medicion.
    """
    tps = _measured_tokens_per_second()
    if tps is None:
        return {"estimated_seconds": _FALLBACK_SECONDS, "source": "fallback"}

    prompt_tokens = (
        _PROMPT_BASE_TOKENS
        + len(question or "") / 4.0  # chars/4: aproximacion gruesa a tokens, igual que el wizard.
        + _PROMPT_TOKENS_PER_STEP * max(horizon, 0)
    )
    output_tokens = _ANALYSIS_OUTPUT_TOKENS + _NARRATION_OUTPUT_TOKENS
    seconds = (
        _LOCAL_OVERHEAD_S
        + output_tokens / tps
        + prompt_tokens / (tps * _PROMPT_SPEED_FACTOR)
    )
    return {"estimated_seconds": round(max(seconds, _MEASURED_FLOOR_S), 1), "source": "medido"}
