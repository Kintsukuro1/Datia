"""Diagnostico de series en lenguaje simple: el contrato completo, sin jerga.

Que se verifica aca
-------------------
`diagnosis.diagnose_series` es lo que la UI de Proyecciones muestra antes de
proyectar. El riesgo no es un numero mal calculado: es un numero bien calculado
explicado con palabras que el lector no puede usar. Por eso cada caso mira las
dos caras del contrato:

1. El texto no lleva MAPE, backtest, naive ni banda.
2. Los campos (`trend`, `cv_pct`, `seasonality`, `max_recommended_horizon`)
   dicen lo mismo que el texto.

Casos: serie estable, creciente, decreciente, con estacionalidad, con el ultimo
dato lejos de la media, historia corta, serie vacia y fechas ilegibles.
"""

import math

import pytest

from app.modules.ml_forecast.diagnosis import diagnose_series
from app.modules.ml_forecast.policy import POLICY

JERGA = ("mape", "backtest", "naive", "banda")


def _sin_jerga(texto):
    for termino in JERGA:
        assert termino not in texto.lower(), (termino, texto)


def _months(count, start_year=2023):
    """Periodos `YYYY-MM` consecutivos, para que el calendario sea legible."""
    year, month = start_year, 1
    periods = []
    for _ in range(count):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return periods


# 46 meses que alternan 964 y 1036: media 1000 y desvio poblacional 36, o sea
# CV 3,6%. La direccion de la serie es plana (la pendiente se cancela) y el mes
# mas alto contra el mas bajo da 1,07: no alcanza para hablar de estacionalidad.
ESTABLE = [964.0, 1036.0] * 23
PERIODOS_ESTABLE = _months(46)


def test_serie_estable_publica_el_diagnostico_en_palabras_simples():
    out = diagnose_series(ESTABLE, PERIODOS_ESTABLE, horizon=12)

    assert out["trend"] == "estable"
    assert out["cv_pct"] == pytest.approx(3.6, abs=0.05)
    assert out["last_vs_mean_pct"] == pytest.approx(3.6, abs=0.05)
    assert out["seasonality"] == "sin_estacionalidad_clara"
    assert out["season_length"] == 12
    # La alternacion de dos periodos encaja con meses pares, pero la diferencia
    # entre meses es del 7%: no es material y no se publica como patron.
    assert out["seasonality_strength"] == 0.0
    assert out["trend_pct_per_step"] == pytest.approx(0.04, abs=0.01)
    assert out["level_shift"] is False
    assert out["outlier_count"] == 0
    assert out["missing_periods"] == 0
    assert out["usable_periods"] == 46
    assert out["reliable_horizon"] == 15
    assert out["max_recommended_horizon"] == 15
    assert out["warnings"] == []
    assert out["summary"] == (
        "La serie es estable: se mueve alrededor de la media con una variación de 3,6%. "
        "No se ve una tendencia clara ni un patrón estacional definido. "
        "La historia alcanza para unos 15 pasos; más allá, el pronóstico se aleja de "
        "los datos disponibles."
    )
    _sin_jerga(out["summary"])
    assert set(out) == {
        "summary", "trend", "cv_pct", "last_vs_mean_pct",
        "seasonality", "season_length", "seasonality_strength",
        "trend_pct_per_step", "level_shift", "level_shift_pct",
        "outlier_count", "missing_periods", "usable_periods",
        "reliable_horizon", "warnings", "max_recommended_horizon",
    }


def test_serie_creciente():
    values = [1000.0 + 50.0 * index for index in range(24)]
    out = diagnose_series(values, _months(24), horizon=6)

    assert out["trend"] == "creciente"
    assert out["trend_pct_per_step"] == pytest.approx(3.17, abs=0.01)
    assert out["summary"].startswith("La serie viene subiendo")
    _sin_jerga(out["summary"])


def test_serie_decreciente():
    values = [2000.0 - 50.0 * index for index in range(24)]
    out = diagnose_series(values, _months(24), horizon=6)

    assert out["trend"] == "decreciente"
    assert out["trend_pct_per_step"] < 0
    assert out["summary"].startswith("La serie viene bajando")
    _sin_jerga(out["summary"])


