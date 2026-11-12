"""Metricas robustas y seleccion por MASE, sobre series concretas.

Por que estos casos y no otros
------------------------------
`test_ml_models.py` fija el contrato historico (MAPE, banda, total). Este archivo
fija lo nuevo del plan 010: la comparacion de modelos publicada
(`metrics_by_method`, `model_comparison`), la seleccion por MASE cuando hay
escala naive y las invariantes que tienen que valer para CUALQUIER serie (MASE no
negativo, MAE <= RMSE, exactamente un `selected`).

`DISCREPA_9` es el caso que prueba que la seleccion no es MAPE disfrazado: por
MAPE gana naive (121,9 contra 148,6 de la media), pero por MASE gana la media
(0,77 contra 1,141). Si alguien vuelve a elegir por MAPE, ese test falla.

Las propiedades sobre TODO el dominio viven en `test_ml_models_properties.py`;
aca van los numeros que se pueden recalcular a mano y comparar con el codigo.
"""

import math
import statistics
from decimal import Decimal

import pytest

from app.modules.chat_engine.forecast_calculator import MIN_PERIODS
from app.modules.ml_forecast.ml_models import (
    EXPLORATORY_MAPE,
    METHODS,
    METHOD_DESCRIPTIONS,
    METHOD_LABELS,
    MIN_BACKTESTS_RELIABLE,
    UNRELIABLE_MAPE,
    build_projection,
    select_model,
    walk_forward,
)
from app.modules.ml_forecast.policy import POLICY

# La misma serie real de los otros tests: 9 meses con el quiebre de mayo.
SERIE_9 = [9_761_026, 8_524_173, 7_319_140, 11_712_796, 17_599_629,
           20_179_434, 23_380_686, 20_237_007, 22_545_032]

RECTA_8 = [10, 20, 30, 40, 50, 60, 70, 80]
PLANA_9 = [500.0] * 9
ALTERNA_9 = [10, 20, 10, 20, 10, 20, 10, 20, 10]

# MAPE y MASE eligen distinto (ver el docstring del archivo).
DISCREPA_9 = [39, 32, 24, 90, 100, 32, 11, 74, 39]

# El actual del origen t=6 es 0: el residuo porcentual de ese paso no existe, el
# error absoluto si. El paso 1 publica n_backtests=2 y MAE sobre 3 errores.
CERO_ACTUAL_9 = [10, 20, 30, 40, 50, 60, 0, 80, 90]


