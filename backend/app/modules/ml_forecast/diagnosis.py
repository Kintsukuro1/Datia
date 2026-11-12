"""Diagnostico de una serie en palabras simples, sin jerga estadistica.

Que hace
--------
Recibe la serie historica YA armada por el modulo de proyecciones (valores y
sus periodos) y devuelve un veredicto que una persona de negocio puede leer: si
viene subiendo, bajando o se mantiene, cuanto se aparta del promedio, si se
repite un patron por ciclo, cuantos valores raros hay, si el nivel cambio y
cuantos pasos de historia hay de verdad.

Es un modulo PURO: sin base de datos, sin LLM, sin configuracion, y no proyecta
nada. Todo lo que publica sale de los valores recibidos.

Decisiones:
- Los umbrales, ventanas y horizontes salen de `policy.POLICY`: aca no se
  duplica ningun numero de producto.
- Un `NULL`, un texto, un `NaN` o un `inf` no es un cero. Contarlo como cero
  inventaria una caida que no ocurrio; se descarta el punto y se sigue. El
  periodo sin valor queda como hueco, no como valor cero.
- El CV y la distancia a la media dividen por |media|: en una serie de perdidas
  (media negativa) el cociente con signo describiria el signo de los datos, no
  su variabilidad. "Por encima o por debajo" lo decide el numerador.
- La tendencia se mide sobre los ultimos `trend_window` puntos con pendiente OLS
  y su error estandar. Una pendiente que no supera a la vez
  `trend_threshold_pct` y `trend_min_t` es ruido y se publica "estable".
- La estacionalidad cambia de ciclo con la granularidad (dia de la semana, mes
  del ano, mes o trimestre) y se mide con eta^2 sobre los residuales de la
  tendencia: una recta no es un patron estacional. Exige dos ciclos completos
  (`seasonality_min_points`). Un pico estacional repetido tambien puede aparecer
  como valor atipico: el aviso es para revisar, no un veredicto.
- Los outliers se miden con mediana y MAD (`outlier_z`), no contra la media: un
  solo valor extremo mueve la media y esconde su propio efecto.
- El cambio de nivel compara la ultima ventana con la anterior en escala
  robusta; con menos de dos ventanas no se afirma nada. Si hay cambio de nivel
  reciente, no se afirma ademas un patron estacional: con dos ciclos no alcanza
  para separar el patron del escalon.
- Los huecos se cuentan con la aritmetica de periodos de `forecast_calculator`.

El resumen no usa MAPE, backtest, naive ni banda: si un termino necesita
explicacion, no va. El numero crudo queda en los campos, el texto explica.
"""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Los helpers privados se importan A PROPOSITO, no se copian: la normalizacion
# numerica y la aritmetica de periodos tienen que ser UNA sola implementacion en
# todo el pipeline. Dos copias divergen en el primer arreglo y la misma serie se
# lee distinto segun por que camino entro. Sin dependencias pesadas:
# `forecast_calculator` es stdlib pura.
from app.modules.chat_engine.forecast_calculator import (
    _as_number,
    normalize_period,
    period_distance,
)
from app.modules.ml_forecast.formatting import pct_text
from app.modules.ml_forecast.policy import POLICY

NO_DATA_SUMMARY = "No hay suficientes datos para diagnosticar la serie."

# Cerca de cero el cociente explota; se trata como media cero y se publica None
# en vez de un porcentaje de un millon por ciento.
_ZERO = 1e-12

# Constante estadistica de la MAD: lleva su escala a la de un desvio estandar
# bajo normalidad. No es una decision de producto, por eso no vive en POLICY.
_MAD_SCALE = 1.4826

