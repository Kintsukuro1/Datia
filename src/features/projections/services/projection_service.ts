import { apiClient } from '../../../shared/api/api_client';

/**
 * Cliente de `/ml/projections` (contrato en `backend/app/modules/ml_forecast/schemas.py`).
 *
 * Regla del modulo: aca NO se rellena nada. Si el backend publica `null` es
 * porque no hay evidencia para ese numero (MAPE de un paso sin backtest,
 * banda de un paso extrapolado, punto no calculado), y convertirlo en 0 en el
 * cliente seria inventar una afirmacion sobre el futuro. Los componentes
 * muestran "—" y "sin evidencia" a partir del `null`, nunca un cero.
 */

interface ProjectionStep {
  step: number;
  period: string | null;
  point: number | null;
  lower: number | null;
  upper: number | null;
  band_pct: number | null;
  mape: number | null;
  n_backtests: number;
  reliable: boolean;
  extrapolated: boolean;
}

export interface ProjectionTotal {
  point: number | null;
  lower: number | null;
  upper: number | null;
  band_pct: number | null;
  mape: number | null;
  n_backtests: number;
  reliable: boolean;
  extrapolated: boolean;
  /** Pasos del total que se midieron de verdad; null = no hubo prefijo medible. */
  measured_steps: number | null;
}

export interface ProjectionGroupSummary {
  value: string;
  available: boolean;
  reason?: string | null;
  method?: string | null;
  mape?: number | null;
  n_periods: number;
  has_gaps: boolean;
  total?: ProjectionTotal | null;
}

export interface ProjectionBreakdown {
  available: boolean;
  reason?: string | null;
  dimension_column?: string | null;
  top_n: number;
  groups: ProjectionGroupSummary[];
}

export interface ProjectionResult {
  available: boolean;
  reason?: string | null;
  question: string;
  /** Horizonte RESUELTO por el backend, no el pedido (Auto puede cambiarlo). */
  horizon: number;
  /** "pedido" | "llm" | "default": quien fijo el horizonte publicado. */
  horizon_source?: string | null;
  /** Historico `{periodo, valor}`: la serie real que alimenta al motor. */
  series: Record<string, any>[];
  steps: ProjectionStep[];
  total?: ProjectionTotal | null;
  /** Top de grupos por volumen historico; no reemplaza el total general. */
  breakdown?: ProjectionBreakdown | null;
  method?: string | null;
  beats_naive?: boolean | null;
  mape?: number | null;
  mape_by_method: Record<string, number | null>;
  n_periods: number;
  has_gaps: boolean;
  /** Periodo en curso excluido por incompleto; null si no se excluyo ninguno. */
  partial_period_excluded?: string | null;
  /** Periodos posteriores al mes actual descartados por no ser historia. */
  future_periods_excluded?: string[];
  income_only: boolean;
  sql?: string | null;
  table?: string | null;
  metric_column?: string | null;
  date_column?: string | null;
  /** Conexion usada; la verificacion la necesita para releer el dato actual. */
  connection_id?: number | null;
  /** "llm" | "heuristica" | "manual": quien eligio la metrica, no los numeros. */
  analysis_source?: string | null;
  narration?: string | null;
  /** "llm" | "plantilla". `plantilla` declara que el modelo local no respondio. */
  narration_source?: string | null;
  audit_log_id?: number | null;
}

export interface ProjectionEstimate {
  estimated_seconds: number;
  /** "medido" | "fallback". `fallback` declara que NO hubo medicion del modelo. */
  source: string;
  /** Horizonte con el que se estimo. Con Auto es 12 (el LLM aun no respondio). */
  horizon_used?: number;
}

export interface ProjectionContext {
  available: boolean;
  reason?: string | null;
  connection_id: number;
  table?: string | null;
  metric_columns: string[];
  date_columns: string[];
  dimension_columns: string[];
}

export interface ProjectionHistoryItem {
  audit_log_id: number;
  timestamp: string;
  question: string;
  horizon: number;
  table?: string | null;
  metric_column?: string | null;
  date_column?: string | null;
  connection_id?: number | null;
  total_point?: number | null;
  verifiable: boolean;
  reason?: string | null;
}

