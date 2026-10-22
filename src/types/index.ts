import type { EChartsOption } from 'echarts';

export interface User {
  id: number;
  username: string;
  email?: string;
  is_admin: boolean;
  // Alta/baja de la cuenta. Opcional porque el usuario se persiste en
  // localStorage y un registro viejo (o un caller parcial) no lo trae;
  // el backend siempre lo manda.
  is_active?: boolean;
  // null = la cuenta no tiene rol. Es lo que manda el backend, asi que el tipo
  // tiene que decirlo: los callers resolvian ese null como si fuera un rol.
  role_name?: string | null;
  must_change_password?: boolean;
  temp_password_issued_at?: string | null;
  temp_password_expires_at?: string | null;
  failed_login_attempts?: number;
  locked_until?: string | null;
}

export interface UserCreationResult {
  message: string;
  temporary_password: string;
  user: User;
}

export interface UserSession {
  id: number;
  user_id: number;
  username?: string;
  jti: string;
  created_at: string;
  last_seen_at: string;
  ip_address?: string;
  user_agent?: string;
  is_revoked: boolean;
}

export interface AuditLog {
  id: number;
  timestamp: string;
  user_id?: number | null;
  username: string;
  user_role?: string | null;
  question_prompt: string;
  sql_generated?: string | null;
  validation_status: string;
  target_database?: string | null;
  execution_time_ms: number;
  rows_returned: number;
  error_message?: string | null;
  result_snapshot?: string | null;
}

export interface AuditLogsPage {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  items: AuditLog[];
}

export type AuditFilterParams = {
  start_date?: string;
  end_date?: string;
  username?: string;
  target_database?: string;
  validation_status?: string;
  page?: number;
  page_size?: number;
}

export interface PasswordResetResult {
  message: string;
  username: string;
  temporary_password: string;
  temp_password_expires_at?: string | null;
}

export interface KPICard {
  title: string;
  value: string;
  subtitle?: string;
  change_direction?: 'positive' | 'negative' | 'neutral';
}

interface ExecutiveReport {
  overview: string;
  key_findings: string[];
  recommendations: string[];
  risk_level?: 'BAJO' | 'MEDIO' | 'ALTO' | 'CRITICO';
  business_impact?: string;
}

export interface TraceabilityAudit {
  sql_executed: string;
  execution_time_ms: number;
  rows_returned: number;
  validation_status: string;
  schema_tables_used: string[];
  explanation: string;
  audit_log_id?: number;
  target_database?: string | null;
}

interface PresentationHints {
  show_executive_report?: boolean;
  show_kpis?: boolean;
  show_chart?: boolean;
  preferred_view?: 'assistant' | 'report' | 'table';
  summary_style?: 'concise' | 'detailed' | 'executive';
}

export interface QueryResult {
  id: string;
  question: string;
  timestamp: string;
  summary_text: string;
  executive_report?: ExecutiveReport;
  kpis: KPICard[];
  chart_type: 'bar' | 'line' | 'area' | 'pie' | 'donut' | 'none';
  chart_option: EChartsOption;
  data_columns: string[];
  data_rows: Record<string, unknown>[];
  traceability: TraceabilityAudit;
  pipeline_source?: 'backend' | 'llm_direct' | 'fallback';
  response_type?: 'data_analysis' | 'advisory' | 'explanation' | 'report' | 'hybrid' | 'greeting' | 'error' | 'out_of_scope';
  conversational_response?: string; // Respuesta conversacional estructurada
  grounding_info?: string; // Información de las tablas o registros reales de la BD consultados
  presentation_hints?: PresentationHints;
  suggested_questions?: string[];
  clarification_options?: string[];
  anomalies_detected?: Array<{
    column: string;
    row_index: number;
    entity: string;
    value: number;
    mean: number;
    stdev: number;
    z_score: number;
    direction: 'spike' | 'drop';
    description: string;
    probable_cause: string;
  }>;
  sql_explanation?: string;
  audit_log_id?: number;
  nulls_detected?: {
    has_nulls: boolean;
    table_name: string;
    columns_with_nulls: string[];
    null_rows_count: number;
    total_rows: number;
    options: Array<{
      action: string;
      label: string;
      prompt: string;
    }>;
  };
  /**
   * Modulo C: el chat no tiene tablas que consultar.
   *
   * `true` = no hay ninguna conexion activa (nadie encendio una base).
   * `null` = no se pudo comprobar el estado. Ausente en cualquier otra
   * respuesta, incluido el rechazo de RBAC, que es otro diagnostico.
   *
   * El boton de activar se lee de esta clave, no del texto del mensaje.
   */
  no_active_connection?: boolean | null;
  activate_connection_action?: {
    connection_id: number;
    connection_name: string;
    endpoint?: string;
  } | null;
}

export interface AppSettings {
  llm_provider: 'llama_cpp' | 'ollama' | 'openai_compatible' | 'custom';
  ollama_url: string;
  ollama_model: string;
  postgres_host: string;
  postgres_port: number;
  postgres_db: string;
  auto_detect_llm: boolean;
}

export interface ComponentHealth {
  name: string;
  type: 'llm' | 'metadata_db' | 'connector';
  status: 'OPERATIVO' | 'DEGRADADO' | 'CRITICO' | 'ERROR';
  latency_ms: number;
  message: string;
  details?: Record<string, unknown>;
}

export interface SystemHealthResponse {
  status: 'OPERATIVO' | 'DEGRADADO' | 'CRITICO';
  timestamp: string;
  llm_engine: ComponentHealth;
  metadata_db: ComponentHealth;
  corporate_connectors: ComponentHealth[];
  total_active_connectors: number;
  healthy_connectors_count: number;
}

export type ToastType = 'success' | 'warning' | 'error' | 'info';

export interface ToastNotification {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}