def test_estacionalidad_con_el_mismo_mes_alto_cada_ano():
    values = [1600.0 if index % 12 == 0 else 1000.0 for index in range(36)]
    out = diagnose_series(values, _months(36), horizon=6)

    assert out["seasonality"] == "posible_estacionalidad"
    assert out["season_length"] == 12
    assert out["seasonality_strength"] >= POLICY.seasonality_min_eta2
    assert "Se repite un patrón cada año" in out["summary"]
    _sin_jerga(out["summary"])


def test_ultimo_dato_lejos_de_la_media_se_avisa_antes_de_proyectar():
    values = [1000.0] * 23 + [850.0]
    out = diagnose_series(values, _months(24), horizon=6)

    assert out["trend"] == "estable"
    assert out["last_vs_mean_pct"] == pytest.approx(-14.47, abs=0.05)
    assert out["outlier_count"] == 1
    assert "El último dato está 14,5% por debajo de la media" in out["summary"]
    assert any("conviene revisarlo" in warning for warning in out["warnings"])
    _sin_jerga(out["summary"])


def test_historia_corta_no_finge_estacionalidad():
    values = [1000.0] * 10
    out = diagnose_series(values, _months(10), horizon=6)

    assert out["seasonality"] == "insuficiente_historia"
    assert out["seasonality_strength"] is None
    assert out["max_recommended_horizon"] == 3
    assert out["reliable_horizon"] == 1
    assert "Todavía no hay historia suficiente" in out["summary"]
    assert any("pocos períodos" in warning for warning in out["warnings"])
    _sin_jerga(out["summary"])


def test_serie_vacia_devuelve_el_contrato_sin_inventar_nada():
    esperado = {
        "summary": "No hay suficientes datos para diagnosticar la serie.",
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
        "warnings": ["No hay suficientes datos para diagnosticar la serie."],
        "max_recommended_horizon": None,
    }
    assert diagnose_series([], [], horizon=6) == esperado
    # Todos los valores descartables es lo mismo que no tener serie.
    assert diagnose_series([None, "texto", math.nan, math.inf], [None] * 4, horizon=3) == esperado


def test_fechas_ilegibles_no_fingen_estacionalidad():
    values = [1000.0 + index for index in range(24)]
    out = diagnose_series(values, [f"mes-{index}" for index in range(24)], horizon=6)

    assert out["seasonality"] == "insuficiente_historia"
    assert out["seasonality_strength"] is None
    # El ciclo mensual se sigue publicando, pero no se afirma fuerza ni huecos.
    assert out["season_length"] == 12
    assert out["missing_periods"] is None
    assert "no se pudieron leer" in out["summary"]


def test_los_valores_invalidos_se_descartan_y_la_serie_se_alinea():
    values = [None, "abc", 100, 200, 300, math.nan, math.inf, 400]
    out = diagnose_series(values, _months(8), horizon=3)

    # Quedan 100, 200, 300 y 400: cuatro puntos, no ocho. Con menos de
    # `min_periods` no hay horizonte publicado, ni siquiera el minimo.
    assert out["last_vs_mean_pct"] == pytest.approx(60.0, abs=0.01)
    assert out["usable_periods"] == 4
    assert out["missing_periods"] == 2
    assert out["max_recommended_horizon"] == 0
    assert out["reliable_horizon"] == 0

    # `periods` mas corto que `values`: se alinean por posicion.
    corto = diagnose_series([100, 200, 300, 999], _months(3), horizon=6)
    assert corto["last_vs_mean_pct"] == pytest.approx(50.0, abs=0.01)
    assert corto["usable_periods"] == 3
    assert corto["missing_periods"] == 0
    assert corto["max_recommended_horizon"] == 0


def test_el_horizonte_pedido_se_avisa_cuando_la_historia_no_alcanza():
    dentro = diagnose_series(ESTABLE, PERIODOS_ESTABLE, horizon=15)
    assert dentro["warnings"] == []

    fuera = diagnose_series(ESTABLE, PERIODOS_ESTABLE, horizon=36)
    assert fuera["warnings"] == [
        "Pediste 36 pasos; la historia alcanza para ~15."
    ]

    # El camino "Auto" del pedido puede traer None: no hay pedido que avisar.
    auto = diagnose_series(ESTABLE, PERIODOS_ESTABLE, horizon=None)
    assert auto["warnings"] == []


def test_el_tope_de_pasos_recomendados_es_36():
    values = [1000.0] * 120
    out = diagnose_series(values, _months(120), horizon=36)

    assert out["max_recommended_horizon"] == 36
    assert out["reliable_horizon"] == 36
    assert out["warnings"] == []
