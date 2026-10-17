"""Proyecciones ML: que metodo se publica, con que banda y con que numeros.

Por que estos casos y no otros
------------------------------
`ml_models` agrega tres metodos al naive del forecast existente, y todo el valor
del modulo esta en UNA decision: cual se publica. Estos casos fijan las dos
direcciones de esa decision (una tendencia real se publica como tendencia; una
serie plana no se declara mejora), el backtest con la cuenta hecha a mano, y la
herencia de banda cuando el horizonte es mas largo que la serie.

Las reglas que valen para TODA serie viven en `test_ml_models_properties.py`;
aca van los numeros concretos, que son los unicos que se pueden recalcular a mano
y comparar contra el codigo.
"""

import math
import statistics
from decimal import Decimal

import pytest

from app.modules.chat_engine.forecast_calculator import MIN_PERIODS
from app.modules.ml_forecast.ml_models import (
    METHODS,
    MIN_BACKTESTS_RELIABLE,
    build_projection,
    select_model,
    walk_forward,
)

# La misma serie de `test_prediction_honesty.py`: 9 meses de `movimientos_maxi3d`
# con el quiebre de mayo. Es la serie donde el naive ya gano una vez.
SERIE_9 = [9_761_026, 8_524_173, 7_319_140, 11_712_796, 17_599_629,
           20_179_434, 23_380_686, 20_237_007, 22_545_032]

RECTA_8 = [10, 20, 30, 40, 50, 60, 70, 80]
ALTERNA_9 = [10, 20, 10, 20, 10, 20, 10, 20, 10]
PLANA_9 = [500.0] * 9


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
    # int, str, bool y None no pueden ser NaN ni inf.


class TestMetodoPublicado:
    """La eleccion sale del backtest, no de una lista de preferencias."""

    def test_una_tendencia_real_se_publica_como_tendencia(self):
        """Recta exacta: la tendencia tiene error 0 y el naive no puede empatar."""
        out = build_projection([float(v) for v in RECTA_8], 3)
        assert out["available"] is True
        assert out["method"] == "ols_trend"
        assert out["beats_naive"] is True
        assert out["mape"] == 0.0
        # El naive predice el valor anterior: sobre una serie que sube 10 por
        # periodo eso es un error de dos digitos.
        assert out["mape_by_method"]["naive"] > 0.0
        assert out["mape_by_method"]["ols_trend"] < out["mape_by_method"]["naive"]
        assert set(out["mape_by_method"]) == set(METHODS)
        assert out["n_periods"] == 8

    def test_serie_plana_no_se_publica_como_mejora(self):
        """Todos los metodos empatan en 0%: gana el mas simple y NO se declara mejora.

        Es la frontera entre "un modelo mejor" y "un modelo distinto": sin la
        regla, una recta horizontal se presentaria como una mejora sobre el
        ultimo valor.
        """
        out = build_projection(PLANA_9, 3)
        assert out["available"] is True
        assert out["method"] == "naive"
        assert out["beats_naive"] is False
        assert out["mape"] == 0.0
        assert out["mape_by_method"] == {method: 0.0 for method in METHODS}

    def test_el_publicado_es_el_del_menor_mape(self):
        """Y sobre la serie real el ganador sigue siendo el naive."""
        sel = select_model([float(v) for v in SERIE_9], 3)
        assert set(sel) == {
            "method", "beats_naive", "mape_by_method", "mape_per_step", "n_backtests_per_step",
        }
        assert sel["method"] == "naive" and sel["beats_naive"] is False
        medibles = [v for v in sel["mape_by_method"].values() if v is not None]
        assert sel["mape_by_method"][sel["method"]] == min(medibles)
        # 9 puntos y horizonte 3: origenes t=6,7,8, y el origen t solo puede medir
        # el paso j si t+j-1 <= 8. Queda 3, 2 y 1 backtest por paso.
        assert sel["n_backtests_per_step"] == [3, 2, 1]
        assert len(sel["mape_per_step"]) == 3


class TestBacktest:
    """Los residuos por paso, que es de donde sale todo lo demas."""

    def test_walk_forward_naive_caso_fijo(self):
        """Residuos del naive paso por paso, con la cuenta escrita al lado.

        values = [10, 20, 10, 20, 10, 20, 10, 20, 10], horizon = 3.
        Origenes t = MIN_PERIODS = 6 .. 8.

          t=6: la historia [:6] termina en 20, asi que predice 20 para los tres
               pasos. paso 1: |20-10|/10 = 100%; paso 2: |20-20|/20 = 0%;
               paso 3: |20-10|/10 = 100%.
          t=7: la historia [:7] termina en 10. paso 1: |10-20|/20 = 50%;
               paso 2: |10-10|/10 = 0%.
          t=8: la historia [:8] termina en 20. paso 1: |20-10|/10 = 100%.

        Queda [[100, 50, 100], [0, 0], [100]]: dos residuos en el paso 2 y uno en
        el paso 3 porque el periodo de esos pasos todavia no ocurrio.
        """
        res = walk_forward([float(v) for v in ALTERNA_9], 3)
        assert res["naive"] == [[100.0, 50.0, 100.0], [0.0, 0.0], [100.0]]
        assert set(res) == set(METHODS)
        # Exactamente `horizon` listas por metodo, incluidas las que quedan vacias.
        assert all(len(pasos) == 3 for pasos in res.values())
        largo = walk_forward([float(v) for v in ALTERNA_9], 12)
        assert all(len(pasos) == 12 for pasos in largo.values())
        assert all(pasos == [] for pasos in largo["naive"][3:]), "sin actual no hay residuo"

    def test_un_metodo_sin_backtest_no_se_publica(self):
        """Sin residuos no hay banda ni eleccion: se declara, no se devuelve 0%."""
        out = build_projection([0.0] * 9, 3)
        assert out["available"] is False
        assert out["reason"] and "residuo" in out["reason"]
        assert out["steps"] == []
        assert walk_forward([0.0] * 9, 3) == {
            method: [[], [], []] for method in METHODS
        }


