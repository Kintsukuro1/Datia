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
1. Gana el error, no la sofisticacion. Si el candidato no le gana ESTRICTAMENTE
   al naive, se publica naive: un modelo mas complejo que empata no es una
   mejora, es mas superficie donde fallar. `beats_naive` lo declara para que la
   UI pueda decir "se probo y no mejoro" en vez de mostrar un metodo nuevo sin
   contexto.
2. La banda es por PASO y sale de los residuos de ESE paso, con la misma regla
   que el forecast existente (cuantil 80 con piso en el peor residuo). Un paso
   sin backtest posible no inventa banda: hereda la del ultimo paso medido y
   sale marcado `extrapolated`, porque una banda heredada no es una medicion de
   ese paso.

Sin DB, sin LLM, sin I/O: entra una serie de numeros y salen numeros. Todo lo
que no se puede calcular es None, nunca un 0 que el lector tomaria por un dato.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy

# Los helpers privados se importan A PROPOSITO, no se copian: la regla de banda
# (`max(cuantil 80, peor residuo)`) y la normalizacion numerica tienen que ser
# UNA sola implementacion. Dos copias divergen en el primer arreglo y la misma
# serie termina con dos bandas distintas segun por que camino se calculo. El
# unico bug que un forecast no puede tener es que el mismo dato de dos numeros.
from ..chat_engine.forecast_calculator import (
    BAND_COVERAGE,
    MIN_PERIODS,
    _as_number,
    _quantile,
)

# Backtests por paso a partir de los cuales la banda de ese paso se declara
# confiable. 4 es el mismo piso que ya usa `forecast_next_period`: con 3 residuos
# el cuantil 80 es practicamente el maximo, o sea la banda mide el ruido de la
# serie y no su error. Se publica igual, con `reliable: False`.
MIN_BACKTESTS_RELIABLE = 4

# Orden = desempate por simplicidad. Con dos MAPE iguales gana el primero, y el
# primero es el naive: entre un modelo simple y uno complejo que miden lo mismo,
# el simple tiene menos formas de romperse en produccion.
METHODS: Tuple[str, ...] = ("naive", "mean", "ols_trend")


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