# Como se nombra el ciclo estacional de cada granularidad en el resumen.
_CYCLE_TEXT = {
    "day": ("cada semana", "algunos días"),
    "week": ("a lo largo del año", "algunos meses"),
    "month": ("cada año", "algunos meses"),
    "quarter": ("cada año", "algunos trimestres"),
}


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def _finite(value: Any) -> Optional[float]:
    """Numero finito, o None. Un `NaN` no es un dato: es la ausencia de uno."""
    number = _as_number(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _ols(points: Sequence[float]) -> Tuple[float, float]:
    """Pendiente e intercepto OLS del tramo (0.0 si no se puede ajustar)."""
    count = len(points)
    if count < 2:
        return 0.0, (points[0] if points else 0.0)
    mean_x = (count - 1) / 2.0
    mean_y = statistics.fmean(points)
    sxx = sum((index - mean_x) ** 2 for index in range(count))
    sxy = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(points)
    )
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def _trend(values: Sequence[float]) -> Tuple[str, Optional[float]]:
    """Direccion de la serie, con la pendiente en % de la media por paso.

    La direccion solo se afirma si la pendiente supera `trend_threshold_pct` Y
    su t supera `trend_min_t`: con 24 puntos, una pendiente chica pero sostenida
    es estadisticamente significativa y sigue siendo ruido para el negocio. El
    t se calcula con el error estandar de la pendiente; en un ajuste perfecto el
    error es cero y la pendiente distinta de cero es evidencia total.
    """
    window = list(values[-POLICY.trend_window:])
    count = len(window)
    if count < 2:
        return "estable", None

    slope, intercept = _ols(window)
    mean_y = statistics.fmean(window)
    step_pct: Optional[float] = None
    if abs(mean_y) > _ZERO:
        step_pct = slope / abs(mean_y) * 100.0

    t_value: Optional[float] = None
    if count >= 3:
        mean_x = (count - 1) / 2.0
        sxx = sum((index - mean_x) ** 2 for index in range(count))
        residuals = [
            value - (intercept + slope * index)
            for index, value in enumerate(window)
        ]
        sse = sum(residual * residual for residual in residuals)
        if sxx > _ZERO:
            error = math.sqrt(sse / (count - 2) / sxx)
            if error > _ZERO:
                t_value = slope / error
            elif abs(slope) > _ZERO:
                t_value = math.inf if slope > 0 else -math.inf
            else:
                t_value = 0.0

    significant = (
        step_pct is not None
        and abs(step_pct) > POLICY.trend_threshold_pct
        and t_value is not None
        and abs(t_value) >= POLICY.trend_min_t
    )
    if significant and step_pct > 0:
        return "creciente", step_pct
    if significant and step_pct < 0:
        return "decreciente", step_pct
    return "estable", step_pct


def _season_key(period: Any, granularity: str) -> Optional[int]:
    """Posicion dentro del ciclo estacional de la granularidad, o None.

    Diario: dia de la semana (1-7). Semanal: mes del ano de la semana ISO
    (1-12). Mensual: mes (1-12). Trimestral: trimestre (1-4). Una etiqueta que
    `normalize_period` no reconoce devuelve None en vez de inventar el eje.
    """
    if granularity not in ("day", "week", "month", "quarter"):
        return None
    normalized = normalize_period(period, granularity)
    if normalized is None:
        return None
    try:
        if granularity == "day":
            return date.fromisoformat(normalized).isoweekday()
        if granularity == "week":
            year, week = int(normalized[:4]), int(normalized[6:8])
            return date.fromisocalendar(year, week, 1).month
        if granularity == "quarter":
            return int(normalized[6])
        return int(normalized[5:7])
    except ValueError:
        return None


def _eta_squared(groups: Sequence[Sequence[float]], residuals: Sequence[float]) -> float:
    """Fraccion de la variacion (sin tendencia) que explica el ciclo.

    Eta^2 = SS_entre_grupos / SS_total, con la mediana como centro de cada
    grupo: un unico valor extremo no alcanza para fabricar un patron. Se calcula
    sobre los residuales del ajuste lineal para que una recta no se disfrace de
    estacionalidad. Si no queda variacion despues de la tendencia, la fuerza es
    0: no hay patron que explicar.
    """
    center = statistics.median(residuals)
    total = sum((residual - center) ** 2 for residual in residuals)
    if total <= _ZERO:
        return 0.0
    between = sum(
        len(group) * (statistics.median(group) - center) ** 2
        for group in groups
    )
    return min(1.0, max(0.0, between / total))


