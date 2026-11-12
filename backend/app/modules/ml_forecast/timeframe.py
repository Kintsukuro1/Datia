"""Horizonte y granularidad que pide el texto del usuario.

La UI ya no ofrece selectores: "proyecta un año" tiene que producir 12 pasos
mensuales, y "6 semanas" seis pasos semanales. El LLM local no es confiable para
hacer esa cuenta (y su contrato anterior solo aceptaba 3/6/12), asi que la
primera lectura es determinista, sin BD y testeable. El LLM queda como respaldo
cuando el texto no dice nada.

Decisiones:

- Se normaliza el texto (minusculas y sin tildes) para que "año" y "anio"
  cuenten igual. El resto del pipeline sigue recibiendo el texto original.
- Se elige la mencion con contexto de futuro ("proyecta", "proximos",
  "siguientes"...) y se descartan las de historico ("ultimos", "pasados").
  Sin contexto, gana la ultima mencion: en una pregunta real el horizonte suele
  cerrar la frase.
- El tope es `MAX_HORIZON`. Si el texto pide mas, se recorta y se devuelve
  `requested_steps` para que la respuesta pueda declarar el recorte en vez de
  mentir sobre lo que se publico.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple, Optional

from app.modules.ml_forecast.lexicon import (
    FUTURE_MARKERS,
    GRANULARITY_HINTS,
    HISTORY_MARKERS,
    NUMBER_WORDS,
    UNIT_FACTOR,
    UNIT_GRANULARITY,
    UNIT_PATTERN,
)
from app.modules.ml_forecast.policy import POLICY
from app.modules.ml_forecast.schemas import MAX_HORIZON


class Timeframe(NamedTuple):
    """Lo que pide la pregunta, ya en pasos publicables."""

    steps: Optional[int]
    granularity: Optional[str]
    requested_steps: Optional[int]


_NUMBER_PATTERN = "|".join(NUMBER_WORDS)
_UNIT_PATTERN = UNIT_PATTERN

_MENTION_RE = re.compile(
    rf"\b(?:(?P<num>\d{{1,4}}|{_NUMBER_PATTERN})\s+)?(?P<unit>{_UNIT_PATTERN})\b"
)

_GRANULARITY_HINTS = tuple(
    (re.compile(pattern), granularity) for pattern, granularity in GRANULARITY_HINTS
)


def _normalize(text: str) -> str:
    """Minusculas y sin tildes. "año" y "anio" quedan como "ano"."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _quantity(raw: Optional[str]) -> int:
    if not raw:
        return 1
    if raw.isdigit():
        return int(raw)
    return NUMBER_WORDS.get(raw, 1)


def _mention_steps(match: re.Match[str]) -> tuple[int, str]:
    unit = match.group("unit")
    quantity = _quantity(match.group("num"))
    factor = UNIT_FACTOR.get(unit, 1)
    return quantity * factor, UNIT_GRANULARITY[unit]


def parse_question_timeframe(question: str) -> Timeframe:
    """(pasos, granularidad, pasos pedidos) que pide el texto, o `(None, ...)`."""
    text = _normalize(question)

    candidates: list[tuple[int, int, str, bool, bool, bool]] = []
    for match in _MENTION_RE.finditer(text):
        steps, granularity = _mention_steps(match)
        window = text[max(0, match.start() - 60):match.start()]
        is_future = any(marker in window for marker in FUTURE_MARKERS)
        is_history = any(marker in window for marker in HISTORY_MARKERS)
        # "semana a semana" / "mes a mes" describen la forma de leer, no una
        # duracion: no cuentan como horizonte (si como pista de granularidad).
        unit = re.escape(match.group("unit"))
        tail = text[match.end():match.end() + 16]
        prefix = text[max(0, match.start() - 16):match.start()]
        repeated = bool(
            re.match(rf"\s+a\s+{unit}\b", tail)
            or re.search(rf"\b{unit}\s+a\s+$", prefix)
        )
        candidates.append(
            (match.start(), steps, granularity, is_future, is_history, repeated)
        )

    chosen = None
    if candidates:
        usable = [item for item in candidates if not item[5]]
        future = [item for item in usable if item[3] and not item[4]]
        if future:
            chosen = future[-1]
        else:
            plain = [item for item in usable if not item[4]]
            if plain:
                chosen = plain[-1]

    if chosen is None:
        for pattern, granularity in _GRANULARITY_HINTS:
            if pattern.search(text):
                return Timeframe(None, granularity, None)
        return Timeframe(None, None, None)

    _, requested, granularity, _, _, _ = chosen
    return Timeframe(min(requested, MAX_HORIZON), granularity, requested)


_SCENARIO_GROWTH_RE = re.compile(
    r"(?:crecimiento|crecer|aumento|incremento)\s+(?:anual\s+)?(?:del?\s+)?"
    r"(\d{1,4}(?:[.,]\d+)?)\s*%"
)
_SCENARIO_ANNUAL_RE = re.compile(
    r"(\d{1,4}(?:[.,]\d+)?)\s*%\s*(?:anual|por ano)"
)
_SCENARIO_GAIN_YEARS_RE = re.compile(
    r"ganancia.*?(\d{1,4}(?:[.,]\d+)?)\s*%.*?\banos?\b"
)


def parse_growth_scenario(question: str) -> Optional[float]:
    """Porcentaje de crecimiento anual si la pregunta plantea un escenario.

    El caso real: "Proyectame una ganancia con el 10% de las bases en 10 años".
    No dice "crecimiento anual" con esas palabras, pero pide una ganancia con un
    porcentaje y un horizonte en años; se interpreta como crecimiento anual y se
    devuelve 10.0. El resto de las preguntas devuelve None y sigue el camino
    estadistico de siempre.
    """
    text = _normalize(question)
    for pattern in (
        _SCENARIO_GROWTH_RE,
        _SCENARIO_ANNUAL_RE,
        _SCENARIO_GAIN_YEARS_RE,
    ):
        match = pattern.search(text)
        if match is None:
            continue
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if 0 < value <= POLICY.scenario_growth_max_pct:
            return value
    return None


def __self_check() -> None:
    """Casos que tienen que seguir ciertos si alguien toca el parser."""
    cases = [
        ("proyecta un ano", 12, "month"),
        ("dame la proyeccion del producto en el proximo año", 12, "month"),
        ("proyecta 6 semanas", 6, "week"),
        ("proyecta 3 trimestres", 3, "quarter"),
        ("proyecta 30 dias", 30, "day"),
        ("proyecta 10 anos", MAX_HORIZON, "month"),
    ]
    for question, steps, granularity in cases:
        result = parse_question_timeframe(question)
        assert (result.steps, result.granularity) == (steps, granularity), (question, result)
    assert parse_question_timeframe("con los ultimos 12 meses").steps is None
    assert parse_question_timeframe("las ventas mensuales").granularity == "month"
    assert parse_question_timeframe("las ventas mensuales").steps is None
    assert parse_growth_scenario(
        "Proyectame una ganancia con el 10% de las bases en 10 anos"
    ) == 10.0
    assert parse_growth_scenario("crecimiento anual del 8% en 5 anos") == 8.0
    assert parse_growth_scenario("proyecta las ventas del proximo ano") is None
    assert parse_growth_scenario("ganancia del 10% sin horizonte") is None


if __name__ == "__main__":
    __self_check()
    print("timeframe: self-check OK")
