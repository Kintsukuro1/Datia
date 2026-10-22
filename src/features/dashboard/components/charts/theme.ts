import { QueryResult } from '../../../../types';

export type ChartType =
  | 'bar'
  | 'horizontal_bar'
  | 'line'
  | 'area'
  | 'donut'
  | 'pie'
  | 'radial'
  | 'scatter'
  | 'gauge'
  | 'treemap'
  | 'none';

export type ColorTheme = 'amber' | 'cyan' | 'emerald' | 'indigo' | 'rose';

/**
 * Pila tipografica de las graficas.
 *
 * `Inter` aparecia literal en ocho lugares (el `textStyle` de cada builder, el de
 * los ejes y el del tooltip) y NUNCA se aplico: `index.html` solo descarga
 * "Plus Jakarta Sans", asi que `Inter` cae de largo y el navegador usa
 * `system-ui`. Se replica aqui la pila REAL de `tailwind.config.js` para que las
 * graficas usen la misma fuente que el resto de la aplicacion.
 */
export const CHART_FONT_FAMILY = '"Plus Jakarta Sans", system-ui, sans-serif';

export const THEME_COLORS: Record<
  ColorTheme,
  {
    primary: string;
    secondary: string;
    glow: string;
    gradient: string[];
    name: string;
  }
> = {
  amber: {
    name: 'Ámbar Ejecutivo',
    primary: '#F59E0B',
    secondary: '#FBBF24',
    glow: 'rgba(245, 158, 11, 0.35)',
    gradient: ['#F59E0B', '#D97706', '#B45309', '#FBBF24', '#FDE68A', '#78350F'],
  },
  cyan: {
    name: 'Cian Tecnológico',
    primary: '#06B6D4',
    secondary: '#22D3EE',
    glow: 'rgba(6, 182, 212, 0.35)',
    gradient: ['#06B6D4', '#0891B2', '#0E7490', '#22D3EE', '#67E8F9', '#155E75'],
  },
  emerald: {
    name: 'Esmeralda Financiero',
    primary: '#10B981',
    secondary: '#34D399',
    glow: 'rgba(16, 185, 129, 0.35)',
    gradient: ['#10B981', '#059669', '#047857', '#34D399', '#6EE7B7', '#064E3B'],
  },
  indigo: {
    name: 'Púrpura Datia IA',
    primary: '#8B5CF6',
    secondary: '#A78BFA',
    glow: 'rgba(139, 92, 246, 0.35)',
    gradient: ['#8B5CF6', '#7C3AED', '#6D28D9', '#A78BFA', '#C4B5FD', '#4C1D95'],
  },
  rose: {
    name: 'Rosa / Carmesí Neón',
    primary: '#F43F5E',
    secondary: '#FB7185',
    glow: 'rgba(244, 63, 94, 0.35)',
    gradient: ['#F43F5E', '#E11D48', '#BE123C', '#FB7185', '#FDA4AF', '#881337'],
  },
};

/**
 * Base ECharts tooltip. Estaba declarado identico (byte a byte) en los cuatro
 * builders — bar, extra, line y pie — y cada copia podia desviarse sin que nada
 * lo notara. Vive aqui con el resto del tema.
 */
export const BASE_TOOLTIP = {
  backgroundColor: '#0F172A',
  borderColor: '#334155',
  borderWidth: 1,
  padding: [10, 14] as [number, number],
  textStyle: { color: '#F8FAFC', fontFamily: CHART_FONT_FAMILY },
  extraCssText:
    'box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.7), 0 8px 10px -6px rgba(0, 0, 0, 0.7); border-radius: 12px; backdrop-filter: blur(8px);',
};

/**
 * Cuerpo HTML del tooltip. Estaba copiado linea por linea en los cuatro builders
 * (bar, extra, line y pie) con quince valores de estilo Repetidos dentro, y cada
 * copia podia cambiar un color sin que las otras se enteraran.
 *
 * `BASE_TOOLTIP` de abajo es la cascara de ECharts; esto es lo que va DENTRO.
 */
const TOOLTIP_CSS = {
  // `Inter` estaba aqui desde antes, pero NUNCA se aplico: `index.html` solo
  // descarga "Plus Jakarta Sans" y `tailwind.config.js` deja `Inter` como
  // segundo de una pila donde nunca llega a usarse, asi que el navegador caia a
  // `system-ui`. Se replica aqui la pila REAL para que el tooltip coincida con
  // el resto de la aplicacion en vez de fingir una fuente que no existe.
  fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif',
  minWidth: '140px',
  maxWidth: '320px',
  titleSize: '11px',
  labelSize: '12px',
  valueSize: '13px',
  shareSize: '10px',
  titleColor: '#94A3B8',
  labelColor: '#E2E8F0',
  shareColor: '#64748B',
  dividerColor: '#1E293B',
  dotSize: '8px',
} as const;

