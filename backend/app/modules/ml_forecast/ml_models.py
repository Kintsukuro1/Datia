"""Proyecciones a varios pasos: tres metodos, el backtest elige uno y publica su banda.

Por que existe, si ya hay un forecast
-------------------------------------
`forecast_calculator.forecast_next_period` publica un unico metodo (naive, el
ultimo valor) sobre series de ~9 periodos, elegido A MANO porque gano el
backtest. Ese numero es honesto pero es uno solo: no hay forma de saber si una
tendencia clara habria errado menos, porque nunca se calculo. Este modulo las
calcula, mide el error de cada candidato caminando hacia adelante y publica el
de menor error medido. La incognita deja de ser una creencia.

Un cuarto candidato, Holt amortiguado (`statsmodels`), se retiro en el plan 004
(fase 4, INV-1): 222 MB de dependencias y 2,8 s de import para competir en un
backtest donde la doctrina es que gana el error medido. Si la evidencia lo
vuelve a pedir, se reintroduce como cualquier otro candidato.

Las dos reglas que sostienen el numero
--------------------------------------
1. Gana el error, no la sofisticacion. El error se mide con MASE (MAE escalado
   por el error naive in-sample) cuando existe escala, porque MAPE se dispara
   sobre valores chicos y no esta definido sobre cero; sin escala se cae a MAPE.
   MAE y RMSE se publican como contexto, y la seleccion se hace sobre los numeros
   ya redondeados que el lector ve. Si el candidato no le gana ESTRICTAMENTE al
   naive en la misma metrica, se publica naive: un modelo mas complejo que empata
   no es una mejora, es mas superficie donde fallar. `beats_naive` lo declara
   para que la UI pueda decir "se probo y no mejoro" en vez de mostrar un metodo
   nuevo sin contexto.
2. La banda es por PASO y sale de los residuos CON SIGNO de ESE paso: un
   intervalo central calibrado a una cobertura objetivo (cuantil empirico con
   correccion finita), no un unico peor caso. Con pocos residuos o un cambio de
   nivel reciente se cae al peor caso observado y se declara. Un paso sin
   backtest posible no inventa banda: hereda la del ultimo paso medido y sale
   marcado `extrapolated`, porque una banda heredada no es una medicion de ese
   paso. El total del horizonte usa la misma regla sobre los residuos
   ACUMULADOS, no sobre la suma de las bandas por paso. La cobertura que se
   publica es walk-forward: se mide con intervalos calculados solo con residuos
   anteriores, nunca con los mismos que eligieron el cuantil.

Sin DB, sin LLM, sin I/O: entra una serie de numeros y salen numeros. Todo lo
que no se puede calcular es None, nunca un 0 que el lector tomaria por un dato.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy

# Los helpers privados se importan A PROPOSITO, no se copian: la normalizacion
# numerica tiene que ser UNA sola implementacion. Dos copias divergen en el
# primer arreglo y la misma serie termina con dos numeros distintos segun por
# que camino se calculo. El unico bug que un forecast no puede tener es que el
# mismo dato de dos numeros.
from ..chat_engine.forecast_calculator import MIN_PERIODS, _as_number
from .formatting import pct_text
from .policy import POLICY

# Los umbrales viven en `policy.py`, que es la unica politica del modulo. Estos
# alias existen para no romper a los importadores historicos (`evaluate_quality`
# y `test_ml_quality`); no son una segunda fuente: cambia la politica y cambia
# el alias. `MIN_BACKTESTS_RELIABLE` es el piso de evidencia por paso: con 3
# residuos el maximo observado es demasiado sensible a un unico caso raro, o sea
# la banda mide el ruido de la serie y no su error. Se publica igual, con
# `reliable: False`.
MIN_BACKTESTS_RELIABLE = POLICY.reliable_backtests

# `> UNRELIABLE_MAPE` es no confiable; entre `EXPLORATORY_MAPE` y
# `UNRELIABLE_MAPE` es exploratoria. Son decisiones de producto, no hallazgos.
UNRELIABLE_MAPE = POLICY.unreliable_mape
EXPLORATORY_MAPE = POLICY.exploratory_mape

# Orden = desempate por simplicidad. Con dos MAPE iguales gana el primero, y el
# primero es el naive: entre un modelo simple y uno complejo que miden lo mismo,
# el simple tiene menos formas de romperse en produccion.
METHODS: Tuple[str, ...] = ("naive", "mean", "ols_trend")

# Etiquetas y mecanica de cada metodo. Son la UNICA fuente: el payload las
# publica y la UI/la narrativa las consumen; agregar un metodo sin describirlo
# falla en el registro, no en una frase suelta.
METHOD_LABELS: Dict[str, str] = {
    "naive": "Repetir el último valor",
    "mean": "Media histórica",
    "ols_trend": "Tendencia lineal",
}
METHOD_DESCRIPTIONS: Dict[str, str] = {
    "naive": "asume que el próximo valor será igual al último observado",
    "mean": "promedio simple de todos los períodos, sin ponderar",
    "ols_trend": "extiende la recta de tendencia ajustada sobre la historia",
}


# ----------------------------------------------------------------------------
# Utilidades de salida
# ----------------------------------------------------------------------------

def _finite(value: Any) -> Optional[float]:
    """`float` finito, o None. La salida publica nunca lleva NaN ni inf.

    Un NaN en el payload no es un numero raro: `json.dumps` lo emite como `NaN`
    crudo, que no es JSON valido, y el que lo lee se rompe al parsear. Todo lo
    que no sea finito se publica como None, que si es una afirmacion: "no se".
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _interval_bounds(
    point: Optional[float], lower_pct: Optional[float], upper_pct: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """(lower, upper) a partir de porcentajes con signo, o (None, None).

    Las puntas se ordenan con min/max: con un punto negativo los productos se
    invierten y un intervalo sin orden quedaria al reves. `_finite` evita
    publicar inf por una banda enorme sobre un monto enorme.
    """
    if point is None or lower_pct is None or upper_pct is None:
        return None, None
    first = point * (1.0 + lower_pct / 100.0)
    second = point * (1.0 + upper_pct / 100.0)
    lower, upper = _finite(min(first, second)), _finite(max(first, second))
    if lower is None or upper is None:
        return None, None
    return lower, upper


def _calibrated_interval(
    signed: Sequence[float], *, force_worst_case: bool = False
) -> Dict[str, Any]:
    """Intervalo central calibrado sobre residuos con signo.

    Con `n` residuos y cobertura objetivo `c`, usa un cuantil empirico con
    correccion finita (estilo conformal): los rangos se calculan sobre `n + 1`
    para que con pocos datos el intervalo no se angoste de mas. Si `n` no llega
    a `POLICY.interval_min_residuals`, o si hay un cambio de nivel, se usa el
    peor error observado y se declara `max_residual`.

    El intervalo SIEMPRE contiene el punto: si todos los errores fueron
    positivos, el limite inferior se colapsa al punto y el superior conserva el
    sesgo. No se inventa simetria donde los datos no la tienen.
    """
    n = len(signed)
    if not signed:
        return {
            "lower_pct": None,
            "upper_pct": None,
            "band_pct": None,
            "worst_case_pct": None,
            "band_method": "none",
        }

    worst_case = max(abs(value) for value in signed)
    if force_worst_case or n < POLICY.interval_min_residuals:
        return {
            "lower_pct": -worst_case,
            "upper_pct": worst_case,
            "band_pct": _round(worst_case, 1),
            "worst_case_pct": _round(worst_case, 1),
            "band_method": "max_residual",
        }

    ordered = sorted(signed)
    target = min(
        max(POLICY.interval_coverage, POLICY.interval_coverage_floor),
        POLICY.interval_coverage_ceiling,
    )
    alpha = 1.0 - target
    # Correccion finita: con n=8 y 80%, los extremos son 1 y 8; con n=20, 2 y 19.
    lower_rank = max(1, math.floor((n + 1) * alpha / 2.0))
    upper_rank = min(n, math.ceil((n + 1) * (1.0 - alpha / 2.0)))
    lower_pct = min(float(ordered[lower_rank - 1]), 0.0)
    upper_pct = max(float(ordered[upper_rank - 1]), 0.0)
    band_pct = max(abs(lower_pct), abs(upper_pct))
    return {
        "lower_pct": _round(lower_pct, 2),
        "upper_pct": _round(upper_pct, 2),
        "band_pct": _round(band_pct, 1),
        "worst_case_pct": _round(worst_case, 1),
        "band_method": "conformal_quantile",
    }


def _walk_forward_coverage(
    signed: Sequence[float], *, force_worst_case: bool = False
) -> Tuple[Optional[float], int]:
    """Cobertura real hacia adelante, sin usar el residuo que se esta midiendo.

    Para cada origen desde `interval_min_residuals`, calcula el intervalo con
    SOLO los residuos anteriores y comprueba si el siguiente cae dentro. Es la
    unica forma honesta de decir "cobertura observada": elegir el cuantil sobre
    todos los residuos y medir sobre esos mismos residuos siempre da el objetivo.
    """
    n = len(signed)
    if n <= POLICY.interval_min_residuals:
        return None, 0
    target = min(
        max(POLICY.interval_coverage, POLICY.interval_coverage_floor),
        POLICY.interval_coverage_ceiling,
    )
    alpha = 1.0 - target
    # Se recorre UNA vez manteniendo el prefijo ordenado por insercion. Ordenar
    # el prefijo en cada origen es O(n^2 log n) por paso: en una serie diaria de
    # varios anios eso convertia una respuesta en una espera.
    ordered: List[float] = []
    worst_case = 0.0
    inside = 0
    evaluated = 0
    for index, value in enumerate(signed):
        if index >= POLICY.interval_min_residuals:
            if force_worst_case:
                lower, upper = -worst_case, worst_case
            else:
                lower_rank = max(1, math.floor((index + 1) * alpha / 2.0))
                upper_rank = min(
                    index, math.ceil((index + 1) * (1.0 - alpha / 2.0))
                )
                lower = _round(min(ordered[lower_rank - 1], 0.0), 2)
                upper = _round(max(ordered[upper_rank - 1], 0.0), 2)
            evaluated += 1
            if lower <= value <= upper:
                inside += 1
        bisect.insort(ordered, value)
        worst_case = max(worst_case, abs(value))
    if evaluated == 0:
        return None, 0
    return _round(inside / evaluated, 3), evaluated


def _unavailable(reason: str, n_periods: int) -> Dict[str, Any]:
    """Payload de "no se puede proyectar", con TODAS las claves del contrato.

    Mismo esqueleto que el caso disponible a proposito: el que lee el payload no
    tiene que distinguir dos formas. `steps: []` es la verdad —no hay ningun paso
    que publicar— y no un hueco que el cliente tenga que interpretar. Las claves
    nuevas de metricas van con None (o lista vacia) porque tampoco hay medicion:
    `metrics_by_method` conserva los tres metodos para que el lector no tenga que
    distinguir "no hay metodos" de "no hubo error medible".
    """
    return {
        "available": False,
        "reason": reason,
        "method": None,
        "method_label": None,
        "method_description": None,
        "beats_naive": None,
        "mape": None,
        "mae": None,
        "rmse": None,
        "mase": None,
        "mape_by_method": {method: None for method in METHODS},
        "metrics_by_method": {
            method: {"mape": None, "mae": None, "rmse": None, "mase": None}
            for method in METHODS
        },
        "model_comparison": [],
        "n_periods": n_periods,
        "steps": [],
        "total": None,
        "interval_target": POLICY.interval_coverage,
    }


# ----------------------------------------------------------------------------
# Ajuste de cada metodo
# ----------------------------------------------------------------------------

def _fit_predict(method: str, history: Sequence[float], horizon: int) -> List[Optional[float]]:
    """Predicciones de los pasos 1..horizon ajustando con `history`. None donde el método no pudo."""
    # `float(v)` en el borde: `SUM()` de Postgres puede entregar `Decimal` y
    # numpy/statistics no operan igual con el. Normalizar aca hace que la misma
    # serie de `int`, de `float` y de `Decimal` de el MISMO numero.
    points = [float(v) for v in history]
    n = len(points)
    nothing: List[Optional[float]] = [None] * max(horizon, 0)

    if method == "naive":
        # El ultimo valor, repetido. Es el piso contra el que se mide todo lo
        # demas: si un metodo no le gana a esto, no aporta nada.
        return [_finite(points[-1])] * horizon if n else nothing

    if method == "mean":
        # La media de la historia: sin tendencia y sin memoria. Es el control.
        return [_finite(sum(points) / n)] * horizon if n else nothing

    if method == "ols_trend":
        # Minimos cuadrados sobre el INDICE (0, 1, 2...), no sobre el periodo:
        # con indices regulares la pendiente queda "por periodo", que es la
        # unidad en que se publica la proyeccion. Dos puntos son el minimo para
        # definir una recta; con uno, la pendiente seria una decision nuestra y
        # no un dato.
        if n < 2:
            return nothing
        coef = numpy.polyfit(numpy.arange(n, dtype=float), points, 1)
        future = numpy.arange(n, n + horizon, dtype=float)
        return [_finite(v) for v in numpy.polyval(coef, future)]

    # Metodo desconocido: es un error de llamada, no un dato que falte. Devolver
    # None lo escondería detras de un "no se" y el typo sobreviviria al test.
    raise ValueError(f"metodo desconocido: {method!r}; esperado uno de {METHODS}")


# ----------------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------------

def _round(value: Optional[float], digits: int) -> Optional[float]:
    """Redondeo de salida que nunca publica `inf`: si el redondeo no es finito, None."""
    number = _finite(value)
    if number is None:
        return None
    return _finite(round(number, digits))


def _error_metrics(
    absolute: Sequence[float], scale: Optional[float]
) -> Dict[str, Optional[float]]:
    """MAE, RMSE y MASE de una lista de errores absolutos.

    MASE = MAE / escala, con la escala del error naive in-sample que calcula
    `_naive_scale`. Sin escala (serie plana o de un solo punto) MASE es None y no
    se publica un 0: "cero errores sobre cero movimiento" no es una precision,
    es una division indefinida.

    RMSE se calcula con `math.dist` (norma con escalado interno) en vez de sumar
    cuadrados, que desbordaria a `inf` sobre montos grandes y publicaria un None
    donde habia un numero.
    """
    if not absolute:
        return {"mae": None, "rmse": None, "mase": None}
    mae = statistics.mean(absolute)
    norm = math.dist(absolute, [0.0] * len(absolute))
    rmse = norm / math.sqrt(len(absolute))
    mase = mae / scale if scale else None
    return {
        "mae": _round(mae, 2),
        "rmse": _round(rmse, 2),
        "mase": _round(mase, 3),
    }


def _naive_scale(values: Sequence[float]) -> Optional[float]:
    """Media de |diferencias| consecutivas: el error naive in-sample que escala MASE.

    Es la referencia de MASE (error absoluto medio sobre el error de repetir el
    periodo anterior) y es la MISMA para todos los metodos: si se calculara por
    metodo, MASE dejaria de comparar contra un piso comun y dos metodos podrian
    tener escalas distintas.

    None cuando hay menos de dos puntos o la serie no se mueve. Con escala 0
    MASE no existe: dividir por cero no da "error perfecto", no da nada.
    """
    numbers: List[float] = []
    for value in values:
        number = _as_number(value)
        if number is None:
            return None
        numbers.append(number)
    differences = [abs(b - a) for a, b in zip(numbers, numbers[1:])]
    scale = _finite(statistics.mean(differences)) if differences else None
    return scale if scale else None


def _walk_backtest(values: Sequence[float], horizon: int) -> Dict[str, Any]:
    """Walk-forward en UNA pasada, con todo lo que necesitan sus lectores.

    Ajustar por origen es la parte cara del modulo (un ajuste por origen para
    cada metodo). Por eso las metricas robustas salen de aca y no de un segundo
    recorrido: dos recorridos podrian divergir en el primer arreglo y publicar
    dos precisiones de la misma serie.

    Devuelve:
      - `steps`: `{metodo: [[residuo % paso 1], ...]}`, el contrato historico de
        `walk_forward`.
      - `cumulative`: `{metodo: [[residuo % del total 1..j], ...]}`; para un
        origen t y un prefijo j compara la suma de las predicciones 1..j contra
        la suma de los actuales 1..j. Es la distribucion que sostiene la banda
        del total.
      - `absolute`: `{metodo: [[|pred - actual| paso 1], ...]}`. A diferencia
        del residuo porcentual, no exige actual > 0: el error absoluto esta
        definido sobre cero (y sobre negativos), que es justo lo que MAPE no
        puede medir.
      - `scale`: la escala naive in-sample de `_naive_scale`, o None.

    Un prefijo con un paso no computable (actual <= 0 o prediccion None) no
    aporta residuo acumulado: sumar los pasos restantes seria medir un total
    distinto del que se publica.

    Origenes t = MIN_PERIODS .. len(values)-1 (un ajuste nunca ve menos de
    MIN_PERIODS puntos). Para cada origen: ajusta con values[:t], predice pasos
    1..h; el actual del paso j es values[t+j-1] si existe. Residuo porcentual =
    |pred - actual| / actual * 100, solo con actual > 0.

    Caminar hacia adelante en vez de ajustar una sola vez sobre toda la serie:
    con un solo ajuste el error medido incluiria los puntos que el modelo ya vio,
    que es la forma clasica de publicar una precision que no existe.
    """
    n = len(values)
    points = [float(value) for value in values]
    steps: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }
    cumulative: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }
    absolute: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }
    signed_steps: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }
    signed_cumulative: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }

    for t in range(MIN_PERIODS, n):
        history = points[:t]
        for method in METHODS:
            predicted = _fit_predict(method, history, horizon)
            cum_pred = 0.0
            cum_actual = 0.0
            broken = False
            for j in range(min(horizon, len(predicted))):
                index = t + j
                if index >= n:
                    # El paso j todavia no ocurrio: no hay actual que comparar.
                    break
                actual = points[index]
                point = predicted[j]
                if point is None:
                    # Sin prediccion no hay error de ningun tipo.
                    broken = True
                    continue
                error = abs(point - actual)
                if math.isfinite(error):
                    absolute[method][j].append(error)
                if actual <= 0:
                    # Denominador no positivo: el error relativo no es
                    # expresable. Ese paso no aporta residuo porcentual, y no se
                    # sustituye por 0, que seria declarar error nulo.
                    broken = True
                    continue
                residual = error / actual * 100.0
                if math.isfinite(residual):
                    steps[method][j].append(residual)
                    signed_steps[method][j].append((point - actual) / actual * 100.0)
                if broken:
                    # El total de 1..j ya no es el publicado: falta un sumando.
                    continue
                cum_pred += point
                cum_actual += actual
                if cum_actual > 0:
                    total_residual = abs(cum_pred - cum_actual) / cum_actual * 100.0
                    if math.isfinite(total_residual):
                        cumulative[method][j].append(total_residual)
                        signed_cumulative[method][j].append(
                            (cum_pred - cum_actual) / cum_actual * 100.0
                        )

    return {
        "steps": steps,
        "cumulative": cumulative,
        "signed_steps": signed_steps,
        "signed_cumulative": signed_cumulative,
        "absolute": absolute,
        "scale": _naive_scale(points),
    }