class TestBandaPorPaso:
    """La banda no es un parametro: es el error que la serie ya mostro."""

    def test_banda_por_paso_con_la_cuenta_a_mano(self):
        """9 puntos y 12 pasos: 3 medidos y 9 heredados.

        Cuenta a mano de la banda del naive sobre SERIE_9 (la misma que hace
        `forecast_next_period`: cuantil 80 con piso en el peor residuo):
          paso 1: residuos 13,7% / 15,5% / 10,2% -> cuantil 80 = 14,8%, piso = el
                  peor = 15,5%
          paso 2: residuos 0,3% / 3,7% -> cuantil 80 = 3,0%, piso = 3,7%
          paso 3: un solo residuo, 10,5% -> la banda ES ese residuo
        """
        out = build_projection([float(v) for v in SERIE_9], 12)
        assert out["available"] is True
        assert out["method"] == "naive"
        assert [s["step"] for s in out["steps"]] == list(range(1, 13))
        assert [s["band_pct"] for s in out["steps"][:3]] == [15.5, 3.7, 10.5]
        assert [s["n_backtests"] for s in out["steps"][:3]] == [3, 2, 1]

        residuos = walk_forward([float(v) for v in SERIE_9], 12)[out["method"]]
        for step in out["steps"]:
            assert step["lower"] <= step["point"] <= step["upper"], step
            assert step["band_pct"] >= 0.0
            if step["n_backtests"]:
                # La banda nunca es mas ajustada que el peor error de ESE paso.
                # 0.05 es medio paso del redondeo con que se publica la banda:
                # comparar contra el valor crudo seria exigir que redondear no
                # redondee.
                assert step["band_pct"] >= max(residuos[step["step"] - 1]) - 0.05, step
                assert step["extrapolated"] is False
                assert step["mape"] is not None
                assert step["step"] <= 3, "con 9 puntos ningun paso mas alla del 3 tiene actual"

    def test_los_pasos_sin_backtest_heredan_la_banda_y_lo_declaran(self):
        """Del paso 4 en adelante el actual no existe: banda heredada y marcada.

        Publicarlos sin marca, o con banda 0, seria declarar medida una banda que
        nadie midio.
        """
        out = build_projection([float(v) for v in SERIE_9], 12)
        ultimo_medido = [s for s in out["steps"] if s["n_backtests"] > 0][-1]
        assert ultimo_medido["step"] == 3
        heredados = out["steps"][3:]
        assert len(heredados) == 9
        for step in heredados:
            assert step["extrapolated"] is True
            assert step["reliable"] is False
            assert step["n_backtests"] == 0
            assert step["mape"] is None
            assert step["band_pct"] == ultimo_medido["band_pct"] == 10.5

    def test_la_confiabilidad_pide_cuatro_backtests(self):
        """Con 3 residuos el cuantil 80 es practicamente el maximo: se publica, no se declara confiable."""
        out = build_projection([float(v) for v in SERIE_9], 12)
        assert max(s["n_backtests"] for s in out["steps"]) < MIN_BACKTESTS_RELIABLE
        assert all(s["reliable"] is False for s in out["steps"])


class TestMapePorPaso:
    """El error publicado es el de ESE paso, no un promedio global."""

    def test_serie_plana_mape_por_paso_cero(self):
        """Plana: el naive acierta todos los pasos, asi que el MAPE de cada uno es 0."""
        out = build_projection(PLANA_9, 3)
        sel = select_model(PLANA_9, 3)
        assert out["method"] == "naive"
        assert [s["mape"] for s in out["steps"]] == [0.0, 0.0, 0.0]
        assert [s["n_backtests"] for s in out["steps"]] == [3, 2, 1]
        assert sel["mape_per_step"] == [0.0, 0.0, 0.0]
        assert sel["n_backtests_per_step"] == [3, 2, 1]
        # El MAPE del payload es el de TODOS los residuos del metodo publicado.
        assert out["mape"] == 0.0 == sel["mape_by_method"]["naive"]

    def test_el_mape_del_paso_sale_de_sus_residuos(self):
        out = build_projection([float(v) for v in SERIE_9], 3)
        residuos = walk_forward([float(v) for v in SERIE_9], 3)[out["method"]]
        for step in out["steps"]:
            propios = residuos[step["step"] - 1]
            assert propios, "con 9 puntos y horizonte 3 todo paso tiene residuo"
            assert step["n_backtests"] == len(propios)
            assert step["mape"] == pytest.approx(statistics.mean(propios), abs=0.05)
        # Los pasos no comparten numero entre si: si el modulo publicara el MAPE
        # global en cada paso, estos tres valores serian iguales entre si.
        assert len({s["mape"] for s in out["steps"]}) > 1


