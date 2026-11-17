export type BIChartType = 'bar' | 'hbar' | 'line' | 'area' | 'pie' | 'kpi' | 'table';
export type BIAggregation = 'sum' | 'avg' | 'count' | 'count_distinct' | 'min' | 'max';
export type BIFilterOp = '=' | '!=' | '>' | '<' | '>=' | '<=' | 'contains';
export type BISort = 'value_desc' | 'value_asc' | 'label_asc' | 'label_desc';
export type BIGrain = 'day' | 'month' | 'year';

export interface BIConnection {
  id: number;
  name: string;
  db_type: string;
  is_active: boolean;
}

export interface BIColumn {
  name: string;
  type: string;
  kind: 'number' | 'date' | 'text';
  masked: boolean;
}

export interface BITable {
  name: string;
  columns: BIColumn[];
}

export interface BISchema {
  connection_id: number;
  connection_name: string;
  is_admin: boolean;
  tables: BITable[];
}

export interface BIFilter {
  column: string;
  op: BIFilterOp;
  value: string;
}

export interface BIVisualSpec {
  connection_id: number;
  table: string;
  chart_type: BIChartType;
  dimension?: string | null;
  date_grain?: BIGrain | null;
  measure?: string | null;
  aggregation: BIAggregation;
  filters: BIFilter[];
  sort: BISort;
  limit: number;
  /** Ancho en la grilla del panel. Default del backend: `small`. */
  size?: 'small' | 'wide' | 'full';
}

export interface BIRow {
  label?: string | number | null;
  value: number | string | null;
}

export interface BIQueryResult {
  rows: BIRow[];
  sql: string;
  execution_time_ms: number;
  masked_label: boolean;
  audit_log_id?: number | null;
}

export interface BIVisual {
  id: number;
  title: string;
  panel_name: string;
  position: number;
  spec: BIVisualSpec;
  created_at: string;
}

export interface BIConnectionRef {
  id: number;
  name: string;
}

export interface BIRoles {
  date: string | null;
  group: string | null;
  category: string | null;
  measure: string;
}

export interface BIPeriod {
  years: number[];
  months: number[];
  year: number | null;
  month: number | null;
}

export interface BIOptions {
  group: string[];
  category: string[];
}

export interface BIKpis {
  registros: number;
  total: number | null;
  /** Medida del período anterior (mes o año previo). `null` = sin historial. */
  total_anterior?: number | null;
  delta_pct?: number | null;
  grupos: number | null;
  dias: number | null;
  promedio_diario: number | null;
  promedio_por_grupo: number | null;
}

export interface BIPoint {
  label: string;
  value: number | null;
}

export interface BIMatrixRow {
  label: string;
  cells: Record<string, number>;
  total: number;
}

export interface BIMatrix {
  days: string[];
  rows: BIMatrixRow[];
}

export interface BICrossRow {
  label: string;
  cells: Record<string, number>;
  total: number;
}

export interface BICross {
  categories: string[];
  rows: BICrossRow[];
}

export interface BIDetail {
  columns: string[];
  rows: Record<string, unknown>[];
}

export interface BIReport {
  connection: BIConnectionRef;
  table: BITable;
  roles: BIRoles;
  period: BIPeriod;
  options: BIOptions;
  kpis: BIKpis;
  by_group: BIPoint[];
  by_category: BIPoint[];
  daily: BIPoint[];
  matrix: BIMatrix | null;
  cross: BICross | null;
  detail: BIDetail;
  audit_log_id?: number | null;
}
