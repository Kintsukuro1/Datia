import React from 'react';
import { BIChart } from './BIChart';
import {
  BIReport,
  BIPoint,
  BICross,
  BIMatrix,
  BIDetail,
} from '../types';
import {
  ReportTab,
  arrowFor,
  cap,
  crossCategoryTotals,
  dayLabel,
  describeMeasure,
  flowLabel,
  formatCompact,
  formatNumber,
  formatPeriod,
  matrixDayTotals,
  monthFromLabel,
  participationPct,
  toggleFilterValue,
} from '../report_logic';

const COLORS = [
  '#006ee6',
  '#10B981',
  '#F59E0B',
  '#8B5CF6',
  '#EF4444',
  '#06B6D4',
  '#EC4899',
  '#84CC16',
  '#0c8aff',
  '#14B8A6',
  '#F97316',
  '#6366F1',
];

const isDark = (): boolean =>
  typeof document !== 'undefined' && document.documentElement.classList.contains('dark');

const chartTheme = () => ({
  text: isDark() ? '#cbd5e1' : '#475569',
  grid: isDark() ? 'rgba(148,163,184,0.15)' : 'rgba(100,116,139,0.15)',
  card: isDark() ? '#111827' : '#ffffff',
});

const Card: React.FC<{ title: string; hint?: string; children: React.ReactNode; className?: string }> = ({
  title,
  hint,
  children,
  className = '',
}) => (
  <section className={`rounded-2xl border border-app-border bg-app-card p-4 shadow-sm min-w-0 ${className}`}>
    <h3 className="text-sm font-bold text-app-text">{title}</h3>
    {hint && <p className="text-[11px] text-app-muted mb-3">{hint}</p>}
    {children}
  </section>
);

const Empty: React.FC<{ text: string; height?: number }> = ({ text, height = 220 }) => (
  <div className="flex items-center justify-center px-4 text-center text-xs text-app-muted" style={{ height }}>
    {text}
  </div>
);

const barOption = (rows: BIPoint[], color: string, horizontal = false): Record<string, unknown> => {
  const theme = chartTheme();
  const labels = rows.map((row) => row.label);
  const values = rows.map((row) => (typeof row.value === 'number' ? row.value : 0));
  const category = {
    type: 'category',
    data: labels,
    inverse: horizontal,
    axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true },
    axisLine: { lineStyle: { color: theme.grid } },
  };
  const value = {
    type: 'value',
    axisLabel: {
      color: theme.text,
      fontSize: 10,
      formatter: (value: number) => formatCompact(value),
    },
    splitLine: { lineStyle: { color: theme.grid } },
  };
  return {
    color: [color],
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: theme.text },
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => formatNumber(value, 2) },
    grid: { left: 8, right: horizontal ? 42 : 12, top: 12, bottom: 8, containLabel: true },
    xAxis: horizontal ? value : category,
    yAxis: horizontal ? category : value,
    series: [
      {
        type: 'bar',
        data: values,
        barMaxWidth: 26,
        itemStyle: { borderRadius: horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0] },
        label: {
          show: true,
          position: horizontal ? 'right' : 'top',
          color: theme.text,
          fontSize: 10,
          formatter: (params: { value: number }) => formatCompact(params.value),
        },
      },
    ],
  };
};

const lineOption = (rows: BIPoint[], monthSelected: boolean, area = false): Record<string, unknown> => {
  const theme = chartTheme();
  return {
    color: [COLORS[0]],
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: theme.text },
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => formatNumber(value, 2) },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((row) => flowLabel(row.label, monthSelected)),
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: theme.grid } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: theme.text,
        fontSize: 10,
        formatter: (value: number) => formatCompact(value),
      },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        type: 'line',
        data: rows.map((row) => (typeof row.value === 'number' ? row.value : 0)),
        smooth: true,
        symbolSize: 6,
        areaStyle: area ? { opacity: 0.18 } : undefined,
      },
    ],
  };
};