export interface ProjectionHistory {
  items: ProjectionHistoryItem[];
}

export interface ProjectionVerificationStep {
  step: number;
  period?: string | null;
  projected?: number | null;
  actual?: number | null;
  error_pct?: number | null;
  inside_band?: boolean | null;
  status: 'medido' | 'pendiente' | 'sin_dato' | 'sin_periodo' | string;
}

export interface ProjectionVerificationTotal {
  projected?: number | null;
  actual?: number | null;
  error_pct?: number | null;
  inside_band?: boolean | null;
  status: 'medido' | 'incompleto' | string;
}

export interface ProjectionVerification {
  available: boolean;
  reason?: string | null;
  audit_log_id: number;
  question: string;
  table?: string | null;
  metric_column?: string | null;
  date_column?: string | null;
  horizon: number;
  method?: string | null;
  steps: ProjectionVerificationStep[];
  total?: ProjectionVerificationTotal | null;
  measured_steps: number;
  pending_steps: number;
  missing_steps: number;
  inside_band_steps: number;
  outside_band_steps: number;
  data_note?: string | null;
}

export interface ProjectionOverrides {
  metricColumn?: string | null;
  dateColumn?: string | null;
  dimensionColumn?: string | null;
}

export const projectionService = {
  /**
   * Estimacion instantanea de lo que va a tardar la corrida. No lee la base ni
   * llama al LLM (asi lo declara el backend): es para la barra de la pantalla
   * de generacion, no para prometer precision.
   */
  async estimate(question: string, horizon: number | null): Promise<ProjectionEstimate> {
    const res = await apiClient.post<ProjectionEstimate>('/ml/projections/estimate', {
      question,
      horizon,
    });
    return res.data;
  },

  /**
   * Corrida completa: analisis de la pregunta + walk-forward + narrativa.
   *
   * Sin catch: un fallo de red o un 500 tienen que llegar como error, no como
   * un `available: false` fabricado — ese estado es una respuesta real del
   * backend (serie corta, columna bloqueada) con su motivo, y mezclarlos deja
   * al usuario sin poder distinguir "no se pudo calcular" de "no se pudo pedir".
   */
  async run(
    question: string,
    connectionId: number | null,
    horizon: number | null,
    overrides?: ProjectionOverrides
  ): Promise<ProjectionResult> {
    // `horizon: null` es una instruccion (Auto), no un campo faltante: se manda
    // explicito para que el backend deje que el analisis lo ajuste.
    const payload: Record<string, any> = { question, horizon };
    // Sin conexion elegida el campo se omite (el backend cae a su default),
    // mandar `null` explicitamente seria otra cosa de la que dijimos.
    if (connectionId) {
      payload.connection_id = connectionId;
    }
    // Un override vacio es "Automatico", no un nombre de columna: se omite.
    if (overrides?.metricColumn) payload.metric_column = overrides.metricColumn;
    if (overrides?.dateColumn) payload.date_column = overrides.dateColumn;
    if (overrides?.dimensionColumn) payload.dimension_column = overrides.dimensionColumn;
    const res = await apiClient.post<ProjectionResult>('/ml/projections/run', payload);
    return res.data;
  },

  /** Columnas que el rol puede elegir en el selector manual. */
  async context(connectionId: number | null): Promise<ProjectionContext> {
    const res = await apiClient.get<ProjectionContext>('/ml/projections/context', {
      params: connectionId ? { connection_id: connectionId } : undefined,
    });
    return res.data;
  },

  /** Ultimas proyecciones del usuario autenticado (nunca las de otro). */
  async history(limit = 10): Promise<ProjectionHistory> {
    const res = await apiClient.get<ProjectionHistory>('/ml/projections/history', {
      params: { limit },
    });
    return res.data;
  },

  /** Compara una proyeccion auditada contra el dato actual de periodos cerrados. */
  async verify(auditLogId: number): Promise<ProjectionVerification> {
    const res = await apiClient.get<ProjectionVerification>(
      `/ml/projections/${auditLogId}/verification`
    );
    return res.data;
  },
};
