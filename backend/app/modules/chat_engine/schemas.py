from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, Field

# Tope de la pregunta. `question` entra crudo en el prompt del LLM, asi que sin
# cota un cliente puede pedir un string arbitrariamente largo: se paga en tokens y
# el contexto desborda, y el fallo aparece como "el modelo no respondio" en vez
# de como un 400 que dice cual fue el problema.
#
# 2000 caracteres es holgado para una pregunta de negocio (unas 300 palabras).
MAX_QUESTION_CHARS = 2000

# Historial. `format_conversation_context` solo lee los ultimos 2 turnos, asi que
# mandar mas no aporta nada y solo infla la peticion.
MAX_HISTORY_TURNS = 20

class QueryRequest(BaseModel):
    question: str = Field(..., max_length=MAX_QUESTION_CHARS)
    connection_id: int = 1
    session_id: Optional[str] = None
    user_role: Optional[str] = "Economista"
    conversation_history: Optional[List[Dict[str, Any]]] = Field(
        default=None, max_length=MAX_HISTORY_TURNS
    )
    # Config LLM de Ajustes que viaja POR REQUEST. El valor crudo NUNCA se usa
    # para emitir requests: `llm_base_url` pasa por `resolve_user_llm_config`
    # (politica de `LLM_MODE`) en el router, y el sink la vuelve a validar con
    # la política más amplia del despliegue.
    llm_base_url: Optional[str] = Field(default=None, max_length=500)
    llm_model: Optional[str] = Field(default=None, max_length=200)

class KPICard(BaseModel):
    title: str
    value: str
    subtitle: Optional[str] = None
    change_direction: Optional[str] = "neutral" # positive | negative | neutral

class ExecutiveReport(BaseModel):
    overview: str
    key_findings: List[str] = []
    recommendations: List[str] = []
    risk_level: Optional[str] = "BAJO" # BAJO | MEDIO | ALTO | CRITICO
    business_impact: Optional[str] = None

class ForecastCard(BaseModel):
    """Prediccion del proximo periodo, con la incertidumbre que se midio.

    `mape` y `band_pct` NO son decorativos ni opcionales. Un forecast sin su
    error historico al lado es un numero sin informacion: la banda se calcula
    por replay sobre la serie real (`forecast_calculator`), asi que el usuario
    ve exactamente cuanto le puede costar creerla.
    """
    available: bool
    period: Optional[str] = None
    point: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    band_pct: Optional[float] = None
    mape: Optional[float] = None
    method: Optional[str] = None
    n_periods: int = 0
    n_backtests: int = 0
    reliable: bool = False
    has_gaps: bool = False
    reason: Optional[str] = None # Por que no se publico forecast, en palabras del usuario
    table: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    income_only: bool = True
    series: List[Dict[str, Any]] = []
    sql: Optional[str] = None # El SQL real que produjo el numero, auditable

class RetentionTier(BaseModel):
    tier: str
    casos: int
    retornaron: int
    prob: Optional[float] = None # None = evidencia insuficiente, no "0%"
    evidence_sufficient: bool = False

class RetentionClient(BaseModel):
    entity: str
    months_active: int
    last_purchase: str
    months_since_last: int
    revenue: float
    revenue_share: float
    tier: str
    return_prob: Optional[float] = None
    evidence_sufficient: bool = False

class RetentionReport(BaseModel):
    total_clients: int = 0
    total_revenue: float = 0.0
    last_period: Optional[str] = None
    tiers: List[RetentionTier] = []
    top: List[RetentionClient] = []
    truncated_by_limit: bool = False
    entity_column: Optional[str] = None
    metric_column: Optional[str] = None
    date_column: Optional[str] = None
    income_only: bool = True
    sql: Optional[str] = None
    reason: Optional[str] = None # Por que no se pudo medir, en palabras del usuario

class PredictionRequest(BaseModel):
    """Que prediccion correr. Los tres flags existen para no pagar lo que no se pidio.

    La auditoria de calidad hace COUNTs sobre la fact table; correrla siempre
    suma latencia a una consulta que solo queria el forecast. Se pide explicita.
    """
    question: Optional[str] = None
    connection_id: int = 1
    include_forecast: bool = True
    include_retention: bool = True
    include_data_quality: bool = False
    top_limit: int = 50 # Cuantos clientes se devuelven, NO cuantos se analizan

class PredictionResponse(BaseModel):
    """Lo que ve el usuario cuando pide una prediccion.

    Un solo envelope para forecast y retencion porque la pregunta del cliente es
    una sola ("prediccion") y partirla en dos endpoints obliga al frontend a
    decidir cual llamar antes de saber si hay datos suficientes.
    """
    question: Optional[str] = None
    forecast: Optional[ForecastCard] = None
    retention: Optional[RetentionReport] = None
    # Fallos PARCIALES: son dos predicciones independientes sobre la misma base y
    # que una no se pueda calcular no dice nada de la otra. Sin este campo el
    # endpoint responderia 200 con un bloque en null y el usuario no sabria si es
    # que no aplica o que se rompio.
    errors: List[str] = []
    data_quality: List[Dict[str, Any]] = [] # Defectos detectados, solo lectura
    audit_log_id: Optional[int] = None

