"""Propiedades de la proyeccion ML, sobre todo el dominio y no sobre 3 series.

Por que aca y no en `test_ml_models.py`
---------------------------------------
Ese archivo fija series concretas y afirma numeros concretos. Este deja que el
generador busque el contraejemplo de las reglas que el modulo promete en su
docstring:

  - BANDA: el intervalo contiene su propio punto, y el ancho nunca es mas
    ajustado que el peor residuo observado de ESE paso. Es lo que convierte el
    numero en una banda en vez de en una cifra con decimales.
  - DETERMINISMO: la misma serie da el mismo punto. Una proyeccion que cambia
    sola entre dos llamadas identicas no es una medicion, y no se puede auditar.
  - SIN NAN: ningun float de la salida es NaN ni inf. `json.dumps` los emitiria
    como `NaN` crudo, que no es JSON valido, y el error apareceria en el cliente
    en vez de en el que calculo.
  - AVAILABLE: `available: False` es una afirmacion ("no se"), y no puede salir
    junto a un paso publicado que la contradiga.
  - TOTAL: la suma de los puntos publicados, y su banda no es mas ajustada que
    el peor error acumulado del prefijo que declara haber medido.

`deadline=None` porque el backtest sobre una serie patologica puede tardar mas
que el plazo por defecto, y ese flake termina con el test borrado. Los ejemplos
estan contados para que el archivo entero corra en pocos segundos: cada
`build_projection` ajusta los metodos una vez por origen (hasta 18 por serie).
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.ml_forecast.ml_models import (
    METHODS,
    MIN_BACKTESTS_RELIABLE,
    MIN_PERIODS,
    _walk_residuals,
    build_projection,
    walk_forward,
)

# Series de positivos finitos, de 0 a 24 elementos: el dominio real (montos de
# periodos observados) y el piso de `MIN_PERIODS`. El 1e-9 no es decorativo: las
# series de negocio tienen valores chicos y ahi un error relativo se dispara, que
# es justo donde la banda tiene que seguir siendo finita.
series = st.lists(
    st.floats(min_value=1e-9, max_value=1e9, allow_nan=False, allow_infinity=False),
    min_size=0,
    max_size=24,
)

horizontes = st.integers(min_value=1, max_value=12)

# Ejemplos contados para que el archivo entero corra en menos de 30s (cada
# ejemplo ajusta Holt hasta 18 veces, y hay dos propiedades que piden dos
# recorridos completos).
POCOS = 20


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


class TestBandasDeLaProyeccion:

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_la_banda_contiene_el_punto(self, valores, horizonte):
        """`lower <= point <= upper` en cada paso con banda, y `band_pct >= 0`.

        Si la banda excluyera su propia estimacion, el lector veria un intervalo
        y una cifra, y el intervalo no incluiria la cifra: es el fallo mas grave
        posible en una proyeccion. Un ancho negativo, ademas, publicaria un
        techo debajo del piso.
        """
        out = build_projection(valores, horizonte)
        for step in out["steps"]:
            if step["band_pct"] is not None:
                assert step["band_pct"] >= 0.0, step
            if step["point"] is None or step["band_pct"] is None:
                # Sin punto o sin banda no hay intervalo que publicar: los dos
                # extremos tienen que ser None, no 0 (que seria un intervalo real
                # de ancho cero, o sea una certeza inventada).
                assert step["lower"] is None and step["upper"] is None, step
                continue
            # Con punto y banda, el intervalo existe y contiene su estimacion. El
            # punto NEGATIVO (serie que baja y cruza el cero) es el caso que
            # invierte los dos productos y publica `lower` arriba de `upper`.
            assert step["lower"] is not None and step["upper"] is not None, step
            assert step["lower"] <= step["point"] <= step["upper"], step
            assert step["lower"] <= step["upper"], step

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_la_banda_no_es_mas_ajustada_que_el_peor_residuo_del_paso(self, valores, horizonte):
        """La banda de un paso cubre el peor error que ESE paso mostro.

        Es la regla del docstring: el ancho no es un parametro elegido, es el
        error observado. Si fuera mas ajustado, se estaria prometiendo una
        precision que la serie ya rompio una vez — y en el mismo paso.
        """
        out = build_projection(valores, horizonte)
        if not out["available"]:
            return
        residuos = walk_forward(valores, horizonte)
        assert set(residuos) == set(METHODS)
        assert all(len(pasos) == horizonte for pasos in residuos.values())
        for step in out["steps"]:
            propios = residuos[out["method"]][step["step"] - 1]
            assert step["n_backtests"] == len(propios), step
            if not propios:
                assert step["extrapolated"] is True, step
                assert step["reliable"] is False and step["mape"] is None, step
                continue
            # 0.05 es medio paso del redondeo con que se publica la banda (y con
            # el que se eligio el metodo): contra el valor crudo, redondear
            # hacia abajo haria fallar la regla por 0.04.
            assert step["band_pct"] >= max(propios) - 0.05, (step, max(propios))
            assert step["extrapolated"] is False, step
            assert step["reliable"] == (len(propios) >= MIN_BACKTESTS_RELIABLE), step

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_el_total_es_la_suma_de_los_puntos(self, valores, horizonte):
        """El total publicado es exactamente la suma de los puntos publicados."""
        out = build_projection(valores, horizonte)
        if not out["available"]:
            assert out["total"] is None, out
            return
        if any(step["point"] is None for step in out["steps"]):
            assert out["total"] is None, out
            return
        assert out["total"] is not None, out
        assert out["total"]["point"] == sum(step["point"] for step in out["steps"]), out

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_la_banda_del_total_cubre_el_prefijo_que_declara(self, valores, horizonte):
        """La banda del total es el peor error ACUMULADO del prefijo medido.

        No es la suma de las bandas por paso: si el total dice
        `measured_steps=j`, su ancho tiene que cubrir el error del total de 1..j
        que el backtest observo. Y si j < horizonte, no puede publicar MAPE del
        total ni presentarse como medido.
        """
        out = build_projection(valores, horizonte)
        total = out["total"]
        if total is None or total["band_pct"] is None:
            return
        _, acumulados = _walk_residuals(valores, horizonte)
        medidos = total["measured_steps"]
        assert medidos is not None and 1 <= medidos <= horizonte, total
        propios = acumulados[out["method"]][medidos - 1]
        assert propios, total
        assert total["band_pct"] >= max(propios) - 0.05, (total, max(propios))
        assert total["n_backtests"] == len(propios), total
        if medidos < horizonte:
            assert total["extrapolated"] is True and total["mape"] is None, total
        else:
            assert total["extrapolated"] is False, total
            assert total["mape"] is not None, total

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_un_hueco_nunca_se_declara_confiable(self, valores, horizonte):
        """Un hueco en el eje temporal invalida la confianza, aunque haya residuos."""
        out = build_projection(valores, horizonte, has_gaps=True)
        for step in out["steps"]:
            assert step["reliable"] is False, step
        if out["total"] is not None:
            assert out["total"]["reliable"] is False, out["total"]


class TestDeterminismo:

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_la_misma_serie_da_la_misma_proyeccion(self, valores, horizonte):
        """Sin azar: la proyeccion se puede auditar repitiendola."""
        primera = build_projection(valores, horizonte)
        segunda = build_projection(valores, horizonte)
        assert primera["method"] == segunda["method"]
        assert primera["mape"] == segunda["mape"]
        assert primera["beats_naive"] == segunda["beats_naive"]
        assert [s["point"] for s in primera["steps"]] == [s["point"] for s in segunda["steps"]]
        assert [s["band_pct"] for s in primera["steps"]] == [s["band_pct"] for s in segunda["steps"]]
        assert primera["total"] == segunda["total"]


class TestAvailableYSinNan:

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_available_y_sin_nan(self, valores, horizonte):
        """`available` no puede contradecir al contenido, y no hay NaN.

        La ida y vuelta NO es estricta a proposito: un metodo puede ganar el
        backtest sobre origenes cortos y no ajustar la serie completa, y entonces
        `available` es False aunque haya residuos. Esa es la unica excepcion, y
        es honesta: sin punto para el paso 1 no hay banda que publicar.
        """
        out = build_projection(valores, horizonte)
        for value in _floats(out):
            assert math.isfinite(value), out

        if len(valores) < MIN_PERIODS:
            assert out["available"] is False
            assert out["reason"] and out["steps"] == [], out
            assert out["total"] is None, out
            return

        if not out["available"]:
            assert out["reason"] and out["steps"] == [], out
            assert out["total"] is None, out
            return

        assert any(step["n_backtests"] > 0 for step in out["steps"]), (
            "sin un solo residuo medible no se puede publicar una banda"
        )
        if any(step["point"] is None for step in out["steps"]):
            assert out["total"] is None, out
        else:
            assert out["total"] is not None, out
        for step in out["steps"]:
            assert step["band_pct"] is not None, step
            if step["point"] is None:
                assert step["lower"] is None and step["upper"] is None, step
            else:
                assert step["lower"] is not None and step["upper"] is not None, step

    @given(series, horizontes)
    @settings(max_examples=POCOS, deadline=None)
    def test_el_error_relativo_no_se_calcula_sobre_cero(self, valores, horizonte):
        """Una serie con ceros no rompe el calculo ni se inventa un 0%.

        El residuo es porcentual, asi que necesita denominador positivo. Los
        pasos cuyo actual es 0 simplemente no aportan residuo: ni excepcion, ni
        un 0% que se leeria como acierto perfecto.
        """
        con_ceros = [0.0 if i % 3 == 0 else v for i, v in enumerate(valores)]
        out = build_projection(con_ceros, horizonte)
        for value in _floats(out):
            assert math.isfinite(value), out
        n = len(con_ceros)
        for method, pasos in walk_forward(con_ceros, horizonte).items():
            for j, propios in enumerate(pasos):
                # Origenes cuyo actual del paso j+1 es positivo: los unicos que
                # pueden aportar residuo porcentual.
                medibles = sum(
                    1 for t in range(MIN_PERIODS, n) if t + j < n and con_ceros[t + j] > 0
                )
                # Los tres metodos ajustan siempre: el unico filtro es el actual.
                assert len(propios) == medibles, (method, j, propios)
                # Un 0.0 es un acierto exacto (pred == actual > 0) y es lo que
                # tiene que salir; el bug seria un residuo calculado sobre un
                # actual 0, o uno negativo.
                assert all(math.isfinite(r) and r >= 0.0 for r in propios), (method, j)