def _floats(node):
    """Todos los `float` de la estructura, para poder afirmar que ninguno es NaN."""
    if isinstance(node, float):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _floats(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _floats(value)


def _primaria(out):
    """Metrica de seleccion que corresponde al payload (MASE si hay escala)."""
    completo = all(out["metrics_by_method"][m]["mase"] is not None for m in METHODS)
    return "mase" if completo else "mape"


class TestMetricasPorMetodo:
    def test_recta_exacta_tiene_error_cero_en_las_tres_metricas(self):
        out = build_projection([float(v) for v in RECTA_8], 3)
        assert out["available"] is True
        assert out["method"] == "ols_trend"
        assert out["mape"] == out["mae"] == out["rmse"] == out["mase"] == 0.0
        # La escala naive de una recta que sube 10 es 10: el MASE 0 no es un
        # None disfrazado.
        assert out["metrics_by_method"]["naive"]["mase"] == 1.333

    def test_plana_no_inventa_mase_sin_escala_naive(self):
        """Sin movimiento no hay denominador: MASE es None, no 0."""
        out = build_projection(PLANA_9, 3)
        assert out["available"] is True
        assert out["method"] == "naive" and out["beats_naive"] is False
        assert out["mape"] == out["mae"] == out["rmse"] == 0.0
        assert out["mase"] is None
        assert all(m["mase"] is None for m in out["metrics_by_method"].values())
        assert all(item["mase"] is None for item in out["model_comparison"])

    def test_alternante_publica_mae_rmse_y_mase(self):
        """9 puntos, horizonte 3: la media gana por MASE (0,5 contra 0,667)."""
        out = build_projection(ALTERNA_9, 3)
        assert out["available"] is True
        assert out["method"] == "mean" and out["beats_naive"] is True
        assert (out["mape"], out["mae"], out["rmse"], out["mase"]) == (41.1, 5.0, 5.02, 0.5)
        assert out["metrics_by_method"]["naive"] == {
            "mape": 58.3, "mae": 6.67, "rmse": 8.16, "mase": 0.667,
        }
        assert out["metrics_by_method"]["ols_trend"]["mase"] == 0.6

    def test_el_mase_es_el_mae_sobre_la_media_de_diferencias(self):
        """La formula es la del contrato: MAE / media(|y_t - y_{t-1}|)."""
        serie = [float(v) for v in DISCREPA_9]
        scale = statistics.mean(abs(b - a) for a, b in zip(serie, serie[1:]))
        out = build_projection(serie, 1)
        for method in METHODS:
            metricas = out["metrics_by_method"][method]
            assert metricas["mase"] == pytest.approx(metricas["mae"] / scale, abs=0.001), (
                method, metricas, scale
            )

    def test_cada_paso_publica_sus_propias_metricas(self):
        """Los pasos medidos no comparten MAE/MASE: si lo hicieran seria el global."""
        out = build_projection([float(v) for v in SERIE_9], 3)
        for step in out["steps"]:
            assert step["mae"] is not None and step["rmse"] is not None
            assert step["mase"] is not None
            assert step["mae"] <= step["rmse"], step
            assert step["mase"] >= 0.0, step
        assert len({step["mae"] for step in out["steps"]}) > 1
        assert len({step["mase"] for step in out["steps"]}) > 1


class TestSeleccion:
    def test_elige_por_mase_aunque_el_mape_prefiera_otro(self):
        """DISCREPA_9: naive tiene mejor MAPE y peor MAE/MASE.

        El contrato pide MASE cuando hay escala; publicar naive aca seria volver
        a elegir por MAPE sin decirlo.
        """
        out = build_projection(DISCREPA_9, 1)
        assert out["available"] is True
        metodos = out["metrics_by_method"]
        assert metodos["naive"]["mape"] < metodos["mean"]["mape"]
        assert metodos["mean"]["mase"] < metodos["naive"]["mase"]
        assert out["method"] == "mean"
        assert out["beats_naive"] is True

    def test_beats_naive_exige_mejora_estricta_en_la_metrica_de_seleccion(self):
        for serie, horizonte in ((SERIE_9, 3), (RECTA_8, 3), (ALTERNA_9, 3),
                                 (PLANA_9, 3), (DISCREPA_9, 1)):
            out = build_projection(serie, horizonte)
            if not out["available"]:
                continue
            assert out["beats_naive"] is (out["method"] != "naive"), out["method"]
            if out["method"] == "naive":
                continue
            primaria = _primaria(out)
            propio = out["metrics_by_method"][out["method"]][primaria]
            naive = out["metrics_by_method"]["naive"][primaria]
            assert naive is not None and propio < naive, (serie, primaria, out)

    def test_una_serie_plana_no_se_declara_mejora(self):
        """Sin escala y con todo en 0%, el empate se resuelve por el orden."""
        out = build_projection(PLANA_9, 3)
        assert out["method"] == "naive" and out["beats_naive"] is False
        assert [item["method"] for item in out["model_comparison"]] == list(METHODS)


class TestContratoPublicado:
    def test_todos_los_metodos_tienen_etiqueta_y_descripcion(self):
        for method in METHODS:
            assert METHOD_LABELS.get(method), method
            assert METHOD_DESCRIPTIONS.get(method), method

    def test_model_comparison_marca_exactamente_un_seleccionado(self):
        for serie, horizonte in ((SERIE_9, 3), (RECTA_8, 3), (ALTERNA_9, 3),
                                 (PLANA_9, 3), (DISCREPA_9, 1), (CERO_ACTUAL_9, 3)):
            out = build_projection(serie, horizonte)
            assert out["available"] is True, serie
            assert len(out["model_comparison"]) == len(METHODS)
            assert sum(1 for item in out["model_comparison"] if item["selected"]) == 1, out
            assert [item["method"] for item in out["model_comparison"]] == list(METHODS)
            assert [item["method"] for item in out["model_comparison"]
                    if item["selected"]] == [out["method"]]

    def test_metrics_by_method_y_comparison_publican_el_mismo_numero(self):
        out = build_projection([float(v) for v in SERIE_9], 6)
        for item in out["model_comparison"]:
            metricas = out["metrics_by_method"][item["method"]]
            assert {k: item[k] for k in ("mape", "mae", "rmse", "mase")} == metricas
        publicado = out["metrics_by_method"][out["method"]]
        assert (out["mape"], out["mae"], out["rmse"], out["mase"]) == (
            publicado["mape"], publicado["mae"], publicado["rmse"], publicado["mase"],
        )
        # La clave historica sigue siendo el MAPE por metodo, con los mismos
        # numeros que `metrics_by_method`.
        assert out["mape_by_method"] == {
            method: out["metrics_by_method"][method]["mape"] for method in METHODS
        }

    def test_cada_metodo_publica_etiqueta_y_descripcion(self):
        out = build_projection([float(v) for v in SERIE_9], 3)

        assert out["method_label"] == METHOD_LABELS[out["method"]]
        assert out["method_description"] == METHOD_DESCRIPTIONS[out["method"]]
        assert all(
            item["label"] == METHOD_LABELS[item["method"]]
            for item in out["model_comparison"]
        )

        sin_proyeccion = build_projection([], 3)
        assert sin_proyeccion["method_label"] is None
        assert sin_proyeccion["method_description"] is None

    def test_serie_corta_declara_las_claves_nuevas(self):
        for n in range(0, MIN_PERIODS):
            out = build_projection([10.0] * n, 3)
            assert out["available"] is False and out["reason"]
            assert out["mae"] is out["rmse"] is out["mase"] is None
            assert out["model_comparison"] == []
            assert set(out["metrics_by_method"]) == set(METHODS)
            assert all(
                metricas == {"mape": None, "mae": None, "rmse": None, "mase": None}
                for metricas in out["metrics_by_method"].values()
            )

    def test_serie_de_ceros_declara_metricas_vacias(self):
        """Sin denominador positivo no hay banda; las claves nuevas igual existen."""
        out = build_projection([0.0] * 9, 3)
        assert out["available"] is False and out["steps"] == []
        assert out["model_comparison"] == []
        assert out["mae"] is None and out["rmse"] is None and out["mase"] is None
        assert all(v is None for v in out["mape_by_method"].values())

    def test_el_payload_y_select_model_son_deterministas(self):
        for serie, horizonte in ((SERIE_9, 3), (DISCREPA_9, 1), (CERO_ACTUAL_9, 3)):
            primera = build_projection(serie, horizonte)
            segunda = build_projection(serie, horizonte)
            assert primera == segunda, serie
            assert select_model(serie, horizonte) == select_model(serie, horizonte), serie


class TestTiposDePostgres:
    def test_int_float_y_decimal_dan_el_mismo_payload(self):
        como_int = build_projection(list(SERIE_9), 3)
        como_float = build_projection([float(v) for v in SERIE_9], 3)
        como_decimal = build_projection([Decimal(str(v)) for v in SERIE_9], 3)
        assert como_int == como_float == como_decimal


class TestInvariantes:
    def test_mase_no_negativo_y_mae_no_supera_rmse(self):
        casos = (
            [float(v) for v in SERIE_9],
            [float(v) for v in RECTA_8],
            ALTERNA_9,
            [float(v) for v in DISCREPA_9],
            [float(v) for v in CERO_ACTUAL_9],
            [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            [1e12, 1.0, 1e12, 1.0, 1e12, 1.0],
        )
        for serie in casos:
            for horizonte in (1, 3, 6, 12):
                out = build_projection(serie, horizonte)
                for valor in _floats(out):
                    assert math.isfinite(valor), (serie, horizonte, out)
                for method in METHODS:
                    metricas = out["metrics_by_method"][method]
                    if metricas["mase"] is not None:
                        assert metricas["mase"] >= 0.0, (serie, method, metricas)
                    if metricas["mae"] is not None:
                        assert metricas["rmse"] is not None, (serie, method, metricas)
                        assert metricas["mae"] <= metricas["rmse"], (serie, method, metricas)
                for step in out["steps"]:
                    if step["mae"] is not None:
                        assert step["mae"] <= step["rmse"], step
                        assert step["mase"] is None or step["mase"] >= 0.0, step

    def test_un_actual_en_cero_entra_al_mae_aunque_no_al_mape(self):
        """El paso 1 de CERO_ACTUAL_9: 2 residuos porcentuales, 3 errores absolutos.

        El origen con actual 0 no puede aportar porcentaje, pero su error
        absoluto (70) es real y entra en MAE = (70 + 40 + 27,5) / 3.
        """
        out = build_projection(CERO_ACTUAL_9, 3)
        assert out["available"] is True
        step = out["steps"][0]
        assert step["n_backtests"] == 2
        assert step["mape"] == 40.3
        assert step["mae"] == 45.83
        # El MAPE del metodo es 26,7 y la media de sus residuos porcentuales
        # tambien, pero el MAE incluye el origen que el porcentaje no puede medir.
        assert out["metrics_by_method"]["ols_trend"]["mae"] == 30.83
        assert out["metrics_by_method"]["ols_trend"]["mape"] == 26.7

        # `walk_forward` conserva su contrato: el paso 1 solo tiene los residuos
        # con denominador positivo.
        residuos = walk_forward(CERO_ACTUAL_9, 3)["ols_trend"]
        assert len(residuos[0]) == 2


class TestPoliticaUnica:
    def test_los_aliases_salen_de_policy(self):
        """Los umbrales ya no se duplican: el alias es la politica."""
        assert MIN_BACKTESTS_RELIABLE == POLICY.reliable_backtests
        assert UNRELIABLE_MAPE == POLICY.unreliable_mape
        assert EXPLORATORY_MAPE == POLICY.exploratory_mape