function buildTooltipHtml(
  name: string,
  valueText: string,
  valueShareText: string,
  numColLabel: string,
  accentColor: string
): string {
  const c = TOOLTIP_CSS;
  return `
      <div style="font-family:${c.fontFamily}; min-width:${c.minWidth}; max-width:${c.maxWidth};">
        <div style="font-size:${c.titleSize}; font-weight:600; color:${c.titleColor}; margin-bottom:6px; word-wrap:break-word; line-height:1.3; border-bottom:1px solid ${c.dividerColor}; padding-bottom:4px;">
          ${name}
        </div>
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
          <div style="display:flex; align-items:center; gap:6px;">
            <span style="display:inline-block; width:${c.dotSize}; height:${c.dotSize}; border-radius:50%; background-color:${accentColor};"></span>
            <span style="font-size:${c.labelSize}; font-weight:500; color:${c.labelColor};">${numColLabel}:</span>
          </div>
          <span style="font-size:${c.valueSize}; font-weight:700; color:${accentColor}; font-family:monospace;">${valueText}</span>
        </div>
        ${
          valueShareText
            ? `<div style="font-size:${c.shareSize}; color:${c.shareColor}; margin-top:4px; text-align:right;">${valueShareText} del total</div>`
            : ''
        }
      </div>
    `;
}

/**
 * Formatter de tooltip para series de una medida. Estaba copiado palabra por
 * palabra en los cuatro builders (bar, extra, line y pie): la unica variacion
 * era el color y el nombre de la columna, que ahora van por parametro.
 */
export function makeTooltipFormatter(
  stats: { total: number },
  numCol: string | undefined,
  isCurrency: boolean,
  accentColor: string
): (name: string, val: number) => string {
  return (name: string, val: number) => {
    const pctStr = stats.total > 0 ? `${((val / stats.total) * 100).toFixed(1)}%` : '';
    return buildTooltipHtml(
      name,
      formatMetricNumber(val, isCurrency),
      pctStr,
      numCol?.replace(/_/g, ' ') ?? '',
      accentColor
    );
  };
}

/**
 * Scaffold comun de las opciones de grafica. `baseDark`, el slider de
 * `dataZoom` y la linea de promedio estaban copiados byte a byte en bar y
 * line (pie solo usa la base): un cambio de color o de margen en una copia
 * dejaba a la otra desincronizada sin que nada lo notara.
 */
export function buildBaseDark(showDataZoom = false) {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: '#94A3B8', fontFamily: CHART_FONT_FAMILY },
    grid: { left: '3%', right: '4%', bottom: showDataZoom ? '18%' : '10%', top: '12%', containLabel: true },
  };
}

export function buildDataZoomComponent(
  currentTheme: (typeof THEME_COLORS)['amber'],
  showDataZoom: boolean,
  rowsCount: number
) {
  return showDataZoom || rowsCount > 12
    ? [
        {
          type: 'slider',
          show: true,
          xAxisIndex: [0],
          bottom: '2%',
          height: 20,
          borderColor: '#334155',
          fillerColor: currentTheme.glow,
          textStyle: { color: '#94A3B8', fontSize: 10 },
          handleStyle: { color: currentTheme.primary, borderColor: '#fff' },
        },
      ]
    : undefined;
}

export function buildAverageMarkLine(
  showAverageLine: boolean,
  stats: { count: number },
  currentTheme: (typeof THEME_COLORS)['amber'],
  isCurrency: boolean
) {
  return showAverageLine && stats.count > 1
    ? {
        data: [
          {
            type: 'average',
            name: 'Promedio',
            lineStyle: { color: currentTheme.secondary, type: 'dashed', width: 2 },
            label: {
              show: true,
              formatter: (p: any) => `Prom: ${formatMetricNumber(p.value, isCurrency)}`,
              color: currentTheme.secondary,
              fontSize: 10,
              position: 'insideEndTop',
            },
          },
        ],
      }
    : undefined;
}

/**
 * Recorta una etiqueta de eje. `str.length > 20` con `substring(0, 18)` estaba
 * en bar, extra y line; pie usa otros numeros (`18`/`16`) y por eso su recorte
 * no coincidia con el de los otros tres.
 */
