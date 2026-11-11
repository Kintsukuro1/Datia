"""La pregunta manda: horizonte y granularidad salen del texto.

El feedback del usuario final fue concreto: pidio "un año" y recibio 6 meses
porque el contrato solo aceptaba 3/6/12. Estos tests fijan la lectura
determinista que reemplaza esa lista.
"""

import pytest
from hypothesis import given, strategies as st

from app.modules.ml_forecast.schemas import MAX_HORIZON
from app.modules.ml_forecast.timeframe import (
    parse_growth_scenario,
    parse_question_timeframe,
)


@pytest.mark.parametrize(
    "question,steps,granularity",
    [
        ("proyecta un año", 12, "month"),
        ("proyecta los proximos 12 meses", 12, "month"),
        (
            "dame la proyeccion del producto con mayor venta en el proximo año",
            12,
            "month",
        ),
        ("proyecta 6 semanas", 6, "week"),
        ("proyecta 3 trimestres", 3, "quarter"),
        ("proyecta 30 dias", 30, "day"),
        ("proyecta 2 años", 24, "month"),
        ("proyecta un año, semana a semana", 12, "month"),
    ],
)
def test_horizonte_y_granularidad_desde_el_texto(question, steps, granularity):
    result = parse_question_timeframe(question)
    assert result.steps == steps
    assert result.granularity == granularity


def test_el_historico_no_es_el_horizonte():
    # "los ultimos 12 meses" describe la historia, no lo que se proyecta.
    assert parse_question_timeframe("proyecta con los ultimos 12 meses").steps is None


def test_el_horizonte_de_la_proyeccion_gana_sobre_el_historico():
    result = parse_question_timeframe(
        "proyecta los proximos 6 meses usando los ultimos 12 meses"
    )
    assert result.steps == 6
    assert result.granularity == "month"


def test_el_tope_recorta_y_declara_lo_pedido():
    result = parse_question_timeframe("proyecta 10 años")
    assert result.steps == MAX_HORIZON
    assert result.requested_steps == 120
    assert result.granularity == "month"


def test_sin_horizonte_pero_con_granularidad_explicita():
    result = parse_question_timeframe("mostra las ventas mensuales")
    assert result.steps is None
    assert result.granularity == "month"

    trimestral = parse_question_timeframe("mostra las ventas trimestrales")
    assert trimestral.steps is None
    assert trimestral.granularity == "quarter"


def test_texto_sin_pistas_no_inventa_nada():
    assert parse_question_timeframe("que paso con las ventas") == (None, None, None)


@given(st.text(max_size=200))
def test_cualquier_texto_produce_un_horizonte_publicable(texto):
    steps, granularity, requested = parse_question_timeframe(texto)
    assert granularity in (None, "day", "week", "month", "quarter")
    if steps is not None:
        assert 1 <= steps <= MAX_HORIZON
        assert requested is not None and requested >= steps
    else:
        assert requested is None


def test_el_escenario_de_crecimiento_se_reconoce():
    assert parse_growth_scenario(
        "Proyectame una ganancia con el 10% de las bases en 10 años"
    ) == 10.0
    assert parse_growth_scenario("crecimiento anual del 8% en 5 años") == 8.0
    assert parse_growth_scenario("un 12% anual durante 3 años") == 12.0


def test_sin_crecimiento_no_hay_escenario():
    assert parse_growth_scenario("proyecta las ventas del próximo año") is None
    assert parse_growth_scenario("ganancia del 10% sin horizonte") is None
    assert parse_growth_scenario("proyecta el margen de ganancia") is None
