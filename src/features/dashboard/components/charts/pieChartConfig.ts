import { BASE_TOOLTIP, CHART_FONT_FAMILY, buildTooltipHtml, truncateAxisLabel, THEME_COLORS, computeChartStats, formatMetricNumber } from './theme';

export function buildPieChartOption(params: {
  processedRows: Record<string, any>[];
  catCol: string;
  numCol: string;
  activeChartType: 'pie' | 'donut';
  currentTheme: (typeof THEME_COLORS)['amber'];
  isCurrency: boolean;
  showDataLabels?: boolean;
}) {
  const {
    processedRows,
    catCol,
    numCol,
    activeChartType,
    currentTheme,
    isCurrency,
    showDataLabels = false,
  } = params;

  const stats = computeChartStats(processedRows, catCol, numCol);

  const formatTooltipHtml = (name: string, val: number) => {
    const pctStr = stats.total > 0 ? `${((val / stats.total) * 100).toFixed(1)}%` : '';
    return buildTooltipHtml(
      name,
      formatMetricNumber(val, isCurrency),
      pctStr,
      numCol?.replace(/_/g, ' ') ?? '',
      currentTheme.primary
    );
  };

  const baseDark = {
    backgroundColor: 'transparent',
    textStyle: { color: '#94A3B8', fontFamily: CHART_FONT_FAMILY },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
  };

  return {
    ...baseDark,
    tooltip: {
      ...BASE_TOOLTIP,
      trigger: 'item',
      formatter: (p: any) => formatTooltipHtml(p.name, p.value),
    },
    // `type: 'scroll'` es lo que evita el desborde, y no es cosmético.
    //
    // El default de ECharts es `type: 'plain'`: la leyenda envuelve en tantas
    // líneas como necesite y SIN límite de altura. Con las ~300 categorías que
    // devuelve una consulta agregada por cliente, la leyenda crecía hasta tapar
    // el canvas entero y la torta quedaba invisible detrás del texto.
    //
    // `scroll` la convierte en una única línea paginada con flechas, con ancho y
    // alto explícitos. Ninguna cantidad de categorías puede desbordarla.
    //
    // No se recortan los datos a un top-N: eso perdería categorías sin avisar.
    // Con scroll el tooltip sigue mostrando el detalle de cada porción.
    legend: {
      type: 'scroll',
      orient: 'horizontal',
      left: 0,
      // El espacio de la derecha es para las flechas de página; sin él la
      // última etiqueta queda debajo de la flecha siguiente.
      right: 18,
      bottom: 0,
      height: 22,
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 12,
      textStyle: { color: '#94A3B8', fontSize: 11 },
      pageIconColor: currentTheme.primary,
      pageIconInactiveColor: '#334155',
      pageIconSize: 10,
      pageTextStyle: { color: '#94A3B8', fontSize: 10 },
      // Usa el mismo recorte que bar/line/extra. Antes el pastel truncaba a 18/16 y
      // los otros a 20/18: la misma idea con dos numeros distintos, sin motivo.
      formatter: (name: string) => truncateAxisLabel(name),
    },
    series: [
      {
        name: numCol?.replace(/_/g, ' ').toUpperCase(),
        type: 'pie',
        radius: activeChartType === 'donut' ? ['45%', '72%'] : '68%',
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: '#0B0F19', borderWidth: 2 },
        label: {
          show: showDataLabels,
          formatter: '{b}: {d}%',
          color: '#E2E8F0',
          fontSize: 10,
        },
        emphasis: {
          label: { show: true, fontSize: 12, fontWeight: 'bold', color: '#FFF' },
          itemStyle: { shadowBlur: 15, shadowColor: 'rgba(0, 0, 0, 0.5)' },
        },
        data: processedRows.map((r, i) => ({
          name: String(r[catCol] || ''),
          value: Number(r[numCol]) || 0,
          itemStyle: { color: currentTheme.gradient[i % currentTheme.gradient.length] },
        })),
      },
    ],
  };
}
