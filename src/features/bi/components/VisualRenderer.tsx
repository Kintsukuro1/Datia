import React from 'react';
import { BIChart } from './BIChart';
import { BIQueryResult, BIRow, BIVisualSpec } from '../types';
import { formatNumber } from '../report_logic';
import { PALETTE, describeVisualSpec } from '../panels_logic';

const isDark = (): boolean =>
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark');

const theme = () => ({
  text: isDark() ? '#cbd5e1' : '#475569',
  grid: isDark() ? 'rgba(148,163,184,0.15)' : 'rgba(100,116,139,0.15)',
  card: isDark() ? '#111827' : '#ffffff',
});

const labelsAndValues = (rows: BIRow[]) => ({
  labels: rows.map((row) => String(row.label ?? '')),
  values: rows.map((row) => (typeof row.value === 'number' ? row.value : Number(row.value) || 0)),
});

const barOption = (rows: BIRow[], horizontal: boolean): Record<string, unknown> => {
  const colors = theme();
  const { labels, values } = labelsAndValues(rows);
  const category = {
    type: 'category',
    data: labels,
    inverse: horizontal,
    axisLabel: { color: colors.text, fontSize: 10, hideOverlap: true },
    axisLine: { lineStyle: { color: colors.grid } },
  };
  const valueAxis = {
    type: 'value',
    axisLabel: {
      color: colors.text,
      fontSize: 10,
      formatter: (value: number) => formatNumber(value, 2),
    },
    splitLine: { lineStyle: { color: colors.grid } },
  };
  return {
    color: [PALETTE[0]],
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: colors.text },
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => formatNumber(value, 2) },
    grid: { left: 8, right: horizontal ? 42 : 12, top: 12, bottom: 8, containLabel: true },
    xAxis: horizontal ? valueAxis : category,
    yAxis: horizontal ? category : valueAxis,
    series: [
      {
        type: 'bar',
        data: values,
        barMaxWidth: 28,
        itemStyle: { borderRadius: horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0] },
        label: {
          show: true,
          position: horizontal ? 'right' : 'top',
          color: colors.text,
          fontSize: 10,
          formatter: (params: { value: number }) => formatNumber(params.value, 2),
        },
      },
    ],
  };
};

const lineOption = (rows: BIRow[], area: boolean): Record<string, unknown> => {
  const colors = theme();
  const { labels, values } = labelsAndValues(rows);
  return {
    color: [PALETTE[0]],
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: colors.text },
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => formatNumber(value, 2) },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: colors.text, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: colors.grid } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: colors.text,
        fontSize: 10,
        formatter: (value: number) => formatNumber(value, 2),
      },
      splitLine: { lineStyle: { color: colors.grid } },
    },
    series: [
      {
        type: 'line',
        data: values,
        smooth: true,
        symbolSize: 6,
        areaStyle: area ? { opacity: 0.18 } : undefined,
      },
    ],
  };
};

const pieOption = (rows: BIRow[]): Record<string, unknown> => {
  const colors = theme();
  return {
    color: PALETTE,
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: colors.text },
    tooltip: { trigger: 'item', valueFormatter: (value: number) => formatNumber(value, 2) },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: colors.text, fontSize: 10 } },
    series: [
      {
        type: 'pie',
        radius: ['40%', '68%'],
        center: ['50%', '44%'],
        itemStyle: { borderRadius: 6, borderColor: colors.card, borderWidth: 2 },
        label: { show: false },
        data: rows.map((row) => ({
          name: String(row.label ?? ''),
          value: typeof row.value === 'number' ? row.value : Number(row.value) || 0,
        })),
      },
    ],
  };
};

export const VisualRenderer: React.FC<{
  spec: BIVisualSpec;
  result: BIQueryResult;
  height?: number;
}> = ({ spec, result, height = 260 }) => {
  const rows = result.rows;

  if (!rows.length) {
    return (
      <div
        className="flex items-center justify-center text-xs text-app-muted"
        style={{ height }}
      >
        Sin datos para estos filtros.
      </div>
    );
  }

  if (spec.chart_type === 'kpi' || !spec.dimension) {
    const total = spec.dimension
      ? rows.reduce((sum, row) => sum + (typeof row.value === 'number' ? row.value : Number(row.value) || 0), 0)
      : rows[0]?.value;
    return (
      <div className="flex flex-col items-center justify-center text-center" style={{ height }}>
        <div className="text-4xl font-extrabold text-brand-600 dark:text-brand-300 tabular-nums">
          {formatNumber(total, 2)}
        </div>
        <div className="mt-2 text-xs text-app-muted">{describeVisualSpec(spec)}</div>
        {result.masked_label && (
          <div className="mt-1 text-[10px] text-amber-600 dark:text-amber-300">
            Etiqueta enmascarada por tu perfil.
          </div>
        )}
      </div>
    );
  }

  if (spec.chart_type === 'table') {
    return (
      <div className="overflow-auto rounded-xl border border-app-border" style={{ maxHeight: height }}>
        <table className="w-full text-xs">
          <caption className="sr-only">{describeVisualSpec(spec)}</caption>
          <thead className="sticky top-0 bg-app-surface">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-semibold text-app-muted">
                {spec.dimension}
              </th>
              <th scope="col" className="px-3 py-2 text-right font-semibold text-app-muted">
                {describeVisualSpec(spec).split(' por ')[0]}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-t border-app-border">
                <td className="px-3 py-1.5 text-app-text">{String(row.label ?? '')}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-app-text">
                  {formatNumber(row.value, 2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const option =
    spec.chart_type === 'pie'
      ? pieOption(rows)
      : spec.chart_type === 'line' || spec.chart_type === 'area'
        ? lineOption(rows, spec.chart_type === 'area')
        : barOption(rows, spec.chart_type === 'hbar');

  return (
    <div className="space-y-1">
      <BIChart option={option} height={height} ariaLabel={describeVisualSpec(spec)} />
      {result.masked_label && (
        <div className="text-[10px] text-amber-600 dark:text-amber-300">
          Etiqueta enmascarada por tu perfil.
        </div>
      )}
    </div>
  );
};
