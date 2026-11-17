import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import { getApiErrorMessage } from '../shared/api/api_client';
import { biService } from '../features/bi/services/bi_service';
import { ReportSections } from '../features/bi/components/ReportSections';
import {
  EMPTY_REPORT_STATE,
  MONTHS,
  REPORT_TABS,
  REPORT_TAB_LABELS,
  ReportState,
  buildReportParams,
  buildReportSearch,
  formatPeriod,
  readReportState,
} from '../features/bi/report_logic';
import { BIConnection, BIReport, BISchema } from '../features/bi/types';

const selectClass =
  'rounded-lg border border-app-border bg-app-surface px-2 py-1.5 text-xs text-app-text focus:outline-none focus:ring-2 focus:ring-brand-500 max-w-[220px]';
const labelClass = 'flex flex-col gap-1 text-[10px] font-bold uppercase tracking-wide text-app-muted';

const reportSearch = (state: ReportState): string => buildReportSearch(state);

export const BIReportPage: React.FC = () => {
  const location = useLocation();
  const initialState = useRef(readReportState(location.search)).current;
  const [state, setState] = useState<ReportState>(initialState);
  const [connections, setConnections] = useState<BIConnection[]>([]);
  const [schema, setSchema] = useState<BISchema | null>(null);
  const [report, setReport] = useState<BIReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reportRequest = useRef(0);
  const schemaRequest = useRef(0);
  const [, setThemeVersion] = useState(0);

  const saveUrl = useCallback((next: ReportState) => {
    const search = reportSearch(next);
    window.history.replaceState(null, '', `#/powerbi${search}`);
  }, []);

  const loadReport = useCallback(
    async (next: ReportState) => {
      if (!next.conn || !next.table) {
        setLoading(false);
        return;
      }
      const requestId = ++reportRequest.current;
      setLoading(true);
      setError(null);
      try {
        const data = await biService.report(buildReportParams(next));
        if (requestId !== reportRequest.current) return;
        const normalized: ReportState = {
          ...next,
          date: data.roles.date ? next.date || data.roles.date : next.date === '-' ? '-' : '',
          group: data.roles.group || '',
          cat: data.roles.category || '',
          measure: data.roles.measure || 'count',
          year: data.period.year ? String(data.period.year) : '',
          month: data.period.month ? String(data.period.month) : data.period.year ? 'all' : '',
        };
        setState(normalized);
        setReport(data);
        saveUrl(normalized);
      } catch (err) {
        if (requestId !== reportRequest.current) return;
        setReport(null);
        setError(getApiErrorMessage(err, 'No se pudo cargar el informe.', true));
      } finally {
        if (requestId === reportRequest.current) setLoading(false);
      }
    },
    [saveUrl],
  );

  const loadSchema = useCallback(
    async (connectionId: string, preferredTable: string, base: ReportState) => {
      const requestId = ++schemaRequest.current;
      setSchemaLoading(true);
      setError(null);
      try {
        const nextSchema = await biService.getSchema(Number(connectionId));
        if (requestId !== schemaRequest.current) return;
        setSchema(nextSchema);
        const tables = [...nextSchema.tables].sort((a, b) => b.columns.length - a.columns.length);
        const chosen = tables.some((table) => table.name === preferredTable)
          ? preferredTable
          : tables[0]?.name || '';
        const next: ReportState = {
          ...base,
          conn: connectionId,
          table: chosen,
          date: '',
          group: '',
          cat: '',
          measure: 'count',
          year: '',
          month: '',
          gv: '',
          cv: '',
        };
        setState(next);
        if (!chosen) {
          setReport(null);
          setLoading(false);
          setError('Esta base no tiene tablas disponibles para tu perfil.');
          return;
        }
        await loadReport(next);
      } catch (err) {
        if (requestId !== schemaRequest.current) return;
        setSchema(null);
        setReport(null);
        setError(getApiErrorMessage(err, 'No se pudo leer la estructura de la base.', true));
      } finally {
        if (requestId === schemaRequest.current) setSchemaLoading(false);
      }
    },
    [loadReport],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const available = await biService.listConnections();
        if (cancelled) return;
        setConnections(available);
        if (!available.length) {
          setError('No hay bases disponibles para tu perfil. Pide permisos en Gobernanza RBAC o incorpora una base.');
          setLoading(false);
          return;
        }
        const wantsExisting = initialState.conn
          && available.some((connection) => String(connection.id) === initialState.conn);
        const connectionId = wantsExisting
          ? initialState.conn
          : String((available.find((connection) => connection.is_active) || available[0]).id);
        await loadSchema(connectionId, initialState.table, {
          ...EMPTY_REPORT_STATE,
          ...initialState,
          conn: connectionId,
        });
      } catch (err) {
        if (cancelled) return;
        setError(getApiErrorMessage(err, 'No se pudieron cargar las bases disponibles.', true));
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // La carga inicial se hace una vez; los cambios posteriores pasan por los handlers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeVersion((version) => version + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  const update = (patch: Partial<ReportState>) => {
    const next = { ...state, ...patch };
    setState(next);
    saveUrl(next);
    void loadReport(next);
  };

  const changeConnection = (connectionId: string) => {
    if (!connectionId) return;
    const next: ReportState = {
      ...state,
      conn: connectionId,
      table: '',
      date: '',
      group: '',
      cat: '',
      measure: 'count',
      year: '',
      month: '',
      gv: '',
      cv: '',
    };
    setState(next);
    setSchema(null);
    setReport(null);
    void loadSchema(connectionId, '', next);
  };

  const changeTable = (table: string) => {
    const next: ReportState = {
      ...state,
      table,
      date: '',
      group: '',
      cat: '',
      measure: 'count',
      year: '',
      month: '',
      gv: '',
      cv: '',
    };
    setState(next);
    saveUrl(next);
    void loadReport(next);
  };

  const changeDate = (date: string) => {
    update({ date, year: '', month: '', gv: '', cv: '' });
  };

  const changeGroup = (group: string) => {
    update({ group, gv: '' });
  };

  const changeCategory = (cat: string) => {
    update({ cat, cv: '' });
  };

  const changeMeasure = (measure: string) => {
    update({ measure });
  };

  const changeYear = (year: string) => {
    update({ year, month: '' });
  };

  const changeMonth = (month: string) => {
    update({ month });
  };

  const changeTab = (tab: ReportState['tab']) => {
    const next = { ...state, tab };
    setState(next);
    saveUrl(next);
  };

  // Cross-filtering y drill: el clic termina en el mismo estado de URL que los
  // selects del encabezado, así el resultado queda compartible por enlace.
  const filterGroup = (value: string) => update({ gv: value });
  const filterCategory = (value: string) => update({ cv: value });
  const drillMonth = (month: number) => update({ month: String(month) });

  const tableColumns =
    schema?.tables.find((table) => table.name === state.table)?.columns ?? [];
  const selectable = schema?.is_admin
    ? tableColumns
    : tableColumns.filter((column) => !column.masked);
  const dateColumns = selectable.filter((column) => column.kind === 'date');
  const groupColumns = selectable.filter(
    (column) => column.kind !== 'number' || /id_|_id$/.test(column.name),
  );
  const categoryColumns = selectable.filter((column) => column.kind !== 'number');
  const measureColumns = selectable.filter((column) => column.kind === 'number');

  const periodText = report ? formatPeriod(report.period) : 'Elige una base y una tabla';
  const showDateSlicers = Boolean(report?.roles.date);
  const showGroupSlicer = Boolean(report?.roles.group);
  const showCategorySlicer = Boolean(report?.roles.category);

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--app-bg)]">
      <div className="max-w-[1440px] mx-auto px-3 sm:px-5 py-4 space-y-4">
        <section className="rounded-2xl border border-app-border bg-app-card p-3 shadow-sm">
          <div className="flex flex-wrap items-end gap-3">
            <div className="mr-2">
              <div className="text-sm font-extrabold text-app-text">Power BI · Informe BI</div>
              <div className="text-[11px] text-app-muted">
                Datos en vivo de las bases incorporadas, según los permisos de tu perfil.
              </div>
            </div>

            <label className={labelClass}>
              Base de datos
              <select
                className={selectClass}
                value={state.conn}
                onChange={(event) => changeConnection(event.target.value)}
                aria-label="Base de datos"
              >
                {connections.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.name}
                    {connection.is_active ? '' : ' (inactiva)'}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              Tabla
              <select
                className={selectClass}
                value={state.table}
                onChange={(event) => changeTable(event.target.value)}
                disabled={!schema || schemaLoading}
                aria-label="Tabla"
              >
                {schemaLoading && <option value="">Cargando…</option>}
                {!schemaLoading && !tableColumns.length && <option value="">Sin tablas</option>}
                {schema?.tables.map((table) => (
                  <option key={table.name} value={table.name}>
                    {table.name}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              Columna de fecha
              <select
                className={selectClass}
                value={state.date}
                onChange={(event) => changeDate(event.target.value)}
                disabled={!state.table}
                aria-label="Columna de fecha"
              >
                <option value="">Automática</option>
                <option value="-">(sin fecha)</option>
                {dateColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              Agrupar por
              <select
                className={selectClass}
                value={state.group}
                onChange={(event) => changeGroup(event.target.value)}
                disabled={!state.table}
                aria-label="Agrupar por"
              >
                <option value="">Automático</option>
                {groupColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              Categoría
              <select
                className={selectClass}
                value={state.cat}
                onChange={(event) => changeCategory(event.target.value)}
                disabled={!state.table}
                aria-label="Categoría"
              >
                <option value="">Automática</option>
                {categoryColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    {column.name}
                  </option>
                ))}
              </select>
            </label>

            <label className={labelClass}>
              Medida
              <select
                className={selectClass}
                value={state.measure || 'count'}
                onChange={(event) => changeMeasure(event.target.value)}
                disabled={!state.table}
                aria-label="Medida"
              >
                <option value="count">Cantidad de registros</option>
                {measureColumns.map((column) => (
                  <option key={column.name} value={column.name}>
                    Suma de {column.name}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => void loadReport(state)}
              className="flex items-center gap-1.5 rounded-lg border border-app-border bg-app-surface px-3 py-2 text-xs font-semibold text-app-text hover:bg-app-hover"
              title="Volver a consultar la base"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Actualizar
            </button>
          </div>
        </section>

        {error && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-300/60 bg-rose-50 dark:bg-rose-500/10 dark:border-rose-500/30 px-4 py-3 text-xs text-rose-700 dark:text-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="flex-1">{error}</div>
            <button
              type="button"
              onClick={() => void loadReport(state)}
              className="font-bold underline underline-offset-2"
            >
              Reintentar
            </button>
          </div>
        )}

        {loading && !report && (
          <div className="flex min-h-[320px] items-center justify-center gap-3 rounded-2xl border border-app-border bg-app-card text-sm text-app-muted">
            <Loader2 className="h-5 w-5 animate-spin" />
            Calculando el informe…
          </div>
        )}

        {!loading && !report && !error && (
          <div className="rounded-2xl border border-dashed border-app-border bg-app-card px-6 py-16 text-center text-sm text-app-muted">
            Elige una base y una tabla para empezar.
          </div>
        )}

        {report && (
          <div className="space-y-3">
            <div className="overflow-hidden rounded-2xl border border-app-border shadow-sm">
              <div className="bg-gradient-to-br from-brand-700 via-brand-600 to-brand-700 text-white px-5 py-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-white/30 bg-white/15 text-lg font-extrabold">
                      D
                    </div>
                    <div className="min-w-0">
                      <h1 className="truncate text-xl font-extrabold">{report.connection.name}</h1>
                      <p className="truncate text-sm text-brand-100">
                        {report.table.name} · {periodText}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-end gap-2">
                    {showDateSlicers && (
                      <>
                        <label className="flex flex-col gap-1 text-[10px] font-bold uppercase tracking-wide text-brand-100">
                          Año
                          <select
                            value={state.year}
                            onChange={(event) => changeYear(event.target.value)}
                            className="rounded-lg border border-white/20 bg-white/95 px-2 py-1.5 text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-white/60"
                            aria-label="Año"
                          >
                            <option value="">Automático</option>
                            {report.period.years.map((year) => (
                              <option key={year} value={year}>
                                {year}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="flex flex-col gap-1 text-[10px] font-bold uppercase tracking-wide text-brand-100">
                          Mes
                          <select
                            value={state.month}
                            onChange={(event) => changeMonth(event.target.value)}
                            className="rounded-lg border border-white/20 bg-white/95 px-2 py-1.5 text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-white/60"
                            aria-label="Mes"
                          >
                            <option value="all">Todos</option>
                            {report.period.months.map((month) => (
                              <option key={month} value={month}>
                                {MONTHS[month - 1]}
                              </option>
                            ))}
                          </select>
                        </label>
                      </>
                    )}
                    {showGroupSlicer && (
                      <label className="flex flex-col gap-1 text-[10px] font-bold uppercase tracking-wide text-brand-100">
                        {report.roles.group}
                        <select
                          value={state.gv}
                          onChange={(event) => update({ gv: event.target.value })}
                          className="rounded-lg border border-white/20 bg-white/95 px-2 py-1.5 text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-white/60"
                          aria-label={`Filtrar por ${report.roles.group}`}
                        >
                          <option value="">Todos</option>
                          {report.options.group.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {showCategorySlicer && (
                      <label className="flex flex-col gap-1 text-[10px] font-bold uppercase tracking-wide text-brand-100">
                        {report.roles.category}
                        <select
                          value={state.cv}
                          onChange={(event) => update({ cv: event.target.value })}
                          className="rounded-lg border border-white/20 bg-white/95 px-2 py-1.5 text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-white/60"
                          aria-label={`Filtrar por ${report.roles.category}`}
                        >
                          <option value="">Todas</option>
                          {report.options.category.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                </div>
              </div>

              <nav
                className="flex overflow-x-auto border-t border-app-border bg-app-card"
                role="tablist"
                aria-label="Páginas del informe"
              >
                {REPORT_TABS.map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={state.tab === tab}
                    aria-controls={`bi-panel-${tab}`}
                    id={`bi-tab-${tab}`}
                    onClick={() => changeTab(tab)}
                    className={`whitespace-nowrap border-r border-app-border px-4 py-3 text-xs font-semibold transition-colors ${
                      state.tab === tab
                        ? 'text-brand-700 dark:text-brand-300 shadow-[inset_0_-3px_0_var(--brand-600)] bg-app-surface'
                        : 'text-app-muted hover:bg-app-hover hover:text-app-text'
                    }`}
                  >
                    {REPORT_TAB_LABELS[tab]}
                  </button>
                ))}
              </nav>
            </div>

            <div
              id={`bi-panel-${state.tab}`}
              role="tabpanel"
              aria-labelledby={`bi-tab-${state.tab}`}
              className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}
            >
              <ReportSections
                data={report}
                tab={state.tab}
                groupValue={state.gv}
                categoryValue={state.cv}
                onFilterGroup={filterGroup}
                onFilterCategory={filterCategory}
                onDrillMonth={drillMonth}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
