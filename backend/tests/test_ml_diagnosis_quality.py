"""Calidad del diagnostico: granularidades, evidencia y defectos de la serie.

Que se verifica aca
-------------------
`diagnose_series` tiene que sostener la misma lectura en cualquier
granularidad y no afirmar mas de lo que la historia permite:

1. El ciclo estacional cambia con la granularidad (dia, semana, mes, trimestre)
   y se mide con los umbrales de `POLICY`.
2. La tendencia exige significancia, no solo una pendiente distinta de cero.
3. Un valor atipico se detecta contra la mediana, no contra la media.
4. Un `NULL` no es un cero: deja un hueco y no mueve el promedio.
5. Los horizontes publicados salen del largo de la serie, no de una tabla fija.
6. Con uno o dos puntos no se afirma nada.
"""

from datetime import date, timedelta

import pytest

from app.modules.ml_forecast.diagnosis import diagnose_series
from app.modules.ml_forecast.policy import POLICY

JERGA = ("mape", "backtest", "naive", "banda")


def _sin_jerga(texto):
    for termino in JERGA:
        assert termino not in texto.lower(), (termino, texto)


def _months(count, start_year=2022):
    year, month, periods = start_year, 1, []
    for _ in range(count):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return periods


def _quarters(count, start_year=2022):
    return [
        f"{start_year + index // 4:04d}-Q{index % 4 + 1}"
        for index in range(count)
    ]


def _weeks(count, start=date(2023, 1, 2)):
    periods = []
    for index in range(count):
        iso = (start + timedelta(weeks=index)).isocalendar()
        periods.append(f"{iso.year:04d}-W{iso.week:02d}")
    return periods


def _days(count, start=date(2024, 1, 1)):
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def test_mensual_detecta_el_ciclo_anual():
    values = [1600.0 if index % 12 == 0 else 1000.0 for index in range(36)]

    out = diagnose_series(values, _months(36), horizon=6)

    assert out["seasonality"] == "posible_estacionalidad"
    assert out["season_length"] == 12
    assert out["seasonality_strength"] >= POLICY.seasonality_min_eta2
    assert out["seasonality_strength"] <= 1.0
    assert "Se repite un patrón cada año" in out["summary"]
    _sin_jerga(out["summary"])


def test_trimestral_detecta_el_ciclo_de_cuatro_trimestres():
    values = [1600.0 if index % 4 == 3 else 1000.0 for index in range(12)]

    out = diagnose_series(
        values, _quarters(12), horizon=4, granularity="quarter"
    )

    assert out["seasonality"] == "posible_estacionalidad"
    assert out["season_length"] == 4
    assert out["seasonality_strength"] >= POLICY.seasonality_min_eta2
    assert "algunos trimestres" in out["summary"]
    _sin_jerga(out["summary"])


def test_semanal_detecta_el_ciclo_del_mes_del_ano():
    start = date(2023, 1, 2)
    values = [
        1600.0 if (start + timedelta(weeks=index)).month == 12 else 1000.0
        for index in range(104)
    ]

    out = diagnose_series(
        values, _weeks(104), horizon=8, granularity="week"
    )

    assert out["seasonality"] == "posible_estacionalidad"
    assert out["season_length"] == 12
    assert out["seasonality_strength"] >= POLICY.seasonality_min_eta2
    assert "a lo largo del año" in out["summary"]


def test_diario_detecta_el_ciclo_de_la_semana():
    start = date(2024, 1, 1)
    values = [
        1500.0 if (start + timedelta(days=index)).weekday() < 5 else 1000.0
        for index in range(28)
    ]

    out = diagnose_series(values, _days(28), horizon=7, granularity="day")

    assert out["seasonality"] == "posible_estacionalidad"
    assert out["season_length"] == 7
    assert out["seasonality_strength"] >= POLICY.seasonality_min_eta2
    assert "cada semana" in out["summary"]


def test_serie_plana_no_afirma_tendencia_ni_patron():
    out = diagnose_series([1000.0] * 36, _months(36), horizon=6)

    assert out["trend"] == "estable"
    assert out["trend_pct_per_step"] == 0.0
    assert out["seasonality"] == "sin_estacionalidad_clara"
    assert out["seasonality_strength"] == 0.0
    assert out["outlier_count"] == 0
    assert out["level_shift"] is False
    assert out["missing_periods"] == 0
    assert out["usable_periods"] == 36
    assert out["warnings"] == []


