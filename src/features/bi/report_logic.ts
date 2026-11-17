import { BIReportParams } from './services/bi_service';
import { BICross, BIMatrix, BIPeriod } from './types';

export const MONTHS = [
  'enero',
  'febrero',
  'marzo',
  'abril',
  'mayo',
  'junio',
  'julio',
  'agosto',
  'septiembre',
  'octubre',
  'noviembre',
  'diciembre',
];

export const DAYS = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];

export const REPORT_TABS = ['resumen', 'diario', 'distribucion', 'datos', 'guia'] as const;
export type ReportTab = (typeof REPORT_TABS)[number];

export const REPORT_TAB_LABELS: Record<ReportTab, string> = {
  resumen: 'Resumen general',
  diario: 'Detalle diario',
  distribucion: 'Distribución',
  datos: 'Datos',
  guia: 'Guía de lectura',
};

export interface ReportState {
  conn: string;
  table: string;
  date: string;
  group: string;
  cat: string;
  measure: string;
  year: string;
  month: string;
  gv: string;
  cv: string;
  tab: ReportTab;
}

export const EMPTY_REPORT_STATE: ReportState = {
  conn: '',
  table: '',
  date: '',
  group: '',
  cat: '',
  measure: 'count',
  year: '',
  month: '',
  gv: '',
  cv: '',
  tab: 'resumen',
};

export const isReportTab = (value: string | null): value is ReportTab =>
  Boolean(value && (REPORT_TABS as readonly string[]).includes(value));

export function readReportState(search: string): ReportState {
  const params = new URLSearchParams(search);
  const tab = params.get('tab');
  return {
    conn: params.get('conn') || '',
    table: params.get('table') || '',
    date: params.get('date') || '',
    group: params.get('group') || '',
    cat: params.get('cat') || '',
    measure: params.get('measure') || 'count',
    year: params.get('year') || '',
    month: params.get('month') || '',
    gv: params.get('gv') || '',
    cv: params.get('cv') || '',
    tab: isReportTab(tab) ? tab : 'resumen',
  };
}

export function buildReportParams(state: ReportState): BIReportParams {
  const params: BIReportParams = {
    connection_id: Number(state.conn),
    table: state.table,
  };
  if (state.date) params.date_column = state.date;
  if (state.group) params.group_column = state.group;
  if (state.cat) params.category_column = state.cat;
  if (state.measure) params.measure = state.measure;
  if (state.year) params.year = Number(state.year);
  if (state.month) params.month = state.month;
  if (state.gv) params.group_value = state.gv;
  if (state.cv) params.category_value = state.cv;
  return params;
}

export function buildReportSearch(state: ReportState): string {
  const params = new URLSearchParams();
  const values: Array<[string, string]> = [
    ['conn', state.conn],
    ['table', state.table],
    ['date', state.date],
    ['group', state.group],
    ['cat', state.cat],
    ['measure', state.measure === 'count' ? '' : state.measure],
    ['year', state.year],
    ['month', state.month],
    ['gv', state.gv],
    ['cv', state.cv],
    ['tab', state.tab === 'resumen' ? '' : state.tab],
  ];
  values.forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return query ? `?${query}` : '';
}

export function buildPowerBiUrl(connectionId?: number | null, table?: string | null): string {
  const params = new URLSearchParams();
  if (connectionId) params.set('conn', String(connectionId));
  if (table) params.set('table', table);
  const query = params.toString();
  return query ? `/powerbi?${query}` : '/powerbi';
}

export function formatNumber(value: unknown, decimals = 0): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString('es-CL', { maximumFractionDigits: decimals });
  }
  return String(value);
}

/**
 * Formato compacto para ejes de gráficos: legible en poco espacio.
 * El tooltip sigue mostrando `formatNumber` completo.
 */
export function formatCompact(value: unknown): string {
  const number = typeof value === 'number' && Number.isFinite(value) ? value : null;
  if (number === null) return '—';
  const abs = Math.abs(number);
  if (abs >= 1_000_000) {
    return `${(number / 1_000_000).toLocaleString('es-CL', { maximumFractionDigits: 1 })} M`;
  }
  if (abs >= 1_000) {
    return `${(number / 1_000).toLocaleString('es-CL', { maximumFractionDigits: 1 })} mil`;
  }
  return number.toLocaleString('es-CL', { maximumFractionDigits: 0 });
}

/** Convierte una etiqueta de mes del gráfico en su número (para el drill). */
export function monthFromLabel(label: string): number | null {
  const byName = MONTHS.findIndex((month) => month === label.toLowerCase());
  if (byName >= 0) return byName + 1;
  const isoMonth = /^(\d{4})-(\d{2})$/.exec(label);
  if (isoMonth) {
    const month = Number(isoMonth[2]);
    return month >= 1 && month <= 12 ? month : null;
  }
  return null;
}

/**
 * Toggle de cross-filter: clic en el valor activo lo quita; clic en otro lo
 * reemplaza. Mismo comportamiento que un slicer de Power BI.
 */
export function toggleFilterValue(current: string, clicked: string): string {
  return current === clicked ? '' : clicked;
}

export function formatPeriod(period: BIPeriod): string {
  if (!period.year) return 'Todo el período';
  if (period.month) return `${MONTHS[period.month - 1]} ${period.year}`;
  return `Año ${period.year}`;
}

export function dayLabel(iso: string): string {
  const date = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return `${String(date.getDate()).padStart(2, '0')} ${DAYS[date.getDay()]}`;
}

export function flowLabel(label: string, monthSelected: boolean): string {
  if (monthSelected) return dayLabel(label);
  const match = /^(\d{4})-(\d{2})$/.exec(label);
  if (match) return MONTHS[Number(match[2]) - 1] || label;
  return label;
}

export const describeMeasure = (measure: string): string =>
  !measure || measure === 'count' ? 'Registros' : `Suma de ${measure}`;

export const cap = (value: string): string =>
  value ? value.charAt(0).toUpperCase() + value.slice(1) : value;

export function participationPct(value: number, total: number): number {
  if (!total) return 0;
  return (value / total) * 100;
}

export function arrowFor(participation: number, expected: number): '↑' | '→' | '↓' {
  if (participation >= expected * 1.2) return '↑';
  if (participation >= expected * 0.8) return '→';
  return '↓';
}

export function matrixDayTotals(matrix: BIMatrix): Record<string, number> {
  return Object.fromEntries(
    matrix.days.map((day) => [
      day,
      matrix.rows.reduce((total, row) => total + (row.cells[day] || 0), 0),
    ]),
  );
}

export function crossCategoryTotals(cross: BICross): Record<string, number> {
  return Object.fromEntries(
    cross.categories.map((category) => [
      category,
      cross.rows.reduce((total, row) => total + (row.cells[category] || 0), 0),
    ]),
  );
}
