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

export interface ProjectionStep {
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

export interface ProjectionResult {
  available: boolean;
  reason?: string | null;
  question: string;
  horizon: number;
  /** Historico `{periodo, valor}`: la serie real que alimenta al motor. */
  series: Record<string, any>[];
  steps: ProjectionStep[];
  method?: string | null;
  beats_naive?: boolean | null;
  mape?: number | null;
  mape_by_method: Record<string, number | null>;
  n_periods: number;
  has_gaps: boolean;
  income_only: boolean;
  sql?: string | null;
  table?: string | null;
  metric_column?: string | null;
  date_column?: string | null;
  /** "llm" | "heuristica": quien eligio la metrica, no quien calculo los numeros. */
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
}

export const projectionService = {
  /**
   * Estimacion instantanea de lo que va a tardar la corrida. No lee la base ni
   * llama al LLM (asi lo declara el backend): es para la barra de la pantalla
   * de generacion, no para prometer precision.
   */
  async estimate(question: string, horizon: number): Promise<ProjectionEstimate> {
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
    horizon: number
  ): Promise<ProjectionResult> {
    const payload: Record<string, any> = { question, horizon };
    // Sin conexion elegida el campo se omite (el backend cae a su default),
    // mandar `null` explicitamente seria otra cosa de la que dijimos.
    if (connectionId) {
      payload.connection_id = connectionId;
    }
    const res = await apiClient.post<ProjectionResult>('/ml/projections/run', payload);
    return res.data;
  },
};