const pieOption = (rows: BIPoint[]): Record<string, unknown> => {
  const theme = chartTheme();
  return {
    color: COLORS,
    textStyle: { fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif', color: theme.text },
    tooltip: { trigger: 'item', valueFormatter: (value: number) => formatNumber(value, 2) },
    legend: {
      type: 'scroll',
      bottom: 0,
      textStyle: { color: theme.text, fontSize: 10 },
    },
    series: [
      {
        type: 'pie',
        radius: ['40%', '68%'],
        center: ['50%', '44%'],
        itemStyle: { borderRadius: 6, borderColor: theme.card, borderWidth: 2 },
        label: { show: false },
        data: rows.map((row) => ({
          name: row.label,
          value: typeof row.value === 'number' ? row.value : 0,
        })),
      },
    ],
  };
};

const KpiGrid: React.FC<{
  items: Array<{
    label: string;
    value: number | null | undefined;
    hint?: string;
    decimals?: number;
    /** Variación contra el período anterior, en %. `null` = sin historial. */
    delta?: number | null;
    /** Valor del período anterior, para mostrar la comparación. */
    previous?: number | null;
  }>;
}> = ({ items }) => {
  const visible = items.filter((item) => item.value !== null && item.value !== undefined);
  if (!visible.length) return null;
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      {visible.map((item) => (
        <div
          key={item.label}
          className="rounded-2xl border border-app-border bg-app-card overflow-hidden shadow-sm"
        >
          <div className="bg-brand-500/10 text-brand-700 dark:text-brand-300 text-[11px] font-bold text-center px-2 py-2 min-h-[38px] flex items-center justify-center">
            {item.label}
          </div>
          <div className="text-center text-2xl font-extrabold text-app-text tabular-nums px-2 py-3">
            {formatNumber(item.value, item.decimals ?? 0)}
            {item.delta !== null && item.delta !== undefined && (
              <div
                className={`text-[11px] font-bold mt-0.5 ${
                  item.delta > 0
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : item.delta < 0
                      ? 'text-rose-600 dark:text-rose-400'
                      : 'text-app-muted'
                }`}
              >
                {item.delta > 0 ? '▲' : item.delta < 0 ? '▼' : '■'}{' '}
                {item.delta > 0 ? '+' : ''}
                {formatNumber(item.delta, 1)} %
              </div>
            )}
            {item.previous !== null && item.previous !== undefined && (
              <div className="text-[10px] font-medium text-app-muted mt-0.5">
                anterior: {formatNumber(item.previous, 2)}
              </div>
            )}
          </div>
          {item.hint && (
            <div className="text-center text-[10px] text-app-muted px-2 pb-2">{item.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
};

const FilterLabelButton: React.FC<{
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ label, active, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    title={active ? `Quitar el filtro "${label}"` : `Filtrar el informe por "${label}"`}
    className={`text-left font-semibold whitespace-nowrap rounded px-1 -mx-1 hover:text-brand-600 dark:hover:text-brand-300 hover:underline underline-offset-2 ${
      active ? 'text-brand-700 dark:text-brand-300' : 'text-app-text'
    }`}
  >
    {label}
    {active && <span aria-hidden> ✕</span>}
    <span className="sr-only">{active ? '(filtro activo; clic para quitar)' : '(clic para filtrar)'}</span>
  </button>
);

const CrossTable: React.FC<{
  cross: BICross;
  groupLabel: string;
  activeValue: string;
  onFilter: (label: string) => void;
}> = ({ cross, groupLabel, activeValue, onFilter }) => {
  if (!cross.categories.length || !cross.rows.length) {
    return <Empty text="No hay datos para el cruce con estos filtros." height={160} />;
  }
  const totals = crossCategoryTotals(cross);
  const grand = cross.rows.reduce((total, row) => total + row.total, 0);
  return (
    <div className="overflow-auto max-h-[420px] rounded-xl border border-app-border">
      <table className="min-w-full text-[11px] border-collapse">
        <caption className="sr-only">
          Resumen por {groupLabel} y categoría del período seleccionado.
        </caption>
        <thead className="sticky top-0 bg-app-surface z-10">
          <tr>
            <th scope="col" className="px-3 py-2 text-left font-bold text-app-muted border-b border-app-border">
              {groupLabel}
            </th>
            {cross.categories.map((category) => (
              <th
                key={category}
                scope="col"
                className="px-3 py-2 text-right font-bold text-app-muted border-b border-app-border whitespace-nowrap"
              >
                {category}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-bold text-app-muted border-b border-app-border">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {cross.rows.map((row) => (
            <tr key={row.label} className="border-b border-app-border/70 last:border-0">
              <th scope="row" className="px-3 py-1.5 text-left whitespace-nowrap">
                <FilterLabelButton
                  label={row.label}
                  active={row.label === activeValue}
                  onClick={() => onFilter(row.label)}
                />
              </th>
              {cross.categories.map((category) => (
                <td key={category} className="px-3 py-1.5 text-right tabular-nums text-app-text">
                  {row.cells[category] === undefined ? '' : formatNumber(row.cells[category])}
                </td>
              ))}
              <td className="px-3 py-1.5 text-right tabular-nums font-bold text-app-text">
                {formatNumber(row.total)}
              </td>
            </tr>
          ))}
          <tr className="bg-app-surface">
            <th scope="row" className="px-3 py-2 text-left font-extrabold text-app-text">
              Total
            </th>
            {cross.categories.map((category) => (
              <td key={category} className="px-3 py-2 text-right tabular-nums font-bold text-app-text">
                {formatNumber(totals[category])}
              </td>
            ))}
            <td className="px-3 py-2 text-right tabular-nums font-extrabold text-app-text">
              {formatNumber(grand)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
};

const MatrixTable: React.FC<{
  matrix: BIMatrix;
  groupLabel: string;
  activeValue: string;
  onFilter: (label: string) => void;
}> = ({ matrix, groupLabel, activeValue, onFilter }) => {
  if (!matrix.days.length || !matrix.rows.length) {
    return <Empty text="No hay datos diarios para el mes seleccionado." height={160} />;
  }
  const dayTotals = matrixDayTotals(matrix);
  const expected = 100 / Math.max(matrix.rows.length, 1);
  const grand = Object.values(dayTotals).reduce((total, value) => total + value, 0);
  return (
    <div className="space-y-2">
      <div className="overflow-auto max-h-[460px] rounded-xl border border-app-border">
        <table className="min-w-full text-[11px] border-collapse">
          <caption className="sr-only">
            {`Detalle diario por ${groupLabel}. Cada día muestra la cantidad y el porcentaje de participación.`}
          </caption>
          <thead className="sticky top-0 bg-app-surface z-10">
            <tr>
              <th scope="col" className="px-3 py-2 text-left font-bold text-app-muted border-b border-app-border">
                Día
              </th>
              {matrix.days.map((day) => (
                <th
                  key={day}
                  scope="col"
                  colSpan={2}
                  className="px-2 py-2 text-center font-bold text-app-muted border-b border-l border-app-border whitespace-nowrap"
                >
                  {dayLabel(day)}
                </th>
              ))}
              <th scope="col" className="px-3 py-2 text-right font-bold text-app-muted border-b border-l border-app-border">
                Total
              </th>
            </tr>
            <tr>
              <th scope="col" className="px-3 py-1 text-left font-semibold text-app-muted">
                {groupLabel}
              </th>
              {matrix.days.map((day) => (
                <React.Fragment key={day}>
                  <th scope="col" className="px-2 py-1 text-right font-semibold text-app-muted border-l border-app-border">
                    N°
                  </th>
                  <th scope="col" className="px-2 py-1 text-right font-semibold text-app-muted">
                    % Particip.
                  </th>
                </React.Fragment>
              ))}
              <th scope="col" className="px-3 py-1 text-right font-semibold text-app-muted border-l border-app-border" />
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr key={row.label} className="border-b border-app-border/70 last:border-0">
                <th scope="row" className="px-3 py-1.5 text-left whitespace-nowrap">
                  <FilterLabelButton
                    label={row.label}
                    active={row.label === activeValue}
                    onClick={() => onFilter(row.label)}
                  />
                </th>
                {matrix.days.map((day) => {
                  const value = row.cells[day];
                  if (value === undefined) {
                    return (
                      <React.Fragment key={day}>
                        <td className="px-2 py-1.5 border-l border-app-border/60" />
                        <td className="px-2 py-1.5" />
                      </React.Fragment>
                    );
                  }
                  const pct = participationPct(value, dayTotals[day] || 0);
                  const arrow = arrowFor(pct, expected);
                  return (
                    <React.Fragment key={day}>
                      <td className="px-2 py-1.5 text-right tabular-nums text-app-text border-l border-app-border/60 whitespace-nowrap">
                        {formatNumber(value)}{' '}
                        <span
                          className={
                            arrow === '↑'
                              ? 'text-emerald-600 dark:text-emerald-400 font-extrabold'
                              : arrow === '↓'
                                ? 'text-rose-600 dark:text-rose-400 font-extrabold'
                                : 'text-amber-600 dark:text-amber-400 font-extrabold'
                          }
                        >
                          {arrow}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right tabular-nums text-app-muted">
                        {pct.toFixed(2)} %
                      </td>
                    </React.Fragment>
                  );
                })}
                <td className="px-3 py-1.5 text-right tabular-nums font-bold text-app-text border-l border-app-border/60">
                  {formatNumber(row.total)}
                </td>
              </tr>
            ))}
            <tr className="bg-app-surface">
              <th scope="row" className="px-3 py-2 text-left font-extrabold text-app-text">
                Total
              </th>
              {matrix.days.map((day) => (
                <React.Fragment key={day}>
                  <td className="px-2 py-2 text-right tabular-nums font-bold text-app-text border-l border-app-border/60">
                    {formatNumber(dayTotals[day])}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums font-bold text-app-text">100.00 %</td>
                </React.Fragment>
              ))}
              <td className="px-3 py-2 text-right tabular-nums font-extrabold text-app-text border-l border-app-border/60">
                {formatNumber(grand)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-500/10 dark:border-amber-500/30 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200">
        Participación esperada si todos aportaran lo mismo: {expected.toFixed(1)} %.{' '}
        <span className="text-rose-600 dark:text-rose-400 font-bold">↓</span> bajo lo esperado ·{' '}
        <span className="text-amber-600 dark:text-amber-400 font-bold">→</span> cerca ·{' '}
        <span className="text-emerald-600 dark:text-emerald-400 font-bold">↑</span> sobre lo esperado.
      </div>
    </div>
  );
};

const DetailTable: React.FC<{ detail: BIDetail }> = ({ detail }) => {
  if (!detail.rows.length) {
    return <Empty text="No hay registros con los filtros aplicados." height={160} />;
  }
  const isNumeric = (value: unknown): boolean =>
    typeof value === 'number' || (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value));
  const renderValue = (value: unknown): string => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'number') return formatNumber(value, 2);
    return String(value);
  };
  return (
    <div className="overflow-auto max-h-[560px] rounded-xl border border-app-border">
      <table className="min-w-full text-[11px] border-collapse">
        <caption className="sr-only">Primeras filas con los filtros aplicados.</caption>
        <thead className="sticky top-0 bg-app-surface z-10">
          <tr>
            {detail.columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="px-3 py-2 text-left font-bold text-app-muted border-b border-app-border whitespace-nowrap"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {detail.rows.map((row, index) => (
            <tr key={index} className="border-b border-app-border/70 last:border-0">
              {detail.columns.map((column) => (
                <td
                  key={column}
                  className={`px-3 py-1.5 text-app-text ${
                    isNumeric(row[column]) ? 'text-right tabular-nums' : 'text-left'
                  }`}
                >
                  {renderValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const Guide: React.FC<{ data: BIReport }> = ({ data }) => {
  const roles = data.roles;
  const measure = describeMeasure(roles.measure);
  const period = formatPeriod(data.period);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card title="Conceptos clave">
        <div className="space-y-2 text-xs text-app-muted leading-relaxed">
          <p>
            <b className="text-app-text">Base de datos:</b> {data.connection.name} (incorporada en Dat.ia).
          </p>
          <p>
            <b className="text-app-text">Tabla:</b> {data.table.name}.
          </p>
          <p>
            <b className="text-app-text">Medida:</b> {measure}
            {roles.measure === 'count' ? ' (cada fila cuenta 1)' : ''}.
          </p>
          {roles.date && (
            <p>
              <b className="text-app-text">Fecha:</b> se usa <i>{roles.date}</i> para los filtros de año y mes.
            </p>
          )}
          {roles.group && (
            <p>
              <b className="text-app-text">Agrupación:</b> la columna <i>{roles.group}</i>.
            </p>
          )}
          {roles.category && (
            <p>
              <b className="text-app-text">Categoría:</b> la columna <i>{roles.category}</i>.
            </p>
          )}
        </div>
      </Card>
      <Card title="Cómo leer cada pestaña">
        <div className="space-y-3 text-xs text-app-muted leading-relaxed">
          <p>
            <b className="text-app-text">Resumen general.</b> Visión global de {period}. Los KPIs cuentan registros,
            total, grupos, días y promedios. La evolución y la distribución muestran el período elegido.
          </p>
          <p>
            <b className="text-app-text">Detalle diario.</b> Matriz con un renglón por {roles.group || 'grupo'} y una
            columna por día del mes. El porcentaje es el peso de esa fila sobre el total del día; las flechas comparan
            contra la participación esperada.
          </p>
          <p>
            <b className="text-app-text">Distribución.</b> Total por {roles.category || 'categoría'} y por{' '}
            {roles.group || 'grupo'} en el período.
          </p>
          <p>
            <b className="text-app-text">Datos.</b> Filas originales con los filtros aplicados, para revisar casos
            puntuales.
          </p>
        </div>
      </Card>
      <div className="lg:col-span-2 rounded-xl border border-amber-300/60 bg-amber-50 dark:bg-amber-500/10 dark:border-amber-500/30 px-4 py-3 text-[11px] text-amber-800 dark:text-amber-200">
        Los números se calculan en vivo contra la base cada vez que cambias un filtro. Solo ves las tablas que tu
        perfil tiene permitidas; las columnas confidenciales no aparecen.
      </div>
    </div>
  );
};

interface ReportSectionsProps {
  data: BIReport;
  tab: ReportTab;
  /** Valor activo del filtro de grupo (`gv` en la URL). */
  groupValue: string;
  /** Valor activo del filtro de categoría (`cv` en la URL). */
  categoryValue: string;
  /** Recibe el valor FINAL del filtro (ya con el toggle aplicado). */
  onFilterGroup: (value: string) => void;
  onFilterCategory: (value: string) => void;
  /** Drill: fija el mes del punto clickeado en la evolución mensual. */
  onDrillMonth: (month: number) => void;
}

export const ReportSections: React.FC<ReportSectionsProps> = ({
  data,
  tab,
  groupValue,
  categoryValue,
  onFilterGroup,
  onFilterCategory,
  onDrillMonth,
}) => {
  const measureName = describeMeasure(data.roles.measure);
  const period = formatPeriod(data.period);
  const monthSelected = Boolean(data.period.month);
  const delta = data.kpis.delta_pct ?? null;
  const previous = data.kpis.total_anterior ?? null;

  const toggleGroup = (label: string) => onFilterGroup(toggleFilterValue(groupValue, label));
  const toggleCategory = (label: string) =>
    onFilterCategory(toggleFilterValue(categoryValue, label));
  const drillFromChart = (label: string) => {
    if (monthSelected) return;
    const month = monthFromLabel(label);
    if (month) onDrillMonth(month);
  };

  const kpiItems = [
    // Con medida = registros, la tendencia vive en la tarjeta de registros;
    // con medida explícita, en la tarjeta de la medida.
    {
      label: 'Registros del período',
      value: data.kpis.registros,
      hint: '¿Cuántas filas hay?',
      ...(data.roles.measure === 'count' ? { delta, previous } : {}),
    },
    ...(data.roles.measure !== 'count'
      ? [{ label: measureName, value: data.kpis.total, hint: 'Total del período', delta, previous }]
      : []),
    ...(data.roles.group
      ? [
          {
            label: `${cap(data.roles.group)} con registros`,
            value: data.kpis.grupos,
            hint: `¿Cuántos ${data.roles.group} distintos?`,
          },
        ]
      : []),
    ...(data.roles.date
      ? [
          { label: 'Días con registros', value: data.kpis.dias, hint: 'Días distintos con datos' },
          {
            label: 'Promedio diario',
            value: data.kpis.promedio_diario,
            hint: `${measureName} por día`,
            decimals: 2,
          },
        ]
      : []),
    ...(data.roles.group
      ? [
          {
            label: `Promedio por ${data.roles.group}`,
            value: data.kpis.promedio_por_grupo,
            hint: `${measureName} por ${data.roles.group}`,
            decimals: 2,
          },
        ]
      : []),
  ];

  if (tab === 'resumen') {
    return (
      <div className="space-y-4">
        <KpiGrid items={kpiItems} />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card
            title={monthSelected ? 'Evolución diaria' : 'Evolución mensual'}
            hint={`${measureName} · ${period}${
              monthSelected ? '' : ' · clic en un mes para ver su detalle'
            }`}
          >
            {data.roles.date && data.daily.length ? (
              <BIChart
                option={lineOption(data.daily, monthSelected)}
                ariaLabel={`Evolución de ${measureName} en ${period}`}
                onPointClick={drillFromChart}
              />
            ) : (
              <Empty text="Sin columna de fecha para mostrar la evolución." />
            )}
          </Card>
          <Card
            title={`${measureName} por ${data.roles.group || '—'}`}
            hint="Ordenado de mayor a menor · clic en una barra para filtrar"
          >
            {data.roles.group && data.by_group.length ? (
              <BIChart
                option={barOption(data.by_group, COLORS[0], true)}
                ariaLabel={`${measureName} por ${data.roles.group}`}
                onPointClick={toggleGroup}
              />
            ) : (
              <Empty text="Elige una columna de agrupación para ver esta tarjeta." />
            )}
          </Card>
          <Card
            title={`Total por ${data.roles.category || '—'}`}
            hint="Participación de cada categoría · clic para filtrar"
          >
            {data.roles.category && data.by_category.length ? (
              <BIChart
                option={pieOption(data.by_category)}
                ariaLabel={`Distribución por ${data.roles.category}`}
                onPointClick={toggleCategory}
              />
            ) : (
              <Empty text="Elige una columna de categoría para ver la distribución." />
            )}
          </Card>
          <Card title="Resumen por grupo y categoría" hint="Totales del período · clic en un grupo para filtrar">
            {data.cross && data.roles.group && data.roles.category ? (
              <CrossTable
                cross={data.cross}
                groupLabel={data.roles.group}
                activeValue={groupValue}
                onFilter={toggleGroup}
              />
            ) : (
              <Empty text='Elige "Agrupar por" y "Categoría" para ver este cuadro.' height={160} />
            )}
          </Card>
        </div>
      </div>
    );
  }

  if (tab === 'diario') {
    return (
      <div className="space-y-4">
        <KpiGrid items={kpiItems.filter((item) => item.label !== 'Días con registros')} />
        <Card
          title={`${measureName} diarios por ${data.roles.group || 'grupo'}`}
          hint="N° y porcentaje de participación por día · clic en un grupo para filtrar"
        >
          {data.matrix ? (
            <MatrixTable
              matrix={data.matrix}
              groupLabel={data.roles.group || 'Grupo'}
              activeValue={groupValue}
              onFilter={toggleGroup}
            />
          ) : (
            <Empty
              text={
                !data.roles.date
                  ? 'Esta tabla no tiene columna de fecha.'
                  : !data.roles.group
                    ? 'Elige "Agrupar por" para ver el detalle diario.'
                    : 'Elige un mes en el filtro "Mes" para ver el detalle por día.'
              }
            />
          )}
        </Card>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card title="Evolución diaria" hint={`${measureName} · ${period}`}>
            {data.roles.date && data.daily.length ? (
              <BIChart
                option={lineOption(data.daily, monthSelected, true)}
                ariaLabel={`Evolución diaria de ${measureName}`}
              />
            ) : (
              <Empty text="Sin columna de fecha." />
            )}
          </Card>
          <Card
            title={`${measureName} por ${data.roles.group || '—'}`}
            hint="Total del período · clic en una barra para filtrar"
          >
            {data.roles.group && data.by_group.length ? (
              <BIChart
                option={barOption(data.by_group, '#10B981', true)}
                ariaLabel={`${measureName} por ${data.roles.group}`}
                onPointClick={toggleGroup}
              />
            ) : (
              <Empty text="Elige una columna de agrupación." />
            )}
          </Card>
        </div>
      </div>
    );
  }

  if (tab === 'distribucion') {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card
          title={`Distribución por ${data.roles.category || '—'}`}
          hint="Total del período · clic en una barra para filtrar"
        >
          {data.roles.category && data.by_category.length ? (
            <BIChart
              option={barOption(data.by_category, COLORS[0])}
              height={340}
              ariaLabel={`Distribución por ${data.roles.category}`}
              onPointClick={toggleCategory}
            />
          ) : (
            <Empty text="Elige una columna de categoría." height={340} />
          )}
        </Card>
        <Card
          title={`${measureName} por ${data.roles.group || '—'}`}
          hint="Total del período · clic en una barra para filtrar"
        >
          {data.roles.group && data.by_group.length ? (
            <BIChart
              option={barOption(data.by_group, '#F59E0B', true)}
              height={340}
              ariaLabel={`${measureName} por ${data.roles.group}`}
              onPointClick={toggleGroup}
            />
          ) : (
            <Empty text="Elige una columna de agrupación." height={340} />
          )}
        </Card>
      </div>
    );
  }

  if (tab === 'datos') {
    return (
      <Card
        title="Registros"
        hint={`Primeras ${data.detail.rows.length} filas con los filtros aplicados${
          data.roles.date ? ', de la más reciente a la más antigua' : ''
        }.`}
      >
        <DetailTable detail={data.detail} />
      </Card>
    );
  }

  return <Guide data={data} />;
};
