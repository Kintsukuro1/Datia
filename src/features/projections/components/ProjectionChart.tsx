import React, { useMemo } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import echarts from '../../../shared/echarts';
import {
  BASE_TOOLTIP,
  CHART_FONT_FAMILY,
  axisRotateAngle,
  formatMetricNumber,
  prefersReducedMotion,
  truncateAxisLabel,
} from '../../dashboard/components/charts/theme';
import type { ProjectionResult } from '../services/projection_service';

/**
 * Historica + proyectada + banda por paso.
 *
 * La banda no tiene precedente en el repo (el forecast retirado del chat usaba
 * una barra CSS). Tecnica elegida en ECharts: dos series de linea
 * apiladas (`stack`) — la de abajo con la banda inferior invisible y la de
 * arriba con `upper - lower` y el relleno violeta. Es la forma estandar de
 * pintar una banda sin `markArea` (que necesitaria una entrada por paso y
 * dejaria huecos entre categorias).
 *
 * La proyeccion va en linea discontinua a proposito: un tramo continuo se lee
 * como dato medido, y estos pasos son calculo con error publicado en la tabla.
 */

const PROJECTED_COLOR = '#7C3AED'; // violeta IA, el mismo del port de Proyecciones
const HISTORY_COLOR = '#94A3B8';
const BAND_FILL = 'rgba(124, 58, 237, 0.16)';
const BAND_NAME = 'Banda de error';

const moneyOrDash = (n: number | null): string => (n === null ? '—' : formatMetricNumber(n, true));

const fmtPct = (n: number): string =>
  `${Number(n).toLocaleString('es-CL', { maximumFractionDigits: 1 })}%`;

const tooltipRow = (label: string, value: string, color: string): string =>
  `<div style="display:flex;justify-content:space-between;gap:14px;margin-top:4px;">` +
  `<span style="display:flex;align-items:center;gap:6px;font-size:12px;color:#E2E8F0;">` +
  `<span style="width:8px;height:8px;border-radius:50%;background:${color};display:inline-block;"></span>${label}</span>` +
  `<span style="font-size:13px;font-weight:700;color:#F8FAFC;font-family:monospace;">${value}</span></div>`;

