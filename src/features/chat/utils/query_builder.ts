import type { BuilderColumn, BuilderMetric, BuilderTable } from '../services/query_service';

export type BuilderPeriod = 'year' | 'last90' | 'all';

export function stripSuggestionEmoji(suggestion: string): string {
  return suggestion.replace(/^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u, '').trim();
}

export function humanizeLabel(label: string): string {
  const trimmed = (label || '').trim();
  if (!trimmed) return '';
  const words = trimmed.includes('_') ? trimmed.replace(/_+/g, ' ').trim() : trimmed;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function buildBuilderPreview(params: {
  metric: BuilderMetric;
  dimension: BuilderColumn | null;
  period: BuilderPeriod;
}): string {
  const metricText = params.metric.aggregate === 'count'
    ? 'Cantidad de registros'
    : humanizeLabel(params.metric.label);
  const dimText = params.dimension ? ` por ${humanizeLabel(params.dimension.label)}` : '';
  const periodText = params.period === 'year'
    ? ` · Año ${new Date().getFullYear()}`
    : params.period === 'last90'
      ? ' · Últimos 90 días'
      : ' · Todo el histórico';
  return `${metricText}${dimText}${periodText}`;
}

/**
 * Arma una pregunta con los identificadores reales de tabla/columna, no solo con
 * etiquetas amigables. El LLM recibe el esquema completo, pero nombrar la columna
 * exacta evita que tenga que adivinar a qué campo corresponde "monto".
 */
export function buildBuilderPrompt(params: {
  table: BuilderTable;
  metric: BuilderMetric;
  dimension: BuilderColumn | null;
  dateColumn: BuilderColumn | null;
  period: BuilderPeriod;
}): string {
  const tableName = params.table.name;
  const action = params.metric.aggregate === 'count'
    ? 'cuenta los registros'
    : `calcula la suma de ${params.metric.column} (${humanizeLabel(params.metric.label)})`;

  const grouping = params.dimension
    ? ` agrupando por ${params.dimension.name} (${humanizeLabel(params.dimension.label)})`
    : '';

  let period = ', considerando todo el histórico';
  if (params.dateColumn) {
    if (params.period === 'year') {
      period = `, considerando solo las filas con ${params.dateColumn.name} en el año ${new Date().getFullYear()}`;
    } else if (params.period === 'last90') {
      period = `, considerando solo las filas con ${params.dateColumn.name} en los últimos 90 días`;
    }
  }

  return `En la tabla ${tableName}, ${action}${grouping}${period}. Presenta el resultado con un gráfico y un análisis breve.`;
}