def test_tendencia_exige_significancia_y_publica_el_paso():
    values = [1000.0 + 50.0 * index for index in range(24)]
    out = diagnose_series(values, _months(24), horizon=6)

    assert out["trend"] == "creciente"
    assert out["trend_pct_per_step"] == pytest.approx(3.17, abs=0.01)
    # Una recta perfecta no deja variacion residual: no se disfraza de patron.
    assert out["seasonality"] == "sin_estacionalidad_clara"
    assert out["seasonality_strength"] == 0.0
    assert out["level_shift"] is False

    # Deriva chica: significativa para el estadistico, pero no para el negocio.
    deriva = [1000.0 + (index % 2) * 100 + 0.5 * index for index in range(24)]
    chica = diagnose_series(deriva, _months(24), horizon=6)
    assert chica["trend"] == "estable"
    assert abs(chica["trend_pct_per_step"]) < POLICY.trend_threshold_pct


def test_escalon_reciente_se_reporta_como_cambio_de_nivel():
    values = [1000.0] * 21 + [1500.0] * 3

    out = diagnose_series(values, _months(24), horizon=6)

    assert out["level_shift"] is True
    assert out["level_shift_pct"] == pytest.approx(50.0, abs=0.1)
    assert any("nivel" in warning for warning in out["warnings"])
    # Un escalon contamina el ultimo ciclo: no se afirma ademas un patron.
    assert out["seasonality"] == "sin_estacionalidad_clara"
    assert out["seasonality_strength"] == 0.0


def test_outlier_robusto_usa_la_mediana():
    values = [1000.0] * 35 + [5000.0]

    out = diagnose_series(values, _months(36), horizon=6)

    assert out["outlier_count"] == 1
    assert out["last_vs_mean_pct"] == pytest.approx(350.0, abs=0.1)
    assert any("atípico" in warning for warning in out["warnings"])
    # El pico no fabrica estacionalidad: la mediana del grupo no se mueve.
    assert out["seasonality"] == "sin_estacionalidad_clara"
    _sin_jerga(out["summary"])


def test_un_null_no_es_un_cero():
    values = [1000.0, None, 1000.0, 1000.0]
    periods = _months(4)

    out = diagnose_series(values, periods, horizon=3)

    # El NULL se descarta: quedan tres puntos y la media no cae a 750.
    assert out["usable_periods"] == 3
    assert out["last_vs_mean_pct"] == 0.0
    assert out["outlier_count"] == 0
    # El periodo sin valor queda como hueco, no como valor cero.
    assert out["missing_periods"] == 1


def test_huecos_se_cuentan_con_la_aritmetica_de_periodos():
    periods = [
        period for index, period in enumerate(_months(12))
        if index not in (4, 8)
    ]

    out = diagnose_series([1000.0] * 10, periods, horizon=6)

    assert out["missing_periods"] == 2
    assert out["usable_periods"] == 10
    assert any("huecos" in warning for warning in out["warnings"])


def test_periodos_no_parseables_no_afirman_huecos_ni_estacionalidad():
    periods = [f"mes-{index}" for index in range(24)]

    out = diagnose_series([1000.0 + index for index in range(24)], periods, horizon=6)

    assert out["seasonality"] == "insuficiente_historia"
    assert out["seasonality_strength"] is None
    assert out["season_length"] == 12
    assert out["missing_periods"] is None
    assert "no se pudieron leer" in out["summary"]


def test_uno_y_dos_puntos_no_afirman_nada():
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

    assert diagnose_series([1000.0], _months(1), horizon=6) == esperado
    assert diagnose_series(
        [1000.0, 1200.0], _months(2), horizon=6
    ) == esperado


def test_horizontes_derivados_son_coherentes():
    for n in (4, 5, 6, 10, 24, 36, 120):
        out = diagnose_series([1000.0] * n, _months(n), horizon=1)

        assert out["usable_periods"] == n
        assert out["max_recommended_horizon"] == POLICY.max_horizon_for(n)
        assert out["reliable_horizon"] == POLICY.reliable_horizon_for(n)
        assert 0 <= out["reliable_horizon"] <= out["max_recommended_horizon"]
        if n < POLICY.min_periods:
            assert out["max_recommended_horizon"] == 0
            assert out["reliable_horizon"] == 0
        else:
            assert out["max_recommended_horizon"] >= POLICY.min_recommended_horizon
