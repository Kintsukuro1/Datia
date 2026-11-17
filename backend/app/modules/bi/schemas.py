"""Contratos Pydantic del módulo BI."""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

BIChartType = Literal["bar", "hbar", "line", "area", "pie", "kpi", "table"]
BIAggregation = Literal["sum", "avg", "count", "count_distinct", "min", "max"]
BIFilterOp = Literal["=", "!=", ">", "<", ">=", "<=", "contains"]
BISort = Literal["value_desc", "value_asc", "label_asc", "label_desc"]
BIGrain = Literal["day", "month", "year"]
BISize = Literal["small", "wide", "full"]


class BIConnectionOut(BaseModel):
    id: int
    name: str
    db_type: str
    is_active: bool


class BIColumn(BaseModel):
    name: str
    type: str = ""
    kind: Literal["date", "number", "text"]
    masked: bool = False


class BITable(BaseModel):
    name: str
    columns: List[BIColumn]


class BISchemaResponse(BaseModel):
    connection_id: int
    connection_name: str
    is_admin: bool
    tables: List[BITable]


class BIFilter(BaseModel):
    column: str = Field(..., max_length=128)
    op: BIFilterOp = "="
    value: Union[str, int, float] = ""


class BIVisualSpec(BaseModel):
    connection_id: int
    table: str = Field(..., max_length=128)
    chart_type: BIChartType = "bar"
    dimension: Optional[str] = Field(None, max_length=128)
    date_grain: Optional[BIGrain] = None
    measure: Optional[str] = Field(None, max_length=128)
    aggregation: BIAggregation = "sum"
    filters: List[BIFilter] = Field(default_factory=list, max_length=5)
    sort: BISort = "value_desc"
    limit: int = Field(20, ge=1, le=500)
    # Ancho del visual en la grilla del panel. Default "small" para que los
    # visuales guardados antes de este campo sigan viéndose igual.
    size: BISize = "small"


class BIQueryResult(BaseModel):
    rows: List[Dict[str, Any]]
    sql: str
    execution_time_ms: int
    masked_label: bool = False
    audit_log_id: Optional[int] = None


class BIVisualCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    panel_name: str = Field("Mi panel", min_length=1, max_length=120)
    spec: BIVisualSpec


class BIVisualUpdate(BaseModel):
    """Edición de un visual guardado.

    El contrato es el mismo que el de creación a propósito: el backend vuelve a
    ejecutar la consulta con los permisos de hoy antes de aceptar el cambio.
    """

    title: str = Field(..., min_length=1, max_length=255)
    panel_name: str = Field("Mi panel", min_length=1, max_length=120)
    spec: BIVisualSpec


class BIVisualReorder(BaseModel):
    """Orden completo de los visuales de UN panel.

    Se exige la lista completa (no un delta) para que el resultado sea
    idempotente y el orden final quede definido en un solo lugar.
    """

    panel_name: str = Field(..., min_length=1, max_length=120)
    ids: List[int] = Field(..., min_length=1, max_length=100)


class BIVisualOut(BaseModel):
    id: int
    title: str
    panel_name: str
    position: int
    spec: BIVisualSpec
    created_at: str


class BIConnectionRef(BaseModel):
    id: int
    name: str


class BIRoles(BaseModel):
    date: Optional[str] = None
    group: Optional[str] = None
    category: Optional[str] = None
    measure: str = "count"


class BIPeriod(BaseModel):
    years: List[int] = Field(default_factory=list)
    months: List[int] = Field(default_factory=list)
    year: Optional[int] = None
    month: Optional[int] = None


class BIOptions(BaseModel):
    group: List[str] = Field(default_factory=list)
    category: List[str] = Field(default_factory=list)


class BIKpis(BaseModel):
    registros: int = 0
    total: Optional[float] = None
    # Comparación contra el período anterior (mes anterior, o año anterior si
    # se mira todo el año). `None` cuando no hay historial comparable: el
    # frontend muestra "sin comparación" en vez de un cero que mentiría.
    total_anterior: Optional[float] = None
    delta_pct: Optional[float] = None
    grupos: Optional[int] = None
    dias: Optional[int] = None
    promedio_diario: Optional[float] = None
    promedio_por_grupo: Optional[float] = None


class BIPoint(BaseModel):
    label: str
    value: Optional[float] = None


class BIMatrixRow(BaseModel):
    label: str
    cells: Dict[str, float] = Field(default_factory=dict)
    total: float = 0


class BIMatrix(BaseModel):
    days: List[str] = Field(default_factory=list)
    rows: List[BIMatrixRow] = Field(default_factory=list)


class BICrossRow(BaseModel):
    label: str
    cells: Dict[str, float] = Field(default_factory=dict)
    total: float = 0


class BICross(BaseModel):
    categories: List[str] = Field(default_factory=list)
    rows: List[BICrossRow] = Field(default_factory=list)


class BIDetail(BaseModel):
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)


class BIReportResponse(BaseModel):
    connection: BIConnectionRef
    table: BITable
    roles: BIRoles
    period: BIPeriod
    options: BIOptions
    kpis: BIKpis
    by_group: List[BIPoint] = Field(default_factory=list)
    by_category: List[BIPoint] = Field(default_factory=list)
    daily: List[BIPoint] = Field(default_factory=list)
    matrix: Optional[BIMatrix] = None
    cross: Optional[BICross] = None
    detail: BIDetail = Field(default_factory=BIDetail)
    audit_log_id: Optional[int] = None