def _walk_residuals(
    values: Sequence[float], horizon: int
) -> Tuple[Dict[str, List[List[float]]], Dict[str, List[List[float]]]]:
    """Atajo historico de `_walk_backtest`: (residuos por paso, acumulados).

    Se mantiene para los llamadores y tests que solo publican la banda: la
    fuente sigue siendo una sola pasada.
    """
    backtest = _walk_backtest(values, horizon)
    return backtest["steps"], backtest["cumulative"]


def walk_forward(values: Sequence[float], horizon: int) -> Dict[str, List[List[float]]]:
    """Residuos porcentuales absolutos POR PASO, por método.

    Contrato intacto: mismos origenes, misma regla, misma forma de salida
    `{method: [[paso 1], [paso 2], ...]}`. Las metricas robustas (MAE, RMSE,
    MASE) salen de `_walk_backtest`, que ademas colecciona los errores absolutos
    en la misma pasada; no hay un segundo ajuste del backtest.
    """
    return _walk_backtest(values, horizon)["steps"]


def _metrics_from_backtest(backtest: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
    """`{metodo: {mape, mae, rmse, mase}}` a partir de una pasada del backtest."""
    scale = backtest["scale"]
    metrics: Dict[str, Dict[str, Optional[float]]] = {}
    for method in METHODS:
        percentage = [r for step in backtest["steps"][method] for r in step]
        absolute = [error for step in backtest["absolute"][method] for error in step]
        metrics[method] = {
            "mape": _round(statistics.mean(percentage), 1) if percentage else None,
            **_error_metrics(absolute, scale),
        }
    return metrics


def _select_from_backtest(backtest: Dict[str, Any]) -> Dict[str, Any]:
    """Ganador del backtest a partir de UNA pasada, con el resto de las metricas.

    `select_model` y `build_projection` necesitan exactamente el mismo backtest.
    El backtest es la parte cara del modulo y hacerlo dos veces sobre la misma
    serie no agrega informacion, agrega tiempo de respuesta en el endpoint.

    Seleccion:
      1. MASE cuando esta disponible para TODOS los metodos comparables (los que
         tienen al menos un error absoluto medible). MASE es robusto a escala y a
         valores chicos, donde MAPE se dispara.
      2. MAPE si falta MASE para alguno (por ejemplo, una serie plana no tiene
         escala naive y MASE es None para todos).
      3. Desempate por MAPE y luego por el orden de METHODS (naive primero).

    Comparacion sobre los valores YA REDONDEADOS, que son los que se publican:
    elegir un modelo mas complejo por una diferencia invisible en el payload
    seria cambiar de metodo sin poder mostrarlo.

    Regla dura: si el ganador no es naive y no mejora ESTRICTAMENTE al naive en
    la MISMA metrica con que se eligio, se publica naive. Es la frontera entre
    "un modelo mejor" y "un modelo distinto": sin esto, una recta que empata con
    el ultimo valor se presentaria como una mejora.
    """
    metrics = _metrics_from_backtest(backtest)
    mape_by_method = {method: metrics[method]["mape"] for method in METHODS}

    comparable = [method for method in METHODS if metrics[method]["mae"] is not None]
    use_mase = bool(comparable) and all(
        metrics[method]["mase"] is not None for method in comparable
    )
    primary = "mase" if use_mase else "mape"

    def order(method: str) -> Tuple[float, float, int]:
        # MAPE None va al final del desempate: no es un empate, es no saber.
        mape = metrics[method]["mape"]
        return (
            float(metrics[method][primary]),
            math.inf if mape is None else mape,
            METHODS.index(method),
        )

    ranked = [
        method
        for method in comparable
        if metrics[method][primary] is not None
    ]
    winner = min(ranked, key=order) if ranked else None
    if winner is not None and winner != "naive":
        naive = metrics["naive"][primary]
        if naive is None or not float(metrics[winner][primary]) < naive:
            winner = "naive"

    method = winner or "naive"
    per_step = backtest["steps"][method]
    return {
        "method": method,
        "beats_naive": method != "naive",
        "mape_by_method": mape_by_method,
        "metrics_by_method": metrics,
        "mape_per_step": [round(statistics.mean(step), 1) if step else None for step in per_step],
        "n_backtests_per_step": [len(step) for step in per_step],
    }


def select_model(values: Sequence[float], horizon: int) -> Dict[str, Any]:
    """{"method": str, "beats_naive": bool, "mape_by_method": {m: float|None},
        "mape_per_step": [float|None]*h, "n_backtests_per_step": [int]*h}

    Gana MASE cuando esta disponible para todos los metodos comparables; si no,
    MAPE. El desempate es por MAPE y despues por el orden de METHODS (naive
    primero). `mape_by_method` se mantiene por compatibilidad.

    Regla dura: si el ganador no es naive y no mejora ESTRICTAMENTE al naive en
    la metrica de seleccion (o naive no la tiene), se publica naive;
    `beats_naive` es True solo cuando el metodo publicado no es naive.
    """
    selection = _select_from_backtest(_walk_backtest(values, horizon))
    return {
        "method": selection["method"],
        "beats_naive": selection["beats_naive"],
        "mape_by_method": selection["mape_by_method"],
        "mape_per_step": selection["mape_per_step"],
        "n_backtests_per_step": selection["n_backtests_per_step"],
    }


def _build_total(
    points: Sequence[Optional[float]],
    cumulative: List[List[float]],
    signed_cumulative: List[List[float]],
    horizon: int,
    has_gaps: bool,
    *,
    force_worst_case: bool = False,
) -> Optional[Dict[str, Any]]:
    """Total del horizonte y su intervalo calibrado, o None si falta algun punto.

    El intervalo sale de los residuos ACUMULADOS con signo: el error de la suma
    de los pasos 1..j, no la suma de las bandas por paso. La cobertura se mide
    hacia adelante (`_walk_forward_coverage`), no sobre los mismos residuos que
    eligieron el cuantil. Si el horizonte completo no se midio, se hereda el
    prefijo medido mas largo y se declara `extrapolated`.
    """
    if any(point is None for point in points):
        return None
    total_point = _finite(sum(float(point) for point in points if point is not None))
    if total_point is None:
        return None

    measured_steps: Optional[int] = None
    residuals: Optional[List[float]] = None
    signed_residuals: List[float] = []
    for j in range(horizon, 0, -1):
        if j - 1 < len(cumulative) and cumulative[j - 1]:
            residuals = cumulative[j - 1]
            measured_steps = j
            if j - 1 < len(signed_cumulative):
                signed_residuals = signed_cumulative[j - 1]
            break

    if residuals is None:
        return {
            "point": total_point,
            "lower": None,
            "upper": None,
            "band_pct": None,
            "worst_case_pct": None,
            "band_method": "none",
            "interval_lower_pct": None,
            "interval_upper_pct": None,
            "interval_coverage": None,
            "interval_coverage_n": 0,
            "interval_target": POLICY.interval_coverage,
            "mape": None,
            "n_backtests": 0,
            "reliable": False,
            "extrapolated": False,
            "measured_steps": None,
        }

    interval = _calibrated_interval(
        signed_residuals, force_worst_case=force_worst_case
    )
    lower, upper = _interval_bounds(
        total_point, interval["lower_pct"], interval["upper_pct"]
    )
    coverage, coverage_n = _walk_forward_coverage(
        signed_residuals, force_worst_case=force_worst_case
    )
    full = measured_steps == horizon
    coverage_ok = (
        coverage is None
        or coverage >= POLICY.interval_coverage - POLICY.interval_coverage_tolerance
    )
    return {
        "point": total_point,
        "lower": lower,
        "upper": upper,
        "band_pct": interval["band_pct"],
        "worst_case_pct": interval["worst_case_pct"],
        "band_method": interval["band_method"],
        "interval_lower_pct": interval["lower_pct"],
        "interval_upper_pct": interval["upper_pct"],
        "interval_coverage": coverage,
        "interval_coverage_n": coverage_n,
        "interval_target": POLICY.interval_coverage,
        "mape": round(statistics.mean(residuals), 1) if full else None,
        "n_backtests": len(residuals),
        "reliable": bool(
            full
            and len(residuals) >= MIN_BACKTESTS_RELIABLE
            and not has_gaps
            and not force_worst_case
            and coverage_ok
        ),
        "extrapolated": not full,
        "measured_steps": measured_steps,
    }


# ----------------------------------------------------------------------------
# Payload de la proyeccion
# ----------------------------------------------------------------------------

def build_projection(
    values: Sequence[float],
    horizon: int,
    has_gaps: bool = False,
    has_level_shift: bool = False,
) -> Dict[str, Any]:
    """Payload completo: metodo, pasos con banda, total del horizonte y su evidencia.

    Devuelve `available: False` con `reason` cuando la serie no alcanza
    `MIN_PERIODS`, cuando algun valor no es numerico o no es finito, o cuando no
    quedo ni un residuo medible. `available: True` significa que el punto, la
    banda y el error publicado salen de numeros observados en esta serie.

    `metrics_by_method` (`{metodo: {mape, mae, rmse, mase}}`) y
    `model_comparison` (misma informacion como lista, con `selected`) son la
    comparacion completa; el metodo publicado repite sus `mape`, `mae`, `rmse` y
    `mase` arriba para el lector que solo mira el ganador. `mape_by_method` se
    mantiene por compatibilidad. Cada paso lleva tambien sus `mae`, `rmse` y
    `mase`: el MAPE y la banda siguen saliendo de residuos porcentuales, y las
    metricas robustas incluyen los actuales en cero, donde el error porcentual no
    esta definido.

    `has_gaps` lo calcula el servicio sobre el eje de periodos (aqui no hay
    periodos, solo valores): si la serie tiene huecos, ni los pasos ni el total
    se declaran confiables, aunque el backtest tenga residuos.

    `has_level_shift` tambien lo calcula el servicio. Un intervalo calibrado
    sobre un regimen viejo no sirve para el regimen nuevo: con un cambio de nivel
    se publica el peor caso y no se declara confiable.

    El total es la suma de los puntos publicados, con banda de los residuos
    acumulados. Si el horizonte completo no se midio, hereda el prefijo medido
    mas largo y sale `extrapolated`; nunca se presenta como medido.

    `horizon < 1` es ValueError y no `available: False`: un horizonte de 0 pasos
    no es una proyeccion que no se pudo calcular, es una llamada mal hecha, y se
    dice en el punto de entrada mientras el error todavia tiene nombre.
    """
    if horizon < 1:
        raise ValueError("horizon debe ser >= 1")

    # `_as_number` en cada valor (el mismo helper del forecast existente): una
    # serie con un None o un texto no es una serie con un hueco, es una serie que
    # no se puede proyectar. Y no finito (NaN, inf) tampoco: contagiaria todo el
    # calculo y el resultado llegaria al cliente como `NaN` sin nombre.
    clean: List[float] = []
    for value in values:
        number = _as_number(value)
        if number is None:
            return _unavailable(
                "La serie tiene valores no numericos: no se puede proyectar sin todos los periodos.",
                len(values),
            )
        if not math.isfinite(number):
            return _unavailable(
                "La serie tiene valores infinitos o NaN: no se puede proyectar sobre ellos.",
                len(values),
            )
        clean.append(number)

    n = len(clean)
    if n < MIN_PERIODS:
        return _unavailable(
            f"Se necesitan al menos {MIN_PERIODS} periodos para proyectar y hay {n}.", n
        )

    backtest = _walk_backtest(clean, horizon)
    residuals = backtest["steps"]
    cumulative = backtest["cumulative"]
    selection = _select_from_backtest(backtest)
    metrics = selection["metrics_by_method"]
    method = selection["method"]
    points = _fit_predict(method, clean, horizon)
    if points[0] is None:
        # El metodo gano el backtest sobre origenes cortos y no pudo ajustar la
        # serie completa. Se declara: publicar un paso 1 sin punto (y por lo
        # tanto sin banda) seria un payload disponible con el primer paso vacio.
        return _unavailable(
            f"El metodo '{method}' no pudo ajustar la serie completa de {n} periodos.", n
        )

    # Intervalo por paso. La banda deja de ser un unico maximo: se elige un
    # cuantil con cobertura objetivo sobre los residuos CON SIGNO, y se cae al
    # peor caso cuando hay pocos residuos o un cambio de nivel. La banda
    # simetrica (`band_pct`) sigue existiendo para clientes viejos; las puntas
    # exactas viajan en `interval_lower_pct`/`interval_upper_pct`.
    force_worst_case = bool(
        has_level_shift and POLICY.interval_use_worst_case_on_shift
    )
    intervals: List[Optional[Dict[str, Any]]] = [
        (
            _calibrated_interval(
                backtest["signed_steps"][method][i],
                force_worst_case=force_worst_case,
            )
            if residuals[method][i]
            else None
        )
        for i in range(horizon)
    ]

    carried: Optional[Dict[str, Any]] = None
    for i in range(horizon):
        if intervals[i] is None:
            intervals[i] = carried
        else:
            carried = intervals[i]

    if carried is None:
        # Ni un paso con residuo: no hay error observado que publicar. Una banda
        # de ancho 0 se leeria como certeza total sobre una serie que no dejo
        # medir ni un paso.
        return _unavailable(
            "La serie no dejo ningun residuo medible: no se puede calcular banda de error.", n
        )

    steps: List[Dict[str, Any]] = []
    for i, step_residuals in enumerate(residuals[method]):
        point = points[i]
        interval = intervals[i]
        measured = bool(step_residuals)
        lower, upper = (
            _interval_bounds(
                point, interval["lower_pct"], interval["upper_pct"]
            )
            if interval
            else (None, None)
        )
        coverage, coverage_n = (
            _walk_forward_coverage(
                backtest["signed_steps"][method][i],
                force_worst_case=force_worst_case,
            )
            if measured
            else (None, 0)
        )
        coverage_ok = (
            coverage is None
            or coverage >= POLICY.interval_coverage - POLICY.interval_coverage_tolerance
        )
        robust = _error_metrics(backtest["absolute"][method][i], backtest["scale"])
        steps.append({
            "step": i + 1,
            "point": point,
            "lower": lower,
            "upper": upper,
            "band_pct": interval["band_pct"] if interval else None,
            "worst_case_pct": interval["worst_case_pct"] if interval else None,
            "band_method": (
                "carried" if interval and not measured
                else (interval["band_method"] if interval else "none")
            ),
            "interval_lower_pct": interval["lower_pct"] if interval else None,
            "interval_upper_pct": interval["upper_pct"] if interval else None,
            "interval_coverage": coverage,
            "interval_coverage_n": coverage_n,
            "interval_target": POLICY.interval_coverage,
            "mape": round(statistics.mean(step_residuals), 1) if measured else None,
            "mae": robust["mae"],
            "rmse": robust["rmse"],
            "mase": robust["mase"],
            "n_backtests": len(step_residuals),
            "reliable": bool(
                measured
                and len(step_residuals) >= MIN_BACKTESTS_RELIABLE
                and not has_gaps
                and not force_worst_case
                and coverage_ok
            ),
            "extrapolated": not measured,
        })

    return {
        "available": True,
        "reason": None,
        "method": method,
        "method_label": METHOD_LABELS.get(method, method),
        "method_description": METHOD_DESCRIPTIONS.get(method, ""),
        "beats_naive": selection["beats_naive"],
        "mape": metrics[method]["mape"],
        "mae": metrics[method]["mae"],
        "rmse": metrics[method]["rmse"],
        "mase": metrics[method]["mase"],
        "mape_by_method": selection["mape_by_method"],
        "metrics_by_method": metrics,
        "model_comparison": [
            {
                "method": candidate,
                "label": METHOD_LABELS.get(candidate, candidate),
                **metrics[candidate],
                "selected": candidate == method,
            }
            for candidate in METHODS
        ],
        "n_periods": n,
        "steps": steps,
        "total": _build_total(
            points,
            cumulative[method],
            backtest["signed_cumulative"][method],
            horizon,
            has_gaps,
            force_worst_case=force_worst_case,
        ),
        "interval_target": POLICY.interval_coverage,
    }


# ----------------------------------------------------------------------------
# Estado agregado de calidad
# ----------------------------------------------------------------------------

def evaluate_quality(
    projection: Dict[str, Any],
    *,
    horizon: int,
    has_gaps: bool,
    diagnosis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Semáforo de calidad sobre la evidencia que ya publicó el motor.

    No es un score: no hay pesos ni un número compuesto. Se aplican reglas
    explícitas sobre MAPE, comparación con naive, cobertura del total, backtests,
    huecos y las alertas del diagnóstico (último dato lejos de la media, cambio
    de nivel, valores atípicos). Cada estado devuelve las razones que lo
    dispararon, para que la UI pueda decir por qué y no solo pintar un color.
    """
    mape = _finite(projection.get("mape"))
    total = projection.get("total")
    interval_info = {
        "interval_target": POLICY.interval_coverage,
        "interval_coverage": (total or {}).get("interval_coverage"),
        "interval_coverage_n": int((total or {}).get("interval_coverage_n") or 0),
        "band_method": (total or {}).get("band_method"),
    }
    thresholds = {
        "unreliable_threshold": UNRELIABLE_MAPE,
        "exploratory_threshold": EXPLORATORY_MAPE,
        **interval_info,
    }
    if mape is None:
        return {
            "status": "sin_medicion",
            "mape": None,
            **thresholds,
            "reasons": ["no hay un error promedio medible"],
        }

    no_confiable: List[str] = []
    if mape > UNRELIABLE_MAPE:
        no_confiable.append(
            f"el error promedio ({pct_text(mape)}) supera el límite de "
            f"{pct_text(UNRELIABLE_MAPE)}"
        )
    # `beats_naive=False` es lo esperado cuando el metodo publicado ES naive:
    # no puede superarse a si mismo. Solo es una senal negativa cuando un
    # metodo mas complejo no logro ganarle.
    if projection.get("beats_naive") is False and projection.get("method") != "naive":
        no_confiable.append("no mejora a repetir el último valor")

    total = projection.get("total")
    if total is None:
        no_confiable.append("no se pudo calcular el total del período")
    else:
        measured = total.get("measured_steps")
        if measured is None:
            no_confiable.append("el total no tiene pasos medidos")
        elif int(measured) < horizon:
            no_confiable.append(
                f"el margen del total cubre {measured} de {horizon} pasos"
            )

    if no_confiable:
        return {
            "status": "no_confiable",
            "mape": mape,
            **thresholds,
            "reasons": no_confiable,
        }

    steps = projection.get("steps") or []
    min_backtests = min(
        (
            int(step.get("n_backtests") or 0)
            for step in steps
            if step.get("point") is not None
        ),
        default=0,
    )
    exploratory: List[str] = []
    if mape > EXPLORATORY_MAPE:
        exploratory.append(
            f"el error promedio ({pct_text(mape)}) supera el límite de "
            f"{pct_text(EXPLORATORY_MAPE)}"
        )
    if min_backtests < MIN_BACKTESTS_RELIABLE:
        exploratory.append(
            f"el paso con menos evidencia tiene {min_backtests} pruebas "
            f"(mínimo {MIN_BACKTESTS_RELIABLE})"
        )
    if projection.get("beats_naive") is None:
        exploratory.append("sin comparación con repetir el último valor")
    if has_gaps:
        exploratory.append("faltan períodos en la serie")
    coverage = interval_info["interval_coverage"]
    if (
        coverage is not None
        and interval_info["interval_coverage_n"] >= POLICY.interval_min_residuals
        and coverage
        < POLICY.interval_coverage - POLICY.interval_coverage_tolerance
    ):
        exploratory.append(
            f"la cobertura observada ({pct_text(coverage * 100)}) está por "
            f"debajo del objetivo ({pct_text(POLICY.interval_coverage * 100)})"
        )

    # El semáforo mide el backtest, pero no puede ignorar que la serie tenga una
    # alerta de datos: un último dato lejos de la media o un cambio de nivel
    # cambian la lectura de la proyección aunque el error medido sea bajo.
    diagnosis = diagnosis or {}
    last_vs_mean = _finite(diagnosis.get("last_vs_mean_pct"))
    if last_vs_mean is not None and abs(last_vs_mean) > POLICY.outlier_pct:
        direction = "por encima" if last_vs_mean > 0 else "por debajo"
        exploratory.append(
            f"el último dato está {pct_text(abs(last_vs_mean))} {direction} "
            "de la media"
        )
    if bool(diagnosis.get("level_shift")):
        suffix = (
            "; la banda usa el peor caso"
            if POLICY.interval_use_worst_case_on_shift
            else ""
        )
        exploratory.append(f"la serie cambió de nivel recientemente{suffix}")
    outlier_count = diagnosis.get("outlier_count")
    if isinstance(outlier_count, int) and outlier_count > 0:
        noun = "valor atípico" if outlier_count == 1 else "valores atípicos"
        exploratory.append(f"se detectó {outlier_count} {noun} en la serie")

    if exploratory:
        return {
            "status": "exploratoria",
            "mape": mape,
            **thresholds,
            "reasons": exploratory,
        }

    return {
        "status": "confiable",
        "mape": mape,
        **thresholds,
        "reasons": [],
    }


# ----------------------------------------------------------------------------
# Auto-verificacion
# ----------------------------------------------------------------------------

def _self_check() -> None:
    """Guardia de las afirmaciones que sostienen este modulo.

    1. La banda contiene el punto en TODOS los pasos, medidos o heredados.
    2. Una tendencia real se publica como tendencia y declara que mejora.
    3. Un paso sin backtest propio hereda la banda del ultimo medido y sale
       marcado `extrapolated`.
    4. Sin MIN_PERIODS no se publica nada, y no hay aleatoriedad: misma serie,
       mismo payload.
    """
    # Recta exacta: el error de la tendencia es 0 y el naive no puede empatar
    # porque la serie sube un escalon fijo por periodo.
    recta = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0]
    projection = build_projection(recta, 3)
    assert projection["available"] is True, projection
    assert projection["method"] == "ols_trend", projection["method"]
    assert projection["beats_naive"] is True
    assert projection["mape"] == 0.0, projection["mape"]
    assert projection["mape_by_method"]["naive"] > 0.0
    # Metricas robustas: la recta exacta tiene MAE/RMSE/MASE 0, y la comparacion
    # publica marca exactamente un ganador.
    assert projection["mae"] == 0.0 and projection["rmse"] == 0.0, projection
    assert projection["mase"] == 0.0, projection["mase"]
    comparacion = projection["model_comparison"]
    assert set(projection["metrics_by_method"]) == set(METHODS), projection
    assert len(comparacion) == len(METHODS)
    assert sum(1 for item in comparacion if item["selected"]) == 1, comparacion
    for item in comparacion:
        assert item["mae"] <= item["rmse"], item
        assert item["mase"] is None or item["mase"] >= 0.0, item
    for step in projection["steps"]:
        assert step["lower"] <= step["point"] <= step["upper"], step
        assert step["band_pct"] >= 0.0
    total = projection["total"]
    assert total is not None, projection
    assert total["point"] == sum(step["point"] for step in projection["steps"]), total
    if total["lower"] is not None:
        assert total["lower"] <= total["point"] <= total["upper"], total
    assert 1 <= total["measured_steps"] <= 3, total
    assert build_projection(recta, 3) == projection, "la proyeccion no es determinista"

    # Serie plana: todos los metodos empatan en 0, se publica el mas simple y
    # NO se declara mejora.
    plana = build_projection([500.0] * 9, 3)
    assert plana["method"] == "naive" and plana["beats_naive"] is False, plana["method"]
    assert plana["mape"] == 0.0 and plana["mape_by_method"]["naive"] == 0.0
    # Sin movimiento no hay escala naive: MASE es None, no un 0 que se leeria
    # como error perfecto.
    assert plana["mase"] is None, plana["mase"]
    assert all(item["mase"] is None for item in plana["model_comparison"]), plana

    # 9 periodos y horizonte 12: del paso 4 en adelante no hay actual que
    # comparar, asi que heredan la banda del ultimo paso medido.
    alterna = [10.0, 20.0, 10.0, 20.0, 10.0, 20.0, 10.0, 20.0, 10.0]
    larga = build_projection(alterna, 12)
    assert larga["available"] is True, larga
    assert len(larga["steps"]) == 12
    for step in larga["steps"][3:]:
        assert step["extrapolated"] is True, step
        assert step["n_backtests"] == 0 and step["mape"] is None, step
        assert step["reliable"] is False, step
    ultimo_medido = [s for s in larga["steps"] if not s["extrapolated"]][-1]
    assert ultimo_medido["step"] <= 3, ultimo_medido
    for step in larga["steps"][ultimo_medido["step"]:]:
        assert step["band_pct"] == ultimo_medido["band_pct"], step
    assert larga["total"]["measured_steps"] == 3, larga["total"]
    assert larga["total"]["extrapolated"] is True, larga["total"]
    assert larga["total"]["mape"] is None, larga["total"]

    corta = build_projection(recta[:5], 3)
    assert corta["available"] is False and corta["steps"] == [], corta
    assert corta["total"] is None, corta
    assert corta["reason"] and "hay 5" in corta["reason"], corta["reason"]
    assert corta["model_comparison"] == [] and corta["mase"] is None, corta

    # El error relativo necesita denominador positivo: sin el, se declara y no se
    # divide por cero.
    ceros = build_projection([0.0] * 9, 3)
    assert ceros["available"] is False and ceros["reason"], ceros

    # Serie que baja y cruza el cero: el punto proyectado queda NEGATIVO con
    # banda positiva, que es donde la formula sin orden publica el intervalo al
    # reves. Es un caso que este modulo SI puede producir y `forecast_next_period`
    # no, porque aquel exige ultimo valor > 0.
    bajando = [100.0, 92.0, 79.0, 71.0, 58.0, 51.0, 38.0, 31.0]
    cayendo = build_projection(bajando, 8)
    assert cayendo["available"] is True, cayendo
    negativos = [s for s in cayendo["steps"] if s["point"] < 0]
    assert negativos, f"el caso no llega a ejercitar un punto negativo: {cayendo['steps']}"
    for step in negativos:
        assert step["band_pct"] > 0.0, step
        assert step["lower"] <= step["point"] <= step["upper"], step
    total_cayendo = cayendo["total"]
    assert total_cayendo is not None, cayendo
    if total_cayendo["lower"] is not None:
        assert total_cayendo["lower"] <= total_cayendo["point"] <= total_cayendo["upper"], total_cayendo

    for bad in (0, -1):
        try:
            build_projection(recta, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"horizon={bad} debio ser ValueError")


if __name__ == "__main__":
    _self_check()
    print("ml_models: OK")