export function truncateAxisLabel(val: string, max = 20): string {
  // El guard de `!val` no es decorativo: ECharts llama al formatter con
  // `undefined` y con `''` en categorias sin nombre, y sin el los series de
  // texto se rotulan literalmente "undefined".
  if (!val) return '';
  const t = String(val).trim();
  return t.length > max ? `${t.substring(0, max - 2)}...` : t;
}

/**
 * Angulo de rotacion de las etiquetas del eje X, en grados.
 *
 * Reproduce el ternario duplicado en bar, extra y line:
 *   alguna etiqueta larga -> 25, muchas etiquetas -> 15, si no -> 0
 */
export function axisRotateAngle(labels: string[]): number {
  if (labels.some((l) => l.length > 14)) return 25;
  return labels.length > 6 ? 15 : 0;
}

/**
 * globals.css kills CSS animations for prefers-reduced-motion, but ECharts
 * animates on canvas from JS, so it was never covered: WCAG 2.3.3.
 * ponytail: read once per call. Cheap enough; a subscription is only worth it
 * if the app starts honouring a live OS setting change.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export interface ChartStats {
  total: number;
  avg: number;
  max: number;
  maxLabel: string;
  min: number;
  minLabel: string;
  count: number;
}

export function computeChartStats(
  rows: Record<string, any>[],
  catCol: string,
  numCol: string
): ChartStats {
  if (!rows.length || !numCol) {
    return { total: 0, avg: 0, max: 0, maxLabel: '-', min: 0, minLabel: '-', count: 0 };
  }

  let total = 0;
  let max = -Infinity;
  let maxLabel = '';
  let min = Infinity;
  let minLabel = '';

  for (const r of rows) {
    const val = Number(r[numCol]) || 0;
    const lbl = String(r[catCol] || '');
    total += val;
    if (val > max) {
      max = val;
      maxLabel = lbl;
    }
    if (val < min) {
      min = val;
      minLabel = lbl;
    }
  }

  const count = rows.length;
  const avg = count > 0 ? total / count : 0;

  return {
    total,
    avg,
    max: max === -Infinity ? 0 : max,
    maxLabel: maxLabel || '-',
    min: min === Infinity ? 0 : min,
    minLabel: minLabel || '-',
    count,
  };
}

export function deriveProcessedRows(
  result: QueryResult,
  sortOrder: 'default' | 'desc' | 'asc'
) {
  if (!result.data_rows || result.data_rows.length === 0) {
    return { catCol: 'categoría', numCol: 'valor', processedRows: [] };
  }

  const columns = result.data_columns || Object.keys(result.data_rows[0]);

  const isColNumeric = (col: string): boolean => {
    let numericCount = 0;
    for (const r of result.data_rows.slice(0, 10)) {
      const v = r[col];
      if (typeof v === 'number' && !isNaN(v)) numericCount++;
      else if (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v))) numericCount++;
    }
    return numericCount > 0;
  };

  let nCol = columns.find(
    (c) =>
      !c.toLowerCase().startsWith('id_') &&
      !c.toLowerCase().endsWith('_id') &&
      c.toLowerCase() !== 'id' &&
      isColNumeric(c)
  );

  let cCol = columns.find(
    (c) =>
      c !== nCol &&
      !c.toLowerCase().startsWith('id_') &&
      !c.toLowerCase().endsWith('_id') &&
      !c.toLowerCase().includes('token') &&
      !c.toLowerCase().includes('iban') &&
      !c.toLowerCase().includes('key')
  );

  if (!nCol) nCol = columns.find((c) => isColNumeric(c)) || columns[0];
  if (!cCol) cCol = columns.find((c) => c !== nCol) || columns[0];

  let sorted = [...result.data_rows];
  if (sortOrder === 'desc') {
    sorted.sort((a, b) => (Number(b[nCol!]) || 0) - (Number(a[nCol!]) || 0));
  } else if (sortOrder === 'asc') {
    sorted.sort((a, b) => (Number(a[nCol!]) || 0) - (Number(b[nCol!]) || 0));
  }

  return { catCol: cCol, numCol: nCol, processedRows: sorted };
}

export function formatMetricNumber(num: number, isCurrency: boolean): string {
  if (num === null || num === undefined || isNaN(num)) return '0';
  if (isCurrency) {
    if (Math.abs(num) >= 1_000_000) return `$${(num / 1_000_000).toFixed(2)}M`;
    if (Math.abs(num) >= 1_000) return `$${(num / 1_000).toFixed(1)}K`;
    return `$${num.toLocaleString('es-CL', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
  }
  if (Math.abs(num) >= 1_000_000) return `${(num / 1_000_000).toFixed(2)}M`;
  if (Math.abs(num) >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return Number.isInteger(num) ? num.toLocaleString('es-CL') : num.toFixed(2);
}
