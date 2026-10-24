"""Propiedades del calculo de forecast, sobre todo el dominio y no 3 ejemplos.

Por que aqui y no en `test_prediction_honesty.py`
------------------------------------------------
Ese archivo fija una serie de 9 periodos y afirma cosas sobre ella. Es un test de
ejemplos: si el bug necesita otra serie, pasa. Este cubre las REGLAS que el
modulo dice cumplir en su docstring, y deja que el generador busque el
contraejemplo.

Que NO se hace aqui, a proposito
--------------------------------
No se recalcula el backtest para compararlo con la banda: eso seria
reimplementar la regla y el test sobrevive a cualquier bug que ambas compartan.
Las propiedades elegidas son algebraicas y restringen el resultado sin repetir
la implementacion:

  - TIPO: `Decimal`, `int` y `float` dan el MISMO float. Esa equivalencia es la
    razon de existir de `_as_number`: `SUM()` de Postgres devuelve `numeric`, y
    si el forecast se calculara sobre una serie de `Decimal` con un camino
    distinto al de `float`, dos series identicas darian dos bandas distintas.
  - INVARIANTE DE BANDA: la banda contiene el punto, siempre. Es la propiedad
    que sostiene todo el modulo: sin ella se publica un intervalo que excluye su
    propia estimacion y el lector no tiene forma de saberlo.

`deadline=None` en las que hacen trabajo real: el plazo por defecto de
Hypothesis convierte una maquina lenta en un test que falla, y ese flake acaba
con el test borrado.
"""
import decimal
import math

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.modules.chat_engine.forecast_calculator import (
    MIN_PERIODS,
    _as_number,
    forecast_next_period,
)


class TestAsNumberProperties:
    """`_as_number` es una funcion de TIPO, no de valor."""

    @given(st.decimals(min_value=decimal.Decimal("-1e9"), max_value=decimal.Decimal("1e9"),
                        allow_nan=False, allow_infinity=False))
    @settings(max_examples=100, deadline=None)
    def test_decimal_y_float_coinciden(self, d):
        """Un Decimal y su float dan el MISMO resultado.

        `SUM()` de Postgres devuelve `numeric` y llega crudo si el llamador no
        pasa por `_clean_row`. Si los dos caminos dieran distinto, dos series
        identicas darian dos bandas distintas segun como llegaran los datos: el
        mismo numero dependeria del camino, no del dato.

        El `int` NO se compara: `int(Decimal('0.5'))` trunca a 0, que es la
        diferencia entre truncar y redondear, no un bug de `_as_number`. La
        equivalencia que importa es la de los tres tipos que Postgres puede
        entregar por el mismo camino.
        """
        as_dec = _as_number(d)
        as_float = _as_number(float(d))
        assert as_dec is not None and as_float is not None
        assert as_dec == pytest.approx(as_float, rel=1e-9)

    @given(st.integers(min_value=-10**9, max_value=10**9))
    @settings(max_examples=100, deadline=None)
    def test_int_y_float_coinciden(self, n):
        """Un entero de Postgres y su float dan el mismo resultado."""
        assert _as_number(n) == pytest.approx(_as_number(float(n)))

    @example(True)
    @example(False)
    @given(st.booleans())
    @settings(max_examples=100, deadline=None)
    def test_bool_no_es_numero_de_negocio(self, b):
        """`True` no es una venta. Puesto en el codigo con un comentario; aqui es una property.

        Sin la exclusion, `count(*)` de una columna booleana entra como numero y
        la retencion se calcula sobre flags en vez de sobre clientes.

        `st.booleans()` y no `st.integers()`: con `@example` encima, Hypothesis
        infiere el tipo del ejemplo, asi que un `True` de ejemplo hace que el
        generador produzca enteros y `True` llegaria como 1: el test pasaria
        probando justo lo contrario de lo que dice.
        """
        assert _as_number(b) is None

    @given(st.one_of(st.none(), st.text(max_size=5), st.lists(st.integers(), max_size=3)))
    @settings(max_examples=100, deadline=None)
    def test_no_numerico_da_none(self, cosa):
        """Lo que no es numero da None, y no 0.

        Un fallback a 0 seria sumar un cliente con importe cero al total: el
        ranking de accion lo colocaria entre los que mas pesan.
        """
        assert _as_number(cosa) is None


class TestBandInvariant:
    """La propiedad que sostiene el modulo entero."""

    # Series de >= MIN_PERIODS con ultimo valor positivo: es el precondicion que
    # `forecast_next_period` exige para publicar, y va en la estrategia para que
    # el generador produzca series publicables directo en vez de descartar.

    @given(
        st.lists(
            st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
            min_size=MIN_PERIODS,
            max_size=24,
        )
    )
    @example([9.8e6, 8.5e6, 7.3e6, 11.7e6, 17.6e6, 20.2e6, 23.3e6, 20.2e6, 22.5e6])
    @settings(max_examples=150, deadline=None)
    def test_la_banda_siempre_contiene_el_punto(self, valores):
        """`lower <= point <= upper`, sin excepcion.

        Si la banda no contiene su propia estimacion, el lector no tiene forma de
        saberlo: ve un intervalo y una cifra, y el intervalo no incluye la cifra.
        Es el fallo mas grave posible en un forecast, y el `self_check` del
        modulo solo lo prueba con UNA serie.
        """
        filas = [{"periodo": f"2026-{i + 1:02d}", "monto": v} for i, v in enumerate(valores)]
        fc = forecast_next_period(filas)
        assert fc is not None, f"una serie de {len(valores)} periodos deberia publicar forecast"
        assert fc["lower"] <= fc["point"] <= fc["upper"], (
            f"banda que excluye su propio punto: {fc}"
        )
        assert fc["lower"] <= fc["upper"], "banda invertida"

    @given(
        st.lists(
            st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
            min_size=MIN_PERIODS,
            max_size=24,
        )
    )
    @settings(max_examples=150, deadline=None)
    def test_la_banda_no_puede_ser_mas_ajustada_que_el_error_medio(self, valores):
        """`band_pct >= mape`.

        La banda es el peor error de backtest, asi que no puede prometer mas
        precision de la que la serie mostro. Un MAPE por encima de la banda
        significaria que el intervalo publicado es mas optimista que el error
        historico real.
        """
        filas = [{"periodo": f"2026-{i + 1:02d}", "monto": v} for i, v in enumerate(valores)]
        fc = forecast_next_period(filas)
        if fc is None:
            return
        assert fc["band_pct"] >= fc["mape"] - 1e-6, (
            f"banda ({fc['band_pct']}%) mas ajustada que el MAPE ({fc['mape']}%)"
        )
        assert not math.isnan(fc["mape"]) and fc["mape"] >= 0.0