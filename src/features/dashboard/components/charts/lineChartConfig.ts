import { BASE_TOOLTIP, axisRotateAngle, makeTooltipFormatter, truncateAxisLabel, THEME_COLORS, computeChartStats, formatMetricNumber, buildBaseDark, buildDataZoomComponent, buildAverageMarkLine } from './theme';

export function buildLineChartOption(params: {
  processedRows: Record<string, any>[];
  catCol: string;
  numCol: string;
  activeChartType: 'line' | 'area';
  currentTheme: (typeof THEME_COLORS)['amber'];
  isCurrency: boolean;
  showDataLabels?: boolean;
  showAverageLine?: boolean;
  showDataZoom?: boolean;
}) {
  const {
    processedRows,
    catCol,
    numCol,
    activeChartType,
    currentTheme,
    isCurrency,
    showDataLabels = false,
    showAverageLine = true,
    showDataZoom = false,
  } = params;

  const xLabels = processedRows.map((r) => String(r[catCol] !== null && r[catCol] !== undefined ? r[catCol] : ''));
  const yValues = processedRows.map((r) => Number(r[numCol]) || 0);
  const stats = computeChartStats(processedRows, catCol, numCol);

  const formatAxisLabel = (val: string) => truncateAxisLabel(val);

  const formatTooltipHtml = makeTooltipFormatter(stats, numCol, isCurrency, currentTheme.primary);

  const baseDark = buildBaseDark(showDataZoom);

  const dataZoomComponent = buildDataZoomComponent(currentTheme, showDataZoom, processedRows.length);

  const markLineConfig = buildAverageMarkLine(showAverageLine, stats, currentTheme, isCurrency);

  const rotateAngle = axisRotateAngle(xLabels);

  return {
    ...baseDark,
    tooltip: {
      ...BASE_TOOLTIP,
      trigger: 'axis',
      formatter: (p: any) => {
        const it = Array.isArray(p) ? p[0] : p;
        return formatTooltipHtml(it.name, it.value);
      },
    },
    xAxis: {
      type: 'category',
      data: xLabels,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: {
        color: '#94A3B8',
        fontSize: 10,
        rotate: rotateAngle,
        formatter: formatAxisLabel,
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.05)' } },
      axisLabel: {
        color: '#94A3B8',
        fontSize: 10,
        formatter: (v: number) => formatMetricNumber(v, isCurrency),
      },
    },
    dataZoom: dataZoomComponent,
    series: [
      {
        name: numCol?.replace(/_/g, ' '),
        type: 'line',
        smooth: true,
        showSymbol: true,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { width: 3.5, color: currentTheme.primary, shadowColor: currentTheme.glow, shadowBlur: 8 },
        itemStyle: { color: currentTheme.primary, borderColor: '#FFF', borderWidth: 1.5 },
        label: {
          show: showDataLabels,
          position: 'top',
          color: '#F8FAFC',
          fontSize: 10,
          formatter: (p: any) => formatMetricNumber(p.value, isCurrency),
        },
        areaStyle:
          activeChartType === 'area'
            ? {
                color: {
                  type: 'linear',
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops: [
                    { offset: 0, color: currentTheme.glow },
                    { offset: 1, color: 'rgba(0, 0, 0, 0)' },
                  ],
                },
              }
            : undefined,
        data: yValues,
        markPoint: {
          data: [
            { type: 'max', name: 'Máximo', itemStyle: { color: '#10B981' } },
            { type: 'min', name: 'Mínimo', itemStyle: { color: '#EF4444' } },
          ],
        },
        markLine: markLineConfig,
      },
    ],
  };
}