def _seasonality(
    clean: List[Tuple[float, Any]],
    numbers: Sequence[float],
    n: int,
    granularity: str,
) -> Tuple[str, Optional[int], Optional[float]]:
    """Patron ciclico repetido, o el motivo por el que no se puede afirmar.

    Se agrupan los valores por su posicion en el ciclo de la granularidad y se
    mide eta^2 sobre los residuales de la tendencia. Con menos de dos ciclos
    completos, o con un calendario a medias, un pico aislado parece un patron y
    se declara `insuficiente_historia` en vez de afirmarlo.
    """
    length = POLICY.season_length(granularity)
    if n < POLICY.seasonality_min_points(granularity):
        return "insuficiente_historia", length, None

    keys = [_season_key(period, granularity) for _, period in clean]
    if any(key is None for key in keys):
        # Un calendario a medias promedia ciclos con distinta cantidad de anos y
        # fabrica el patron que dice estar midiendo.
        return "insuficiente_historia", length, None

    slope, intercept = _ols(numbers)
    residuals = [
        value - (intercept + slope * index)
        for index, value in enumerate(numbers)
    ]
    residual_groups: Dict[int, List[float]] = {}
    raw_groups: Dict[int, List[float]] = {}
    for key, value, residual in zip(keys, numbers, residuals, strict=False):
        residual_groups.setdefault(key, []).append(residual)
        raw_groups.setdefault(key, []).append(value)

    strength = _eta_squared(list(residual_groups.values()), residuals)
    averages = [statistics.fmean(group) for group in raw_groups.values()]
    lowest = min(averages)
    # La eta^2 puede ser alta por un alias (una alternancia de dos periodos
    # encaja con las posiciones pares de un ciclo de 12). El cociente entre el
    # ciclo mas alto y el mas bajo exige que la diferencia sea material; con
    # medias no positivas el cociente no mide estacionalidad y no se afirma.
    material = lowest > 0 and max(averages) / lowest > POLICY.seasonality_ratio
    label = (
        "posible_estacionalidad"
        if strength >= POLICY.seasonality_min_eta2 and material
        else "sin_estacionalidad_clara"
    )
    # La fuerza publicada describe el patron que SI se afirma: si el ciclo no
    # supera el umbral o la diferencia no es material, no hay estacionalidad que
    # medir y se publica 0 en vez de un numero que contradice la etiqueta.
    if label != "posible_estacionalidad":
        strength = 0.0
    return label, length, strength


def _outlier_count(numbers: Sequence[float]) -> int:
    """Cuantos puntos se alejan de la mediana mas de `outlier_z` scales robustas.

    La escala es la MAD. Si la MAD es cero (una serie casi constante) se usa la
    desviacion media absoluta: un unico valor distinto si es un atipico, pero
    ruido de coma flotante no alcanza para declararlo.
    """
    median = statistics.median(numbers)
    deviations = [abs(value - median) for value in numbers]
    scale = _MAD_SCALE * statistics.median(deviations)
    if scale <= _ZERO:
        scale = statistics.fmean(deviations)
    if scale <= _ZERO:
        return 0
    return sum(
        1 for deviation in deviations if deviation / scale > POLICY.outlier_z
    )


def _level_shift(numbers: Sequence[float]) -> Tuple[bool, Optional[float]]:
    """Cambio de nivel entre la ultima ventana y la anterior, en escala robusta.

    Se comparan las medias de las dos ventanas, pero la diferencia se mide
    contra lo que el tramo ya venia cambiando (pendiente OLS de las dos
    ventanas): si no, una serie que sube parejo se reportaria como cambio de
    nivel en cada corte. La escala es el error estandar de la diferencia usando
    la dispersion DENTRO de cada ventana: la dispersion propia (y no la de toda
    la serie) evita que el cambio que se quiere medir agrande su propio rasero,
    y una ventana con un valor extremo se vuelve exigente consigo misma en vez
    de inventar un salto. Con menos de dos ventanas completas no se afirma nada.
    Si las ventanas no tienen dispersion, cualquier desvio real de la tendencia
    local es un cambio; el piso compara contra el ruido de coma flotante.
    """
    window = POLICY.level_shift_window
    if window < 1 or len(numbers) < 2 * window:
        return False, None

    block = list(numbers[-2 * window:])
    local_slope, _ = _ols(block)
    previous = block[:window]
    last = block[window:]
    previous_center = statistics.fmean(previous)
    last_center = statistics.fmean(last)
    difference = last_center - previous_center
    residual_difference = difference - local_slope * window
    standard_error = math.sqrt(
        (statistics.pvariance(previous) + statistics.pvariance(last)) / window
    )

    if standard_error > _ZERO:
        shifted = (
            abs(residual_difference) / standard_error > POLICY.level_shift_z
        )
    else:
        epsilon = _ZERO * max(1.0, abs(previous_center), abs(last_center))
        shifted = abs(residual_difference) > epsilon

    shift_pct: Optional[float] = None
    if abs(previous_center) > _ZERO:
        shift_pct = difference / abs(previous_center) * 100.0
    return shifted, shift_pct


def _missing_periods(
    clean: List[Tuple[float, Any]], granularity: str
) -> Optional[int]:
    """Periodos que faltan entre el primero y el ultimo, o None si no se sabe.

    Un calendario ilegible no autoriza a decir "no hay huecos": se devuelve
    None. Los periodos repetidos se cuentan una vez.
    """
    if granularity not in ("day", "week", "month", "quarter"):
        return None
    normalized: List[str] = []
    for _, period in clean:
        value = normalize_period(period, granularity)
        if value is None:
            return None
        normalized.append(value)

    ordered = sorted(set(normalized))
    missing = 0
    for previous, current in zip(ordered, ordered[1:], strict=False):
        distance = period_distance(previous, current, granularity)
        if distance > 1:
            missing += distance - 1
    return missing