class TestSinNumerosInventados:
    """Lo que no se puede calcular es None; nunca 0, NaN ni una excepcion."""

    def test_ninguna_salida_lleva_nan_ni_inf(self):
        """Un NaN en el payload no es un numero raro: es JSON invalido."""
        casos = (
            [float(v) for v in SERIE_9] + [22_000_000.0],
            [0.0] * 12,
            [1.0] * MIN_PERIODS,
            [1e12, 1.0, 1e12, 1.0, 1e12, 1.0],
            [],
        )
        for serie in casos:
            for value in _floats(build_projection(serie, 4)):
                assert math.isfinite(value), serie

    def test_serie_corta_no_publica_nada_y_dice_cuanto_hay(self):
        for n in range(0, MIN_PERIODS):
            out = build_projection([10.0] * n, 3)
            assert out["available"] is False
            assert out["reason"]
            assert out["steps"] == []
            assert out["method"] is None and out["mape"] is None and out["beats_naive"] is None
            assert out["n_periods"] == n
        # El motivo dice cuanto hay: es lo que permite accionar ("falta un
        # periodo") en vez de un "insuficiente" que no se puede resolver.
        assert "hay 5" in build_projection([10.0] * 5, 3)["reason"]

    def test_horizonte_invalido_es_error_de_llamada(self):
        """Un horizonte de 0 pasos no es una proyeccion que no se pudo calcular."""
        for bad in (0, -1, -12):
            with pytest.raises(ValueError, match="horizon"):
                build_projection([100.0] * 9, bad)

    def test_serie_con_ceros_no_explota(self):
        """Sin denominador positivo no hay residuo porcentual: se declara, no se divide por cero."""
        out = build_projection([0.0] * 9, 3)
        assert out["available"] is False and out["reason"]
        assert out["steps"] == []

    def test_serie_con_ceros_intercalados_no_explota(self):
        """Los pasos cuyo actual es 0 no aportan residuo: no se inventa un 0%."""
        out = build_projection([10.0, 20.0, 0.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0], 3)
        for value in _floats(out):
            assert math.isfinite(value)
        if out["available"]:
            assert out["steps"][0]["point"] is not None
            assert all(s["band_pct"] is not None for s in out["steps"])
        else:
            assert out["reason"]

    def test_valor_no_numerico_o_no_finito_da_motivo_no_excepcion(self):
        con_none = build_projection([100.0, 200.0, None, 400.0, 500.0, 600.0], 3)
        assert con_none["available"] is False and con_none["reason"]
        assert con_none["steps"] == []
        con_texto = build_projection([100.0, 200.0, "300", 400.0, 500.0, 600.0], 3)
        assert con_texto["available"] is False and con_texto["reason"]
        # NaN e inf no son "un dato raro": contagian el calculo entero.
        for raro in (float("nan"), float("inf"), float("-inf")):
            out = build_projection([100.0, 200.0, raro, 400.0, 500.0, 600.0], 3)
            assert out["available"] is False, raro
            assert out["reason"] and out["steps"] == []


class TestTiposDePostgres:
    """La misma serie tiene que dar el mismo numero, venga como venga."""

    def test_int_float_y_decimal_dan_la_misma_proyeccion(self):
        """`SUM()` de Postgres devuelve `numeric`, que llega como `Decimal` crudo.

        Si los tres caminos dieran distinto, dos series identicas darian dos
        bandas distintas segun como llegaron los datos: el mismo numero
        dependeria del camino y no del dato.
        """
        como_int = build_projection(list(SERIE_9), 3)
        como_float = build_projection([float(v) for v in SERIE_9], 3)
        como_decimal = build_projection([Decimal(str(v)) for v in SERIE_9], 3)

        for out in (como_int, como_float, como_decimal):
            assert out["available"] is True
        assert como_int["method"] == como_float["method"] == como_decimal["method"]
        assert como_int["mape"] == como_float["mape"] == como_decimal["mape"]
        assert como_int["beats_naive"] == como_decimal["beats_naive"]
        # Punto y banda, paso por paso: es donde un Decimal mal normalizado se
        # nota, porque el punto y la banda salen de caminos distintos.
        assert [s["point"] for s in como_int["steps"]] == [s["point"] for s in como_decimal["steps"]]
        assert [s["band_pct"] for s in como_int["steps"]] == [s["band_pct"] for s in como_decimal["steps"]]
        assert como_float["steps"][0]["point"] == como_decimal["steps"][0]["point"]
