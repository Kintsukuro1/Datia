import { BIAggregation, BIFilterOp, BIRow, BISort, BIVisualSpec } from './types';

export const AGGREGATIONS: Array<{ id: BIAggregation; label: string; numericOnly: boolean }> = [
  { id: 'sum', label: 'Suma', numericOnly: true },
  { id: 'avg', label: 'Promedio', numericOnly: true },
  { id: 'count', label: 'Contar', numericOnly: false },
  { id: 'count_distinct', label: 'Contar distintos', numericOnly: false },
  { id: 'min', label: 'Mínimo', numericOnly: false },
  { id: 'max', label: 'Máximo', numericOnly: false },
];

export const SORTS: Array<{ id: BISort; label: string }> = [
  { id: 'value_desc', label: 'Mayor valor primero' },
  { id: 'value_asc', label: 'Menor valor primero' },
  { id: 'label_asc', label: 'Eje A → Z / más antiguo' },
  { id: 'label_desc', label: 'Eje Z → A / más reciente' },
];

export const FILTER_OPS: Array<{ id: BIFilterOp; label: string }> = [
  { id: '=', label: 'es igual a' },
  { id: '!=', label: 'es distinto de' },
  { id: 'contains', label: 'contiene' },
  { id: '>', label: 'mayor que' },
  { id: '>=', label: 'mayor o igual' },
  { id: '<', label: 'menor que' },
  { id: '<=', label: 'menor o igual' },
];

export const PALETTE = [
  '#006ee6',
  '#10B981',
  '#F59E0B',
  '#8B5CF6',
  '#EF4444',
  '#06B6D4',
  '#EC4899',
  '#84CC16',
];

export const emptyVisualSpec = (connectionId: number): BIVisualSpec => ({
  connection_id: connectionId,
  table: '',
  chart_type: 'bar',
  dimension: null,
  date_grain: null,
  measure: null,
  aggregation: 'count',
  filters: [],
  sort: 'value_desc',
  limit: 20,
  size: 'small',
});

export function describeVisualSpec(spec: BIVisualSpec): string {
  const aggregation = AGGREGATIONS.find((item) => item.id === spec.aggregation)?.label
    ?? spec.aggregation;
  const measure = spec.measure ? `${aggregation} de ${spec.measure}` : 'Cantidad de filas';
  return spec.dimension ? `${measure} por ${spec.dimension}` : measure;
}

// --- Exportación CSV del visual -------------------------------------------------
// Exporta EXACTAMENTE lo que ya está en pantalla (mismo endpoint auditado), y
// escapa los prefijos de fórmula igual que `sanitize_spreadsheet_value` del
// backend: un valor que empieza por "=", "+", "-" o "@" no puede convertirse
// en fórmula al abrir el archivo en Excel.

const FORMULA_PREFIXES = ['=', '+', '-', '@', '\t', '\r'];

export function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? '' : String(value);
  if (text.length > 0 && FORMULA_PREFIXES.includes(text[0])) {
    text = `'${text}`;
  }
  if (/[;"\n]/.test(text)) {
    text = `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export function visualToCsv(rows: BIRow[]): string {
  const hasLabel = rows.some((row) => 'label' in row);
  const header = hasLabel ? 'Etiqueta;Valor' : 'Valor';
  const lines = rows.map((row) =>
    hasLabel ? `${csvCell(row.label)};${csvCell(row.value)}` : csvCell(row.value),
  );
  return [header, ...lines].join('\r\n');
}

export function csvFilename(title: string): string {
  const clean = title
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80);
  return `${clean || 'visual'}.csv`;
}
