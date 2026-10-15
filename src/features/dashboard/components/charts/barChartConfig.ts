import { BASE_TOOLTIP, CHART_FONT_FAMILY, axisRotateAngle, buildTooltipHtml, truncateAxisLabel, THEME_COLORS, computeChartStats, formatMetricNumber } from './theme';

export function buildBarChartOption(params: {
  processedRows: Record<string, any>[];
  catCol: string;
  numCol: string;
  activeChartType: 'bar' | 'horizontal_bar';
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
    grid: { left: '3%', right: '4%', bottom: showDataZoom ? '18%' : '10%', top: '12%', containLabel: true },
  };

  const dataZoomComponent = showDataZoom || processedRows.length > 12
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

  const markLineConfig = showAverageLine && stats.count > 1
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

  if (activeChartType === 'horizontal_bar') {
    return {
      ...baseDark,
      tooltip: {
        ...BASE_TOOLTIP,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (p: any) => {
          const it = Array.isArray(p) ? p[0] : p;
          return formatTooltipHtml(it.name, it.value);
        },
      },
      xAxis: {
        type: 'value',
        axisLine: { lineStyle: { color: '#334155' } },
        splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.05)' } },
        axisLabel: {
          color: '#94A3B8',
          fontSize: 10,
          formatter: (v: number) => formatMetricNumber(v, isCurrency),
        },
      },
      yAxis: {
        type: 'category',
        data: xLabels,
        inverse: true,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: {
          color: '#CBD5E1',
          fontSize: 11,
          formatter: formatAxisLabel,
        },
      },
      dataZoom: dataZoomComponent,
      series: [
        {
          name: numCol?.replace(/_/g, ' '),
          type: 'bar',
          data: yValues.map((val, idx) => ({
            value: val,
            itemStyle: {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 1,
                y2: 0,
                colorStops: [
                  { offset: 0, color: currentTheme.gradient[idx % currentTheme.gradient.length] },
                  { offset: 1, color: currentTheme.primary },
                ],
              },
              borderRadius: [0, 6, 6, 0],
            },
          })),
          label: {
            show: showDataLabels,
            position: 'right',
            color: '#F8FAFC',
            fontSize: 10,
            formatter: (p: any) => formatMetricNumber(p.value, isCurrency),
          },
          markLine: markLineConfig,
        },
      ],
    };
  }

  const rotateAngle = axisRotateAngle(xLabels);

  return {
    ...baseDark,
    tooltip: {
      ...BASE_TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
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
        type: 'bar',
        data: yValues.map((val, idx) => ({
          value: val,
          itemStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: currentTheme.primary },
                { offset: 1, color: currentTheme.gradient[idx % currentTheme.gradient.length] },
              ],
            },
            borderRadius: [6, 6, 0, 0],
          },
        })),
        label: {
          show: showDataLabels,
          position: 'top',
          color: '#F8FAFC',
          fontSize: 10,
          formatter: (p: any) => formatMetricNumber(p.value, isCurrency),
        },
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