function buildOption(result: ProjectionResult) {
  // `periodo` puede venir null en la serie real (grupo de fecha NULL en la
  // tabla): se etiqueta como tal en vez de dejar una categoria vacia.
  const historyLabels = result.series.map((r) =>
    r.periodo === null || r.periodo === undefined ? 'sin periodo' : String(r.periodo)
  );
  const historyValues = result.series.map((r) =>
    r.valor === null || r.valor === undefined ? null : Number(r.valor)
  );
  const steps = result.steps;
  // `period` puede venir null (periodos no normalizables): el paso igual se
  // muestra, con su numero, en vez de desaparecer del grafico.
  const futureLabels = steps.map((s) => s.period ?? `Paso ${s.step}`);
  const labels = [...historyLabels, ...futureLabels];

  const historyData: (number | null)[] = [...historyValues, ...steps.map(() => null)];

  // La proyectada arranca anclada en el ultimo valor real para que la linea
  // conecte; el resto de los huecos historicos queda en null (no en 0).
  const projectedData: (number | null)[] = labels.map(() => null);
  if (historyValues.length > 0) {
    projectedData[historyValues.length - 1] = historyValues[historyValues.length - 1];
  }
  steps.forEach((s, i) => {
    projectedData[historyValues.length + i] = s.point;
  });

  const lowerData: (number | null)[] = [
    ...historyLabels.map(() => null),
    ...steps.map((s) => s.lower),
  ];
  // Espesor de la banda por paso. Con `lower` null el tramo queda con hueco:
  // se dibuja lo que hay medido, no un relleno inventado.
  const bandData: (number | null)[] = [
    ...historyLabels.map(() => null),
    ...steps.map((s) => (s.lower !== null && s.upper !== null ? Math.max(0, s.upper - s.lower) : null)),
  ];

  const stepByPeriod = new Map(
    steps.filter((s) => s.period).map((s) => [String(s.period), s])
  );

  return {
    backgroundColor: 'transparent',
    textStyle: { color: '#94A3B8', fontFamily: CHART_FONT_FAMILY },
    animation: !prefersReducedMotion(),
    legend: {
      top: 0,
      left: 0,
      // Solo lectura: apagar la banda desde la leyenda romperia el apilado y
      // dejaria el relleno colgado desde cero.
      selectedMode: false,
      itemWidth: 14,
      itemHeight: 8,
      textStyle: { color: '#94A3B8', fontSize: 10 },
      data: ['Histórica', 'Proyección', BAND_NAME],
    },
    grid: { left: '3%', right: '4%', bottom: '10%', top: 30, containLabel: true },
    tooltip: {
      ...BASE_TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#334155' } },
      formatter: (params: any) => {
        const items = Array.isArray(params) ? params : [params];
        const name = String(items[0]?.axisValue ?? '');
        const histIdx = historyLabels.indexOf(name);
        const step = stepByPeriod.get(name);
        let html =
          `<div style="font-family:${CHART_FONT_FAMILY};min-width:180px;">` +
          `<div style="font-size:11px;font-weight:600;color:#94A3B8;border-bottom:1px solid #1E293B;padding-bottom:4px;">${name}</div>`;
        if (histIdx >= 0 && historyValues[histIdx] !== null) {
          html += tooltipRow('Histórico', formatMetricNumber(historyValues[histIdx] as number, true), HISTORY_COLOR);
        }
        if (step) {
          html += tooltipRow('Proyección', moneyOrDash(step.point), PROJECTED_COLOR);
          if (step.lower !== null && step.upper !== null) {
            html += tooltipRow(
              step.band_pct === null ? 'Banda' : `Banda ±${fmtPct(step.band_pct)}`,
              `${formatMetricNumber(step.lower, true)} – ${formatMetricNumber(step.upper, true)}`,
              PROJECTED_COLOR
            );
          }
          if (step.mape !== null) {
            html += tooltipRow('MAPE del paso', fmtPct(step.mape), '#F59E0B');
          }
          html += tooltipRow('Backtests', String(step.n_backtests), '#64748B');
          if (step.extrapolated) {
            html += `<div style="margin-top:5px;font-size:10px;color:#FBBF24;">Sin backtest propio: hereda la banda del último paso medido.</div>`;
          } else if (!step.reliable) {
            html += `<div style="margin-top:5px;font-size:10px;color:#FBBF24;">Poca evidencia: menos de 4 backtests en este paso.</div>`;
          }
        }
        return html + '</div>';
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: {
        color: '#94A3B8',
        fontSize: 10,
        rotate: axisRotateAngle(labels),
        formatter: (val: string) => truncateAxisLabel(val, 12),
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.05)' } },
      axisLabel: {
        color: '#94A3B8',
        fontSize: 10,
        formatter: (v: number) => formatMetricNumber(v, true),
      },
    },
    series: [
      {
        name: BAND_NAME,
        type: 'line',
        stack: 'band',
        data: lowerData,
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 },
        silent: true,
        tooltip: { show: false },
        z: 1,
      },
      {
        name: BAND_NAME,
        type: 'line',
        stack: 'band',
        data: bandData,
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { color: BAND_FILL },
        silent: true,
        tooltip: { show: false },
        z: 1,
      },
      {
        name: 'Histórica',
        type: 'line',
        data: historyData,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 2.5, color: HISTORY_COLOR },
        itemStyle: { color: HISTORY_COLOR },
        z: 3,
      },
      {
        name: 'Proyección',
        type: 'line',
        data: projectedData,
        smooth: false,
        symbol: 'circle',
        symbolSize: 8,
        lineStyle: { width: 3, color: PROJECTED_COLOR, type: 'dashed' },
        itemStyle: { color: PROJECTED_COLOR, borderColor: '#FFF', borderWidth: 1.5 },
        z: 4,
      },
    ],
  };
}

export const ProjectionChart: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const option = useMemo(() => buildOption(result), [result]);
  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      style={{ height: 320, width: '100%' }}
      opts={{ renderer: 'canvas' }}
    />
  );
};
