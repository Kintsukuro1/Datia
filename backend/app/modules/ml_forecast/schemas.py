"""Contrato HTTP de las proyecciones ML: pedido, pasos, narrativa y estimacion.

Por que el horizonte se valida en el borde y no en el servicio
-------------------------------------------------------------
`horizon` decide cuantos pasos se publican y para cuantos de ellos hay backtest
propio. Un valor fuera de rango no es una proyeccion que no se pudo calcular: es
una peticion mal armada. Se corta en el punto de entrada —FastAPI responde 422
con el campo nombrado— mientras el error todavia tiene nombre, en vez de llegar
al motor y volver como un "no se pudo" que culpa a los datos.

El rango publicable sale de `policy.POLICY`, que es la unica fuente de los
limites y defaults. La UI ya no ofrece un selector: el horizonte sale de lo que
el usuario escribe ("un año", "6 semanas") y lo resuelve
`ml_forecast/timeframe.py`. El limite del modo estadistico existe para no
publicar una banda de 120 pasos construida sobre una serie corta; no es una
lista de valores comodos. Un escenario de crecimiento explicito
(`scenario_growth_pct`) si admite hasta `max_scenario_horizon` pasos, porque no
tiene banda ni backtest que sostener.

`horizon=None` es "Auto": el analisis puede ajustarlo a lo que pida la pregunta.
Un valor explicito siempre manda sobre el LLM. El default sigue siendo 6
para que un cliente viejo que no manda el campo conserve el comportamiento.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.chat_engine.schemas import MAX_QUESTION_CHARS
from app.modules.ml_forecast.policy import POLICY

# Rango publicable. El piso evita un horizonte de 0 pasos (que no es una
# proyeccion) y el techo evita bandas sin evidencia sobre series cortas. Los
# exports se mantienen para los clientes que ya los importan.
MIN_HORIZON = POLICY.min_horizon
MAX_HORIZON = POLICY.max_horizon

# Horizonte que se usa cuando el pedido no lo trae y la pregunta tampoco lo dice.
# Se mantiene 6 por compatibilidad con los clientes existentes.
DEFAULT_HORIZON = POLICY.default_horizon


def _validar_horizonte(value: Optional[int], techo: int = MAX_HORIZON) -> Optional[int]:
    """Corta cualquier horizonte fuera de rango. `None` es Auto.

    `techo` es el maximo del MODO: estadistico (`MAX_HORIZON`) o escenario
    (`max_scenario_horizon`). El techo del escenario es mas alto porque sus pasos
    son un supuesto determinista, no una banda medida.
    """
    if value is None:
        return None
    if not MIN_HORIZON <= value <= techo:
        raise ValueError(
            f"horizon debe estar entre {MIN_HORIZON} y {techo}, o null (Auto); "
            f"recibido: {value!r}"
        )
    return value


def _validar_escenario(value: Optional[float]) -> Optional[float]:
    """Escenario explicito dentro del rango publicable, o `None`.

    El escenario no tiene banda ni backtest que lo acote, pero una tasa absurda
    (`inf`, 5000%) llega al motor y revienta lejos del origen. Se corta aca.
    """
    if value is None:
        return None
    if not math.isfinite(value) or not (
        POLICY.scenario_growth_min_pct <= value <= POLICY.scenario_growth_max_pct
    ):
        raise ValueError(
            f"scenario_growth_pct debe estar entre "
            f"{POLICY.scenario_growth_min_pct} y {POLICY.scenario_growth_max_pct}; "
            f"recibido: {value!r}"
        )
    return value


def _validar_horizonte_del_modo(
    horizon: Optional[int], scenario_growth_pct: Optional[float]
) -> None:
    """Sin escenario, el horizonte no puede pasar el techo estadistico.

    El escenario admite mas pasos porque es un supuesto determinista, no una
    banda medida. La regla es una sola para los dos pedidos (corrida y estimacion).
    """
    if (
        horizon is not None
        and scenario_growth_pct is None
        and horizon > MAX_HORIZON
    ):
        raise ValueError(
            f"horizon debe estar entre {MIN_HORIZON} y {MAX_HORIZON}, o null "
            f"(Auto), cuando no hay scenario_growth_pct; recibido: {horizon!r}"
        )


class ProjectionPolicyPublic(BaseModel):
    """Politica publicada en la respuesta para que la UI no repita constantes.

    Es el subconjunto estable de `POLICY.public()`. El frontend tiene que leer
    `max_horizon` de aca en vez de asumir 36.
    """

    min_horizon: int = POLICY.min_horizon
    max_horizon: int = POLICY.max_horizon
    max_scenario_horizon: int = POLICY.max_scenario_horizon
    default_horizon: int = POLICY.default_horizon
    default_scenario_growth_pct: float = POLICY.default_scenario_growth_pct
    min_periods: int = POLICY.min_periods
    min_diagnosable: int = POLICY.min_diagnosable
    reliable_backtests: int = POLICY.reliable_backtests
    min_recommended_horizon: int = POLICY.min_recommended_horizon
    exploratory_mape: float = POLICY.exploratory_mape
    unreliable_mape: float = POLICY.unreliable_mape
    breakdown_top_n: int = POLICY.breakdown_top_n
    scenario_growth_min_pct: float = POLICY.scenario_growth_min_pct
    scenario_growth_max_pct: float = POLICY.scenario_growth_max_pct

    @classmethod
    def from_policy(cls) -> "ProjectionPolicyPublic":
        """La politica vigente. Una sola construccion, sin copiar numeros."""
        return cls(**POLICY.public())


class _ProjectionRequestBase(BaseModel):
    """Campos y validaciones comunes a la corrida y a la estimacion."""

    horizon: Optional[int] = DEFAULT_HORIZON
    scenario_growth_pct: Optional[float] = None

    @field_validator("horizon")
    @classmethod
    def _horizonte_soportado(cls, value: Optional[int]) -> Optional[int]:
        # El techo del modo se decide en `_horizonte_del_modo`: aca solo se corta
        # lo que ningun modo admite.
        return _validar_horizonte(value, POLICY.max_scenario_horizon)

    @field_validator("scenario_growth_pct")
    @classmethod
    def _escenario_en_rango(cls, value: Optional[float]) -> Optional[float]:
        return _validar_escenario(value)

    @model_validator(mode="after")
    def _horizonte_del_modo(self) -> "_ProjectionRequestBase":
        """El escenario admite mas pasos que el estadistico, no al reves."""
        _validar_horizonte_del_modo(self.horizon, self.scenario_growth_pct)
        return self


class ProjectionRequest(_ProjectionRequestBase):
    """Lo que pide el cliente para correr una proyeccion.

    `question` existe porque la metrica se elige a partir de ella (el motor no
    adivina si proyectar montos, cantidades o cualquier otra numerica de la
    tabla): es parte del pedido, no una etiqueta para el log.
    """

    connection_id: Optional[int] = None
    # Tabla elegida manualmente. Se valida contra las autorizadas; si no está,
    # la corrida falla cerrada en vez de caer a otra tabla.
    table_name: Optional[str] = None
    question: str = Field(..., max_length=MAX_QUESTION_CHARS)
    # Overrides del selector manual. Se validan contra las columnas REALES y
    # permitidas del rol; un nombre que no exista no llega al SQL. Si vienen
    # metrica, fecha y horizonte, el analisis no necesita llamar al LLM.
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    dimension_column: Optional[str] = None
    # Agregación pedida. `count` usa `metric_column="*"`; `count_distinct`
    # requiere una columna de identificador; `formula` es una métrica
    # pre-agregada del catálogo. Si se omite, se usa la de la opción resuelta.
    metric_agg: Optional[
        Literal["sum", "count", "count_distinct", "formula"]
    ] = None
    # Granularidad manual del eje. `None` conserva el contrato histórico: mes.
    granularity: Optional[Literal["day", "week", "month", "quarter"]] = None
    # Escape hatch para auditar un mes en curso: por defecto se excluye porque un
    # periodo incompleto hunde el ultimo punto y el backtest lo lee como caida.
    include_partial_period: bool = False


class ProjectionEvidence(BaseModel):
    """Punto, intervalo y evidencia comunes a un paso y al total."""

    point: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    band_pct: Optional[float] = None
    worst_case_pct: Optional[float] = None
    band_method: Optional[str] = None
    interval_lower_pct: Optional[float] = None
    interval_upper_pct: Optional[float] = None
    interval_coverage: Optional[float] = None
    interval_coverage_n: int = 0
    interval_target: Optional[float] = None
    mape: Optional[float] = None
    n_backtests: int = 0
    reliable: bool = False
    extrapolated: bool = False
    # La banda inferior se acotó a 0 (métrica de conteo: no hay cantidades
    # negativas). El `band_pct` sigue siendo la banda calibrada.
    lower_clamped: bool = False


class ProjectionStep(ProjectionEvidence):
    """Un paso futuro publicado, con su banda y su evidencia.

    `period` lo agrega el servicio con `next_period` del forecast existente
    (aritmetica de calendario compartida, no una copia). `reliable` y
    `extrapolated` viajan SIEMPRE: un paso sin backtest propio hereda la banda
    del ultimo medido y quien lee tiene que poder distinguirlos.
    """

    step: int
    period: Optional[str] = None


class ProjectionTotal(ProjectionEvidence):
    """Total del horizonte con la evidencia que lo sostiene.

    `measured_steps` dice cuantos pasos del total se midieron de verdad. Si es
    menor que `horizon`, `extrapolated=True` y `mape=None`: la banda existe, pero
    es la de un prefijo mas corto y no se presenta como medicion del total
    completo. `measured_steps=None` significa que no hubo ni un prefijo medible;
    en ese caso el total viaja sin banda.
    """

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
    top_n: int = POLICY.breakdown_top_n
    groups: List[ProjectionGroupSummary] = Field(default_factory=list)


class ProjectionQuality(BaseModel):
    """Estado agregado de calidad y las razones que lo dispararon.

    No es un score: `status` sale de reglas explícitas sobre MAPE, cobertura,
    naive, backtests, huecos y las alertas del diagnóstico (último dato lejos de
    la media, cambio de nivel, valores atípicos). `reasons` es lo que la UI
    muestra para que el lector sepa POR QUÉ, no solo el color.
    """

    status: str  # "confiable" | "exploratoria" | "no_confiable" | "sin_medicion"
    mape: Optional[float] = None
    unreliable_threshold: Optional[float] = None
    exploratory_threshold: Optional[float] = None
    interval_target: Optional[float] = None
    interval_coverage: Optional[float] = None
    interval_coverage_n: int = 0
    band_method: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class ProjectionScenario(BaseModel):
    """Supuesto de crecimiento aplicado a la base historica.

    No es un pronostico estadistico: no hay backtest, ni banda, ni MAPE. Se
    publica separado para que la UI no lo presente con el semaforo de calidad.
    """

    growth_pct: float
    annual: bool = True
    base_value: float
    base_period: Optional[str] = None
    final_value: float
    gain: float
    label: str = "Escenario de crecimiento"


class ProjectionDiagnosis(BaseModel):
    """Lectura de la serie historica que sostiene —o no— la proyeccion.

    No describe el futuro: describe la historia con la que se proyecto (tendencia,
    dispersion, ultimo valor contra la media) y las advertencias que el lector
    tiene que ver ANTES de creerle al numero. `max_recommended_horizon` es un
    limite sugerido por la evidencia, no un recorte del horizonte publicado.
    """

    summary: str
    trend: Literal["creciente", "estable", "decreciente"]
    cv_pct: Optional[float] = None
    last_vs_mean_pct: Optional[float] = None
    seasonality: Literal[
        "sin_estacionalidad_clara", "posible_estacionalidad", "insuficiente_historia"
    ]
    warnings: List[str] = Field(default_factory=list)
    max_recommended_horizon: Optional[int] = None
    # Calidad de la serie. Todos opcionales: un cliente viejo no los manda y un
    # diagnostico corto no puede afirmarlos. `level_shift` acepta el flag o la
    # magnitud segun lo que mida el diagnostico; `level_shift_pct` es el tamano.
    season_length: Optional[int] = None
    seasonality_strength: Optional[float] = None
    trend_pct_per_step: Optional[float] = None
    level_shift: Optional[Union[bool, float]] = None
    level_shift_pct: Optional[float] = None
    outlier_count: Optional[int] = None
    missing_periods: Optional[int] = None
    usable_periods: Optional[int] = None
    reliable_horizon: Optional[int] = None


class ScenarioComparisonItem(BaseModel):
    """Un supuesto de crecimiento dentro de la tabla comparativa.

    `total` es la suma de los pasos del escenario y `final_value` su ultimo paso.
    `gain_vs_base` compara el TOTAL del escenario contra el total de la proyeccion
    estadistica (`base_total`), que es la respuesta que se publicaria sin
    supuesto: un escenario no se compara contra el historico sino contra el
    pronostico, que es la alternativa real del lector. `label` explica de donde
    sale la tasa (ritmo historico, conservador, optimista, supuesto del usuario).
    """

    growth_pct: float
    label: Optional[str] = None
    total: Optional[float] = None
    final_value: Optional[float] = None
    gain_vs_base: Optional[float] = None
    gain_vs_base_pct: Optional[float] = None


class ProjectionComparison(BaseModel):
    """Tabla de escenarios contra la proyeccion base, para elegir un supuesto.

    Solo viaja en el modo escenario: publicarla sin un supuesto pedido seria
    presentar hipotesis como si fueran la conclusion del analisis.
    """

    base_total: Optional[float] = None
    base_final: Optional[float] = None
    scenarios: List[ScenarioComparisonItem] = Field(default_factory=list)


class ProjectionModelScore(BaseModel):
    """Una fila de la comparacion de metodos del backtest.

    `selected=True` marca el metodo que se publico. MASE es la metrica de
    seleccion; MAPE/MAE/RMSE viajan para que la eleccion se pueda auditar.
    """

    method: str
    # Etiqueta legible del metodo; el codigo queda para auditar la seleccion.
    label: Optional[str] = None
    mape: Optional[float] = None
    mae: Optional[float] = None
    rmse: Optional[float] = None
    mase: Optional[float] = None
    selected: bool = False


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
    # Escenario de crecimiento (supuesto), separado de la proyeccion estadistica.
    scenario: Optional[ProjectionScenario] = None
    # Escenarios derivados de la historia (ritmo, conservador, optimista) mas
    # el supuesto pedido. Solo en modo escenario.
    comparison: Optional[ProjectionComparison] = None
    # Lectura de la serie historica: viaja incluso cuando no se pudo proyectar,
    # porque el motivo ("poca historia") es justamente lo que describe.
    diagnosis: Optional[ProjectionDiagnosis] = None
    # Que espera responder la pregunta: el total del horizonte o un grupo del
    # desglose. Lo decide el backend sobre el texto, no el cliente.
    answer_focus: Literal["total", "dimension"] = "total"
    method: Optional[str] = None
    # Etiqueta y mecanica publicadas por el backend: la UI no las inventa y la
    # narrativa tampoco. Opcionales para snapshots viejos.
    method_label: Optional[str] = None
    method_description: Optional[str] = None
    beats_naive: Optional[bool] = None
    mape: Optional[float] = None
    mape_by_method: Dict[str, Optional[float]] = {}
    quality: Optional[ProjectionQuality] = None
    n_periods: int = 0
    has_gaps: bool = False
    # Contrato nuevo (plan 010): la politica vigente y la evidencia completa del
    # backtest. Todo opcional para que un snapshot viejo siga siendo legible.
    policy: Optional[ProjectionPolicyPublic] = None
    metrics_by_method: Dict[str, Dict[str, Optional[float]]] = {}
    model_comparison: List[ProjectionModelScore] = []
    mase: Optional[float] = None
    mae: Optional[float] = None
    rmse: Optional[float] = None
    # Pasos que la HISTORIA sostiene (`policy.max_horizon_for(n)` y
    # `reliable_horizon_for(n)`); 0 significa que ni el primer paso tiene
    # historia. No recortan el horizonte pedido: lo recomiendan.
    max_horizon: Optional[int] = None
    min_periods: Optional[int] = None
    reliable_horizon: Optional[int] = None
    can_forecast: Optional[bool] = None
    guidance: Optional[str] = None
    interval_target: Optional[float] = None
    # Periodo en curso excluido del calculo (o None si no se excluyo ninguno).
    partial_period_excluded: Optional[str] = None
    # Periodos posteriores al mes actual descartados por no ser historia.
    future_periods_excluded: List[str] = []
    income_only: bool = True
    sql: Optional[str] = None
    table: Optional[str] = None
    table_source: Optional[str] = None
    metric_column: Optional[str] = None
    metric_agg: str = "sum"
    granularity: str = "month"
    date_column: Optional[str] = None
    # Conexion sobre la que se calculo: la verificacion la necesita para volver
    # a leer los datos actuales con los MISMOS gates. Las corridas viejas no la
    # tienen y se muestran como no verificables.
    connection_id: Optional[int] = None
    analysis_source: Optional[str] = None  # "llm" | "heuristica" | "manual"
    narration: Optional[str] = None
    narration_source: Optional[str] = None  # "llm" | "plantilla"
    audit_log_id: Optional[int] = None


class ProjectionEstimateRequest(_ProjectionRequestBase):
    """Pedido de estimacion. Mismo tope de pregunta, mismo rango y Auto.

    Hereda `horizon`/`scenario_growth_pct` y sus validaciones de la corrida: el
    escenario explicito permite el horizonte de escenario.
    """

    question: str = Field(..., max_length=MAX_QUESTION_CHARS)


class ProjectionEstimateResponse(BaseModel):
    """Estimacion instantanea. `source="fallback"` declara que NO hubo medicion.

    `horizon_used` es el horizonte con el que se calculo la estimacion: la
    pregunta manda, y sin horizonte en el texto se usa el default declarado.
    """

    estimated_seconds: float
    source: str  # "medido" | "fallback"
    horizon_used: int = DEFAULT_HORIZON


class ProjectionColumnOption(BaseModel):
    """Una columna ofrecida al selector, con la evidencia que la sostiene.

    `source` dice de dónde salió la clasificación (`catalogo`, `perfil`, `tipo`,
    `sintetica`); `confidence` es alta/media/baja. La expresión SQL NO viaja:
    el cliente manda el nombre y el backend la vuelve a resolver.
    """

    column: str
    label: str
    source: str
    confidence: str
    evidence: Optional[str] = None
    min: Optional[str] = None
    max: Optional[str] = None


class ProjectionMetricOption(ProjectionColumnOption):
    """Columna + agregación: `sum`, `count`, `count_distinct` o `formula`."""

    agg: str


class ProjectionTableOption(BaseModel):
    """Tabla candidata del selector, con su conteo y nombre de negocio."""

    name: str
    row_count: int
    friendly_name: Optional[str] = None


class ProjectionContextResponse(BaseModel):
    """Columnas que el selector manual puede ofrecer, ya filtradas por el rol.

    Solo nombres que existen en los metadatos de la fact table autorizada y que
    el rol puede leer. `available=False` explica por que no hay contexto (sin
    rol, sin tablas, conexion caida) sin devolver una lista inventada.

    Las listas `metric_columns`/`date_columns`/`dimension_columns` se conservan
    para los clientes viejos; `date_options`/`metric_options`/
    `identifier_options`/`count_option` llevan la evidencia del resolvedor.
    """

    available: bool = True
    reason: Optional[str] = None
    connection_id: int
    table: Optional[str] = None
    table_source: Optional[str] = None
    tables: List[ProjectionTableOption] = Field(default_factory=list)
    metric_columns: List[str] = Field(default_factory=list)
    date_columns: List[str] = Field(default_factory=list)
    dimension_columns: List[str] = Field(default_factory=list)
    date_options: List[ProjectionColumnOption] = Field(default_factory=list)
    metric_options: List[ProjectionMetricOption] = Field(default_factory=list)
    identifier_options: List[ProjectionMetricOption] = Field(default_factory=list)
    count_option: Optional[ProjectionMetricOption] = None
    granularities: List[str] = Field(default_factory=lambda: ["day", "week", "month", "quarter"])
    default_granularity: str = "month"
    min_periods: int = POLICY.min_periods


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
    # El snapshot se puede reabrir como resultado publicado. Es distinto de
    # `verifiable`: una corrida sin conexion guardada no se puede comparar con el
    # dato actual, pero su detalle historico sigue siendo legible.
    readable: bool = False
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
    metric_agg: str = "sum"
    granularity: str = "month"
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