def _outlier_sentence(last_vs_mean_pct: float) -> str:
    """La frase del ultimo dato, con la direccion que dicta su signo."""
    direction = "por encima" if last_vs_mean_pct > 0 else "por debajo"
    return (
        f"El último dato está {pct_text(abs(last_vs_mean_pct))} {direction} de la "
        "media; conviene revisarlo antes de proyectar."
    )


def _level_shift_sentence(level_shift_pct: Optional[float]) -> str:
    """La frase del cambio de nivel, con el porcentaje solo si es calculable."""
    window = POLICY.level_shift_window
    if level_shift_pct is None:
        return (
            f"El nivel de los últimos {window} períodos cambió respecto del "
            "tramo anterior."
        )
    direction = "subió" if level_shift_pct > 0 else "bajó"
    return (
        f"El nivel de los últimos {window} períodos {direction} "
        f"{pct_text(abs(level_shift_pct))} respecto del tramo anterior."
    )


def _insufficient(reason: str) -> Dict[str, Any]:
    """El contrato completo cuando la serie no alcanza para diagnosticar."""
    return {
        "summary": reason,
        "trend": "estable",
        "cv_pct": None,
        "last_vs_mean_pct": None,
        "seasonality": "insuficiente_historia",
        "season_length": None,
        "seasonality_strength": None,
        "trend_pct_per_step": None,
        "level_shift": False,
        "level_shift_pct": None,
        "outlier_count": 0,
        "missing_periods": None,
        "usable_periods": 0,
        "reliable_horizon": None,
        "warnings": [reason],
        "max_recommended_horizon": None,
    }


# ----------------------------------------------------------------------------
# Partes del resumen
# ----------------------------------------------------------------------------

def _build_summary(
    trend: str,
    cv_pct: Optional[float],
    seasonality: str,
    n: int,
    max_horizon: int,
    last_vs_mean_pct: Optional[float],
    granularity: str,
) -> str:
    """Dos o tres frases (mas los avisos) sin jerga."""
    cycle = _CYCLE_TEXT.get(granularity, _CYCLE_TEXT["month"])
    if trend == "creciente":
        first = "La serie viene subiendo de forma sostenida"
    elif trend == "decreciente":
        first = "La serie viene bajando de forma sostenida"
    elif cv_pct is not None and cv_pct < POLICY.low_cv_pct:
        first = (
            "La serie es estable: se mueve alrededor de la media "
            f"con una variación de {pct_text(cv_pct)}"
        )
    elif cv_pct is not None:
        first = (
            "La serie no tiene una dirección clara: los valores varían bastante "
            f"alrededor de la media (la variación es de {pct_text(cv_pct)})"
        )
    else:
        first = "La serie no tiene una dirección clara"
    if trend != "estable" and cv_pct is not None:
        first += f" (la variación es de {pct_text(cv_pct)} alrededor de la media)"

    parts = [first + "."]

    if seasonality == "posible_estacionalidad":
        parts.append(
            f"Se repite un patrón {cycle[0]}: {cycle[1]} suelen estar bastante "
            "por encima de otros."
        )
    elif seasonality == "insuficiente_historia":
        if n < POLICY.seasonality_min_points(granularity):
            parts.append(
                "Todavía no hay historia suficiente para ver si se repite un "
                f"patrón {cycle[0]}."
            )
        else:
            parts.append(
                "Las fechas de la serie no se pudieron leer, así que no se "
                f"revisó si se repite un patrón {cycle[0]}."
            )
    elif trend == "estable" and cv_pct is not None and cv_pct < POLICY.low_cv_pct:
        parts.append("No se ve una tendencia clara ni un patrón estacional definido.")
    else:
        parts.append("No se ve un patrón estacional definido.")

    if last_vs_mean_pct is not None and abs(last_vs_mean_pct) > POLICY.outlier_pct:
        parts.append(_outlier_sentence(last_vs_mean_pct))

    if max_horizon > 0:
        parts.append(
            f"La historia alcanza para unos {max_horizon} pasos; más allá, el pronóstico "
            "se aleja de los datos disponibles."
        )
    else:
        parts.append(
            "Todavía no hay historia suficiente para proyectar: hacen falta al "
            f"menos {POLICY.min_periods} períodos."
        )
    return " ".join(parts)


# ----------------------------------------------------------------------------
# Entrada publica
# ----------------------------------------------------------------------------