class TraceabilityAudit(BaseModel):
    sql_executed: str
    execution_time_ms: int
    rows_returned: int
    # NULL = no hay un estado de validacion que afirmar. No es "APROBADO":
    # el fallback conversacional no puede inventar una aprobacion cuando el
    # grounding query fue rechazado o no se ejecuto.
    validation_status: Optional[str] = None
    schema_tables_used: List[str]
    explanation: str
    audit_log_id: Optional[int] = None
    # Contra que base se ejecuto este SQL. Sin esto el texto copiado al
    # portapapeles es ambiguo: las mismas tablas existen en varias bases y el
    # mismo SQL pegado en otra da "no existe la relacion" o, peor, datos de otra
    # fuente. El dato ya vivia en `audit_logs.target_database`; aqui se le
    # entrega a quien va a verificar la consulta a mano.
    target_database: Optional[str] = None

class PresentationHints(BaseModel):
    show_executive_report: bool = True
    show_kpis: bool = True
    show_chart: bool = True
    preferred_view: str = "assistant"  # assistant | report | table
    summary_style: str = "detailed"  # concise | detailed | executive

class QueryResponse(BaseModel):
    question: str
    summary_text: str
    executive_report: Optional[ExecutiveReport] = None
    kpis: List[KPICard] = []
    chart_type: str = "bar" # bar | line | area | pie | donut | none
    chart_option: Dict[str, Any] = {} # ECharts option JSON object
    data_columns: List[str] = []
    data_rows: List[Dict[str, Any]] = []
    response_type: str = "data_analysis" # data_analysis | advisory | explanation | report | hybrid
    conversational_response: Optional[str] = None # Respuesta conversacional estructurada
    grounding_info: Optional[str] = None # Información de las tablas/registros reales de la BD consultados
    presentation_hints: Optional[PresentationHints] = None
    suggested_questions: List[str] = [] # Preguntas sugeridas de seguimiento (Next Best Questions)
    clarification_options: List[str] = [] # Opciones interactivas ante ambigüedad
    anomalies_detected: List[Dict[str, Any]] = [] # Alertas de anomalías estadísticas detectadas
    sql_explanation: Optional[str] = None # Explicación de consulta en lenguaje ciudadano
    nulls_detected: Optional[Dict[str, Any]] = None # Detección e intercepción proactiva de nulos para Opción 4
    traceability: TraceabilityAudit
    audit_log_id: Optional[int] = None
    # Modulo C: por que no hay tablas que consultar. Es una clave
    # DISCRIMINANTE, no texto que la UI tenga que parsear: `True` = no hay
    # ninguna conexion activa (nadie encendio una base), `None` = no se pudo
    # comprobar. Ausente en el resto de respuestas, incluido el rechazo de
    # RBAC de `build_rbac_denied_response`, que es otro diagnostico.
    no_active_connection: Optional[bool] = None
    # Accion que el admin puede ejecutar desde el chat. `None` para cualquier
    # otro perfil: el boton seria un rechazo guaranteed del backend.
    activate_connection_action: Optional[Dict[str, Any]] = None

class SuggestionsResponse(BaseModel):
    user_role: Optional[str] = None
    allowed_tables: Optional[List[str]] = None
    suggestions: List[str] = []

class ChatThreadCreate(BaseModel):
    id: str
    title: str
    connection_id: Optional[int] = 1
    results: List[Dict[str, Any]] = []
    # Opt-in explicito: sin esto el hilo es privado y /threads/shared/{id} responde 404.
    is_shared: bool = False

class ChatThreadSummary(BaseModel):
    id: str
    title: str
    connection_id: Optional[int] = 1
    message_count: int
    updated_at: str

class ChatThreadDetail(BaseModel):
    id: str
    title: str
    connection_id: Optional[int] = 1
    results: List[Dict[str, Any]] = []
    created_at: str
    updated_at: str

class ChatFeedbackRequest(BaseModel):
    audit_log_id: Optional[int] = None
    question: str
    sql: Optional[str] = None
    connection_id: int = 1
    rating: str  # positive | negative
    comment: Optional[str] = None
    is_golden: Optional[bool] = False

class GoldenQueryRequest(BaseModel):
    question: str
    sql: str
    connection_id: int = 1
    is_golden: bool = True

class ChatFeedbackResponse(BaseModel):
    success: bool
    message: str
    learning_saved: bool = False

class GoldenQueryOut(BaseModel):
    """Fila de `query_learning_memories` tal como la ve el admin.

    `user_role` NO es el autor: la tabla no tiene `user_id`, asi que no se puede
    afirmar quien escribio el SQL. `execution_count` es el uso real medido ( veces
    inyectada en el prompt de la conexion) y es el dato que justifica la pantalla.
    """
    id: int
    question_pattern: str
    successful_sql: str
    user_role: Optional[str] = None
    is_golden: bool = False
    execution_count: int = 0
    was_self_healed: bool = False
    created_at: str
    updated_at: str

class GoldenQueryList(BaseModel):
    items: List[GoldenQueryOut] = []
    total: int = 0

class DashboardWidgetCreate(BaseModel):
    title: str
    connection_id: Optional[int] = 1
    chart_type: str = "bar"
    chart_option_json: str
    kpis_json: Optional[str] = None
    query_text: Optional[str] = None

class DashboardWidgetOut(BaseModel):
    id: int
    user_id: int
    title: str
    connection_id: Optional[int] = 1
    chart_type: str = "bar"
    chart_option_json: str
    kpis_json: Optional[str] = None
    query_text: Optional[str] = None
    created_at: str

    model_config = ConfigDict(from_attributes=True)

