"""Contrato HTTP de las proyecciones ML: pedido, pasos, narrativa y estimacion.

Por que el horizonte se valida en el borde y no en el servicio
-------------------------------------------------------------
`horizon` decide cuantos pasos se publican y para cuantos de ellos hay backtest
propio. Un valor fuera de {3, 6, 12} no es una proyeccion que no se pudo
calcular: es una peticion mal armada. Se corta en el punto de entrada —FastAPI
responde 422 con el campo nombrado— mientras el error todavia tiene nombre, en
vez de llegar al motor y volver como un "no se pudo" que culpa a los datos.

La misma lista la reusa `ml_service.analyze_question` para aceptar (o ignorar)
el horizonte que proponga el LLM: una sola definicion, dos puertas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.modules.chat_engine.schemas import MAX_QUESTION_CHARS

# Horizontes publicables: trimestre, semestre y ano. No es una lista de valores
# comodos —es el conjunto que el backtest puede sostener con series de ~9 meses y
# el unico que el motor acepta— y por eso se comparte con el analisis del LLM.
SUPPORTED_HORIZONS = (3, 6, 12)


def _validar_horizonte(value: int) -> int:
    """Corta cualquier horizonte que no sea publicable. Compartido por los dos pedidos."""
    if value not in SUPPORTED_HORIZONS:
        raise ValueError(
            f"horizon debe ser uno de {list(SUPPORTED_HORIZONS)}; recibido: {value!r}"
        )
    return value


class ProjectionRequest(BaseModel):
    """Lo que pide el cliente para correr una proyeccion.

    `question` existe porque la metrica se elige a partir de ella (el motor no
    adivina si proyectar montos, cantidades o cualquier otra numerica de la
    tabla): es parte del pedido, no una etiqueta para el log.
    """

    connection_id: Optional[int] = None
    question: str = Field(..., max_length=MAX_QUESTION_CHARS)
    horizon: int = 6

    @field_validator("horizon")
    @classmethod
    def _horizonte_soportado(cls, value: int) -> int:
        return _validar_horizonte(value)


class ProjectionStep(BaseModel):
    """Un paso futuro publicado, con su banda y su evidencia.

    `period` lo agrega el servicio con `next_period` del forecast existente
    (aritmetica de calendario compartida, no una copia). `reliable` y
    `extrapolated` viajan SIEMPRE: un paso sin backtest propio hereda la banda
    del ultimo medido y quien lee tiene que poder distinguirlos.
    """

    step: int
    period: Optional[str] = None
    point: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    band_pct: Optional[float] = None
    mape: Optional[float] = None
    n_backtests: int = 0
    reliable: bool = False
    extrapolated: bool = False


class ProjectionResponse(BaseModel):
    """Lo que ve el usuario: la proyeccion, su evidencia y el SQL que la produjo.

    `available=False` no es un error: es la respuesta honesta cuando la serie no
    alcanza o la gobernanza nego una columna. En ese caso `reason` explica por
    que, y la serie y las columnas viajan igual para que el lector pueda accionar
    sin pedir la corrida de nuevo.
    """

    available: bool
    reason: Optional[str] = None
    question: str
    horizon: int
    series: List[Dict[str, Any]] = []  # Historico [{periodo, valor}] (patron ForecastCard.series)
    steps: List[ProjectionStep] = []
    method: Optional[str] = None
    beats_naive: Optional[bool] = None
    mape: Optional[float] = None
    mape_by_method: Dict[str, Optional[float]] = {}
    n_periods: int = 0
    has_gaps: bool = False
    income_only: bool = True
    sql: Optional[str] = None
    table: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    analysis_source: Optional[str] = None  # "llm" | "heuristica"
    narration: Optional[str] = None
    narration_source: Optional[str] = None  # "llm" | "plantilla"
    audit_log_id: Optional[int] = None


class ProjectionEstimateRequest(BaseModel):
    """Pedido de estimacion. Mismo tope de pregunta y misma lista de horizontes."""

    question: str = Field(..., max_length=MAX_QUESTION_CHARS)
    horizon: int = 6

    @field_validator("horizon")
    @classmethod
    def _horizonte_soportado(cls, value: int) -> int:
        return _validar_horizonte(value)


class ProjectionEstimateResponse(BaseModel):
    """Estimacion instantanea. `source="fallback"` declara que NO hubo medicion."""

    estimated_seconds: float
    source: str  # "medido" | "fallback"
