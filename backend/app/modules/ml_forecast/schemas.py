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

`horizon=None` es "Auto": el analisis puede ajustarlo a lo que pida la pregunta.
Un 3, 6 o 12 explicito siempre manda sobre el LLM. El default sigue siendo 6
para que un cliente viejo que no manda el campo conserve el comportamiento.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.modules.chat_engine.schemas import MAX_QUESTION_CHARS

# Horizontes publicables: trimestre, semestre y ano. No es una lista de valores
# comodos —es el conjunto que el backtest puede sostener con series de ~9 meses y
# el unico que el motor acepta— y por eso se comparte con el analisis del LLM.
SUPPORTED_HORIZONS = (3, 6, 12)

# Horizonte que se usa cuando el pedido no lo trae y el analisis no propone uno
# valido. Se mantiene 6 por compatibilidad con los clientes existentes.
DEFAULT_HORIZON = 6


def _validar_horizonte(value: Optional[int]) -> Optional[int]:
    """Corta cualquier horizonte que no sea publicable. `None` es Auto."""
    if value is None:
        return None
    if value not in SUPPORTED_HORIZONS:
        raise ValueError(
            f"horizon debe ser uno de {list(SUPPORTED_HORIZONS)} o null (Auto); "
            f"recibido: {value!r}"
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
    horizon: Optional[int] = DEFAULT_HORIZON
    # Overrides del selector manual. Se validan contra las columnas REALES y
    # permitidas del rol; un nombre que no exista no llega al SQL. Si vienen
    # metrica, fecha y horizonte, el analisis no necesita llamar al LLM.
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    dimension_column: Optional[str] = None
    # Escape hatch para auditar un mes en curso: por defecto se excluye porque un
    # periodo incompleto hunde el ultimo punto y el backtest lo lee como caida.
    include_partial_period: bool = False

    @field_validator("horizon")
    @classmethod
    def _horizonte_soportado(cls, value: Optional[int]) -> Optional[int]:
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


class ProjectionTotal(BaseModel):
    """Total del horizonte con la evidencia que lo sostiene.

    `measured_steps` dice cuantos pasos del total se midieron de verdad. Si es
    menor que `horizon`, `extrapolated=True` y `mape=None`: la banda existe, pero
    es la de un prefijo mas corto y no se presenta como medicion del total
    completo. `measured_steps=None` significa que no hubo ni un prefijo medible;
    en ese caso el total viaja sin banda.
    """

    point: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    band_pct: Optional[float] = None
    mape: Optional[float] = None
    n_backtests: int = 0
    reliable: bool = False
    extrapolated: bool = False
    measured_steps: Optional[int] = None


class ProjectionGroupSummary(BaseModel):
    """Un grupo de la dimension, proyectado con SU propia serie historica.

    `available=False` con `reason` no es un error de la corrida: el grupo puede
    tener historia insuficiente o haber perdido el permiso de lectura. El resto
    del desglose y la proyeccion principal siguen publicandose.
    """

    value: str
    available: bool
    reason: Optional[str] = None
    method: Optional[str] = None
    mape: Optional[float] = None
    n_periods: int = 0
    has_gaps: bool = False
    total: Optional[ProjectionTotal] = None


class ProjectionBreakdown(BaseModel):
    """Top de grupos por volumen historico, sin categoria "Otros".

    No reemplaza ni suma el total general: cada grupo se proyecta por separado
    y el top se elige por historia observada, no por el resultado proyectado.
    """

    available: bool
    reason: Optional[str] = None
    dimension_column: Optional[str] = None
    top_n: int = 5
    groups: List[ProjectionGroupSummary] = Field(default_factory=list)


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
    # "pedido" | "llm" | "default": quien fijo el horizonte que se publico.
    horizon_source: Optional[str] = None
    series: List[Dict[str, Any]] = []  # Historico [{periodo, valor}] (patron ForecastCard.series)
    steps: List[ProjectionStep] = []
    total: Optional[ProjectionTotal] = None
    # Desglose opcional por una dimension de texto permitida.
    breakdown: Optional[ProjectionBreakdown] = None
    method: Optional[str] = None
    beats_naive: Optional[bool] = None
    mape: Optional[float] = None
    mape_by_method: Dict[str, Optional[float]] = {}
    n_periods: int = 0
    has_gaps: bool = False
    # Periodo en curso excluido del calculo (o None si no se excluyo ninguno).
    partial_period_excluded: Optional[str] = None
    # Periodos posteriores al mes actual descartados por no ser historia.
    future_periods_excluded: List[str] = []
    income_only: bool = True
    sql: Optional[str] = None
    table: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    # Conexion sobre la que se calculo: la verificacion la necesita para volver
    # a leer los datos actuales con los MISMOS gates. Las corridas viejas no la
    # tienen y se muestran como no verificables.
    connection_id: Optional[int] = None
    analysis_source: Optional[str] = None  # "llm" | "heuristica" | "manual"
    narration: Optional[str] = None
    narration_source: Optional[str] = None  # "llm" | "plantilla"
    audit_log_id: Optional[int] = None


class ProjectionEstimateRequest(BaseModel):
    """Pedido de estimacion. Mismo tope de pregunta, misma lista y Auto."""

    question: str = Field(..., max_length=MAX_QUESTION_CHARS)
    horizon: Optional[int] = DEFAULT_HORIZON

    @field_validator("horizon")
    @classmethod
    def _horizonte_soportado(cls, value: Optional[int]) -> Optional[int]:
        return _validar_horizonte(value)


class ProjectionEstimateResponse(BaseModel):
    """Estimacion instantanea. `source="fallback"` declara que NO hubo medicion.

    `horizon_used` es el horizonte con el que se calculo la estimacion: con Auto
    se asume el mayor de la lista, porque el LLM todavia no respondio.
    """

    estimated_seconds: float
    source: str  # "medido" | "fallback"
    horizon_used: int = DEFAULT_HORIZON


class ProjectionContextResponse(BaseModel):
    """Columnas que el selector manual puede ofrecer, ya filtradas por el rol.

    Solo nombres que existen en los metadatos de la fact table autorizada y que
    el rol puede leer. `available=False` explica por que no hay contexto (sin
    rol, sin tablas, conexion caida) sin devolver una lista inventada.
    """

    available: bool = True
    reason: Optional[str] = None
    connection_id: int
    table: Optional[str] = None
    metric_columns: List[str] = Field(default_factory=list)
    date_columns: List[str] = Field(default_factory=list)
    dimension_columns: List[str] = Field(default_factory=list)


class ProjectionHistoryItem(BaseModel):
    """Una proyeccion auditada del usuario, con lo minimo para elegir verificarla."""

    audit_log_id: int
    timestamp: datetime
    question: str
    horizon: int
    table: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    connection_id: Optional[int] = None
    total_point: Optional[float] = None
    verifiable: bool = False
    reason: Optional[str] = None


class ProjectionHistoryResponse(BaseModel):
    items: List[ProjectionHistoryItem] = Field(default_factory=list)


class ProjectionVerificationStep(BaseModel):
    """Punto proyectado contra el dato actual de un periodo CERRADO.

    `status="pendiente"` significa que el periodo aun no cerro: no es un error
    ni un cero, es una comparacion que todavia no se puede hacer.
    """

    step: int
    period: Optional[str] = None
    projected: Optional[float] = None
    actual: Optional[float] = None
    error_pct: Optional[float] = None
    inside_band: Optional[bool] = None
    status: str  # "medido" | "pendiente" | "sin_dato" | "sin_periodo"


class ProjectionVerificationTotal(BaseModel):
    projected: Optional[float] = None
    actual: Optional[float] = None
    error_pct: Optional[float] = None
    inside_band: Optional[bool] = None
    status: str  # "medido" | "incompleto"


class ProjectionVerificationResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    audit_log_id: int
    question: str
    table: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    horizon: int
    method: Optional[str] = None
    steps: List[ProjectionVerificationStep] = Field(default_factory=list)
    total: Optional[ProjectionVerificationTotal] = None
    measured_steps: int = 0
    pending_steps: int = 0
    missing_steps: int = 0
    inside_band_steps: int = 0
    outside_band_steps: int = 0
    data_note: Optional[str] = None