def diagnose_series(
    values: Sequence[Any],
    periods: Sequence[Any],
    *,
    horizon: int,
    granularity: str = "month",
) -> Dict[str, Any]:
    """Diagnostico legible de la serie: resumen, tendencia, calidad y avisos.

    `values` y `periods` se alinean por posicion y se descartan los pares sin un
    valor numerico finito. Si quedan menos de `POLICY.min_diagnosable`, no se
    afirma nada: el resumen lo dice y los campos nuevos vuelven con su default
    seguro.

    `horizon` es lo que el usuario pidio proyectar. No cambia ningun calculo:
    sirve para avisar cuando la historia no sostiene ese pedido.

    `granularity` define el ciclo estacional, la aritmetica de huecos y los
    horizontes derivados. Los pasos se cuentan en periodos de esa granularidad.
    """
    pairs = list(zip(
        [] if values is None else list(values),
        [] if periods is None else list(periods),
        strict=False,
    ))
    clean: List[Tuple[float, Any]] = []
    for value, period in pairs:
        number = _finite(value)
        if number is not None:
            clean.append((number, period))

    numbers = [number for number, _ in clean]
    n = len(numbers)
    if n < POLICY.min_diagnosable:
        return _insufficient(NO_DATA_SUMMARY)

    mean = statistics.fmean(numbers)

    cv_pct: Optional[float] = None
    last_vs_mean_pct: Optional[float] = None
    if abs(mean) > _ZERO:
        cv_pct = statistics.pstdev(numbers) / abs(mean) * 100.0
        last_vs_mean_pct = (numbers[-1] - mean) / abs(mean) * 100.0

    trend, trend_pct_per_step = _trend(numbers)
    level_shift, level_shift_pct = _level_shift(numbers)
    seasonality, season_length, seasonality_strength = _seasonality(
        clean, numbers, n, granularity
    )
    if level_shift and seasonality == "posible_estacionalidad":
        # Un escalon reciente contamina el ultimo ciclo: con dos ciclos de
        # historia no alcanza para separar patron de cambio de nivel. Se publica
        # el cambio de nivel, que es lo observable, y no se afirma el patron.
        seasonality, seasonality_strength = "sin_estacionalidad_clara", 0.0
    outlier_count = _outlier_count(numbers)
    missing_periods = _missing_periods(clean, granularity)

    max_horizon = POLICY.max_horizon_for(n)
    reliable_horizon = POLICY.reliable_horizon_for(n)

    warnings: List[str] = []
    if last_vs_mean_pct is not None and abs(last_vs_mean_pct) > POLICY.outlier_pct:
        warnings.append(_outlier_sentence(last_vs_mean_pct))
    if outlier_count == 1:
        warnings.append("Se detectó 1 valor atípico en la serie.")
    elif outlier_count > 1:
        warnings.append(
            f"Se detectaron {outlier_count} valores atípicos en la serie."
        )
    if level_shift:
        warnings.append(_level_shift_sentence(level_shift_pct))
    if missing_periods == 1:
        warnings.append(
            "Falta 1 período en la serie: la historia tiene un hueco."
        )
    elif missing_periods is not None and missing_periods > 1:
        warnings.append(
            f"Faltan {missing_periods} períodos en la serie: la historia tiene "
            "huecos."
        )
    min_seasonality_points = POLICY.seasonality_min_points(granularity)
    if n < min_seasonality_points:
        warnings.append(
            f"Hay pocos períodos de historia ({n}); con menos de "
            f"{min_seasonality_points} el diagnóstico es menos confiable."
        )
    # `horizon` puede llegar en None desde el camino "Auto" del pedido HTTP; ahi
    # no hay pedido que avisar. Un None comparado con un entero seria un 500
    # despues de haber calculado todo el diagnostico.
    if horizon is not None and max_horizon > 0 and horizon > max_horizon:
        warnings.append(
            f"Pediste {horizon} pasos; la historia alcanza para ~{max_horizon}."
        )

    return {
        "summary": _build_summary(
            trend, cv_pct, seasonality, n, max_horizon, last_vs_mean_pct,
            granularity,
        ),
        "trend": trend,
        "cv_pct": cv_pct,
        "last_vs_mean_pct": last_vs_mean_pct,
        "seasonality": seasonality,
        "season_length": season_length,
        "seasonality_strength": seasonality_strength,
        "trend_pct_per_step": trend_pct_per_step,
        "level_shift": level_shift,
        "level_shift_pct": level_shift_pct,
        "outlier_count": outlier_count,
        "missing_periods": missing_periods,
        "usable_periods": n,
        "reliable_horizon": reliable_horizon,
        "warnings": warnings,
        "max_recommended_horizon": max_horizon,
    }