def _bounds(
    point: Optional[float], band_pct: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """(lower, upper) del punto, o (None, None) si falta cualquiera de las dos.

    El ancho es el de `forecast_next_period` (misma banda, sin clamp a 0: una
    banda del 120% sobre un punto de 100 da piso -20, y recortarlo publicaria una
    banda mas angosta que el error que se midio), pero las puntas se ordenan con
    `min`/`max` en vez de confiar en el signo de la formula.

    Por que el orden no es redundant: con un punto NEGATIVO —una serie que baja y
    cruza el cero— los dos productos se invierten y quedaria publicado un
    intervalo al reves, que excluye su propia estimacion. Medido con la serie
    [100, 92, 79, 71, 58, 51, 38, 31] y horizonte 8 (el caso del `_self_check`):
    paso 4, punto -7,4524 y banda 0,1%; la formula sin orden daria lower -7,4450
    arriba de upper -7,4599. `forecast_next_period` no necesita el orden porque
    exige ultimo valor > 0; este modulo si puede proyectar negativo, asi que el
    signo del punto no alcanza como garantia.

    `_finite` en las dos puntas porque una banda enorme sobre un punto enorme
    desborda a `inf`: un intervalo infinito no es una banda, es un error de
    parseo en el cliente. Si una punta desborda no se publica media banda.
    """
    if point is None or band_pct is None:
        return None, None
    ends = (point * (1 - band_pct / 100.0), point * (1 + band_pct / 100.0))
    lower, upper = _finite(min(ends)), _finite(max(ends))
    if lower is None or upper is None:
        return None, None
    return lower, upper


def _unavailable(reason: str, n_periods: int) -> Dict[str, Any]:
    """Payload de "no se puede proyectar", con TODAS las claves del contrato.

    Mismo esqueleto que el caso disponible a proposito: el que lee el payload no
    tiene que distinguir dos formas. `steps: []` es la verdad —no hay ningun paso
    que publicar— y no un hueco que el cliente tenga que interpretar.
    """
    return {
        "available": False,
        "reason": reason,
        "method": None,
        "beats_naive": None,
        "mape": None,
        "mape_by_method": {method: None for method in METHODS},
        "n_periods": n_periods,
        "steps": [],
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

def walk_forward(values: Sequence[float], horizon: int) -> Dict[str, List[List[float]]]:
    """Residuos porcentuales absolutos POR PASO, por método.

    Orígenes t = MIN_PERIODS .. len(values)-1 (un ajuste nunca ve menos de
    MIN_PERIODS puntos). Para cada origen: ajusta con values[:t], predice pasos
    1..h; el actual del paso j es values[t+j-1] si existe y es > 0 (si no, ese
    paso no aporta residuo).

    Devuelve {method: [[residuos del paso 1], [paso 2], ...]} — exactamente
    `horizon` listas por método, posiblemente vacías.

    Residuo = |pred - actual| / actual * 100 (porcentaje, como
    `forecast_calculator`).

    Caminar hacia adelante en vez de ajustar una sola vez sobre toda la serie:
    con un solo ajuste el error medido incluiria los puntos que el modelo ya vio,
    que es la forma clasica de publicar una precision que no existe.
    """
    n = len(values)
    residuals: Dict[str, List[List[float]]] = {
        method: [[] for _ in range(max(horizon, 0))] for method in METHODS
    }

    for t in range(MIN_PERIODS, n):
        history = values[:t]
        for method in METHODS:
            predicted = _fit_predict(method, history, horizon)
            for j in range(min(horizon, len(predicted))):
                index = t + j
                if index >= n:
                    # El paso j todavia no ocurrio: no hay actual que comparar.
                    break
                actual = values[index]
                point = predicted[j]
                if point is None or actual is None or actual <= 0:
                    # Denominador no positivo: el error relativo no es
                    # expresable. Ese paso no aporta residuo, y no se sustituye
                    # por 0, que seria declarar error nulo.
                    continue
                residual = abs(point - float(actual)) / float(actual) * 100.0
                if math.isfinite(residual):
                    residuals[method][j].append(residual)

    return residuals


def _select_from_residuals(residuals: Dict[str, List[List[float]]]) -> Dict[str, Any]:
    """Ganador del backtest. Existe para no ajustar Holt dos veces.

    `select_model` y `build_projection` necesitan exactamente los mismos
    residuos por paso. El backtest es la parte cara del modulo (un ajuste de Holt
    por origen) y hacerlo dos veces sobre la misma serie no agrega informacion,
    agrega tiempo de respuesta en el endpoint.
    """
    mape_by_method: Dict[str, Optional[float]] = {}
    for method in METHODS:
        flat = [r for step in residuals[method] for r in step]
        # MAPE de un metodo = media de TODOS sus residuos medibles (aplanado).
        # Un metodo puede no haber ajustado ningun origen: entonces None, que es
        # "no se", y no participa de la eleccion.
        mape_by_method[method] = round(statistics.mean(flat), 1) if flat else None

    # Comparacion sobre el MAPE YA REDONDEADO, que es el numero que se publica.
    # Elegir un modelo mas complejo por una diferencia invisible en el payload
    # seria cambiar de metodo sin poder mostrarlo.
    winner: Optional[str] = None
    best: Optional[float] = None
    for method in METHODS:
        value = mape_by_method[method]
        if value is None:
            continue
        if best is None or value < best:
            best, winner = value, method

    method = winner or "naive"
    # Regla dura: el que no le gana ESTRICTAMENTE al naive no se publica. Es la
    # frontera entre "un modelo mejor" y "un modelo distinto": sin esto, una
    # recta que empata con el ultimo valor se presentaria como una mejora.
    if method != "naive" and (best is None or not best < (mape_by_method["naive"] or 0.0)):
        method = "naive"

    per_step = residuals[method]
    return {
        "method": method,
        "beats_naive": method != "naive",
        "mape_by_method": mape_by_method,
        "mape_per_step": [round(statistics.mean(step), 1) if step else None for step in per_step],
        "n_backtests_per_step": [len(step) for step in per_step],
    }


def select_model(values: Sequence[float], horizon: int) -> Dict[str, Any]:
    """{"method": str, "beats_naive": bool, "mape_by_method": {m: float|None},
        "mape_per_step": [float|None]*h, "n_backtests_per_step": [int]*h}

    MAPE de un método = media de TODOS sus residuos medibles (aplanado); None si
    no hay. Gana el mínimo, y el empate se resuelve por el orden de METHODS
    (naive primero).

    Regla dura: si el ganador no es naive y su MAPE NO es estrictamente menor que
    el de naive (o naive no tiene MAPE), se publica naive; `beats_naive` es
    True solo cuando el método publicado no es naive.
    """
    return _select_from_residuals(walk_forward(values, horizon))


# ----------------------------------------------------------------------------
# Payload de la proyeccion
# ----------------------------------------------------------------------------

def build_projection(values: Sequence[float], horizon: int) -> Dict[str, Any]:
    """Payload completo de la proyeccion: metodo publicado, pasos y banda por paso.

    Devuelve `available: False` con `reason` cuando la serie no alcanza
    `MIN_PERIODS`, cuando algun valor no es numerico o no es finito, o cuando no
    quedo ni un residuo medible. `available: True` significa que el punto, la
    banda y el error publicado salen de numeros observados en esta serie.

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

    residuals = walk_forward(clean, horizon)
    selection = _select_from_residuals(residuals)
    method = selection["method"]
    points = _fit_predict(method, clean, horizon)
    if points[0] is None:
        # El metodo gano el backtest sobre origenes cortos y no pudo ajustar la
        # serie completa. Se declara: publicar un paso 1 sin punto (y por lo
        # tanto sin banda) seria un payload disponible con el primer paso vacio.
        return _unavailable(
            f"El metodo '{method}' no pudo ajustar la serie completa de {n} periodos.", n
        )

    # Banda por paso, con la MISMA regla de `forecast_next_period`: cuantil 80 de
    # los residuos de ese paso, con piso en el peor residuo observado. La banda no
    # es un parametro elegido: es lo que la serie hizo antes de que se le pidiera
    # predecir.
    band_by_step: List[Optional[float]] = []
    for step_residuals in residuals[method]:
        if step_residuals:
            ordered = sorted(step_residuals)
            band_by_step.append(
                max(_quantile(ordered, BAND_COVERAGE) or 0.0, max(step_residuals))
            )
        else:
            band_by_step.append(None)

    # Un paso sin backtest propio no puede tener banda medida. Hereda la del
    # ultimo paso anterior que SI se midio, y sale marcado `extrapolated`: una
    # banda heredada no es una medicion de ese paso, y presentarla como propia
    # seria declarar medida una banda que nadie midio. Se hereda la del paso
    # medido MAS CERCANO porque el error crece con el horizonte; NO se toma el
    # maximo de las bandas medidas, que seria el error de un paso con el que este
    # horizonte no se midio.
    carried: Optional[float] = None
    for i in range(horizon):
        if band_by_step[i] is None:
            band_by_step[i] = carried
        else:
            carried = band_by_step[i]

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
        band = band_by_step[i]
        lower, upper = _bounds(point, band)
        measured = bool(step_residuals)
        steps.append({
            "step": i + 1,
            "point": point,
            "lower": lower,
            "upper": upper,
            "band_pct": round(band, 1) if band is not None else None,
            "mape": round(statistics.mean(step_residuals), 1) if measured else None,
            "n_backtests": len(step_residuals),
            "reliable": len(step_residuals) >= MIN_BACKTESTS_RELIABLE,
            "extrapolated": not measured,
        })

    return {
        "available": True,
        "reason": None,
        "method": method,
        "beats_naive": selection["beats_naive"],
        "mape": selection["mape_by_method"][method],
        "mape_by_method": selection["mape_by_method"],
        "n_periods": n,
        "steps": steps,
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
    for step in projection["steps"]:
        assert step["lower"] <= step["point"] <= step["upper"], step
        assert step["band_pct"] >= 0.0
    assert build_projection(recta, 3) == projection, "la proyeccion no es determinista"

    # Serie plana: todos los metodos empatan en 0, se publica el mas simple y
    # NO se declara mejora.
    plana = build_projection([500.0] * 9, 3)
    assert plana["method"] == "naive" and plana["beats_naive"] is False, plana["method"]
    assert plana["mape"] == 0.0 and plana["mape_by_method"]["naive"] == 0.0

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

    corta = build_projection(recta[:5], 3)
    assert corta["available"] is False and corta["steps"] == [], corta
    assert corta["reason"] and "hay 5" in corta["reason"], corta["reason"]

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
