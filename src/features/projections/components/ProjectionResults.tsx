import React, { useId, useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, FileSpreadsheet, FileText, Info, Loader2, Sparkles, TrendingUp } from 'lucide-react';
import { ProjectionChart } from './ProjectionChart';
import type { ProjectionGroupSummary, ProjectionResult } from '../services/projection_service';
import { sumLastPeriods, variationPct } from '../services/projection_metrics';
import { reportService } from '../../dashboard/services/report_service';
import { useNotifications } from '../../../context/NotificationContext';

/**
 * Resultados de una proyeccion. Dos reglas de honestidad gobiernan este archivo:
 *
 * 1. "Sin evidencia" es un estado visible, nunca un cero. Todo `null` del
 *    backend (punto, banda, MAPE de un paso sin backtest) se pinta "—" con su
 *    explicacion; un 0 seria una afirmacion sobre el futuro que nadie midio.
 * 2. Los limites se muestran con el mismo peso que el numero: banda por paso,
 *    n_backtests, MAPE, `reliable` y `extrapolated` van en la tabla, no
 *    escondidos tras un "ver detalle".
 */

const money = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `$${Math.round(n).toLocaleString('es-CL', { maximumFractionDigits: 0 })}`;

const pct = (n: number | null | undefined): string =>
  n === null || n === undefined ? '—' : `${Number(n).toLocaleString('es-CL', { maximumFractionDigits: 1 })}%`;

const METHOD_LABELS: Record<string, string> = {
  naive: 'Naive (último valor)',
  mean: 'Media histórica',
  ols_trend: 'Tendencia lineal (OLS)',
};

const methodLabel = (m?: string | null): string => (m ? METHOD_LABELS[m] ?? m : '—');

const GRANULARITY_LABELS: Record<string, string> = {
  day: 'Día',
  week: 'Semana',
  month: 'Mes',
};

const granularityLabel = (value?: string | null): string =>
  GRANULARITY_LABELS[value ?? 'month'] ?? 'Mes';

const tableSourceLabel = (source?: string | null): string => {
  if (source === 'manual') return 'elegida manualmente';
  if (source === 'catalogo') return 'elegida por pregunta/catálogo';
  if (source === 'mayor') return 'la de más filas';
  return '—';
};

const QUALITY_LABELS: Record<string, string> = {
  confiable: 'Confiable',
  exploratoria: 'Exploratoria',
  no_confiable: 'No confiable',
  sin_medicion: 'Sin medición de error',
};

const QUALITY_STYLES: Record<string, string> = {
  confiable:
    'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300',
  exploratoria:
    'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
  no_confiable:
    'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/60 dark:text-rose-300',
  sin_medicion:
    'border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-300',
};

const qualityLabel = (status?: string | null): string =>
  QUALITY_LABELS[status ?? ''] ?? 'Sin medición de error';

const qualityClass = (status?: string | null): string =>
  QUALITY_STYLES[status ?? ''] ?? QUALITY_STYLES.sin_medicion;

/**
 * Que significa cada estado, en una frase. Los `reasons` del backend dicen que
 * regla se disparo; esto explica el criterio cuando no hay ninguna (confiable).
 */
const QUALITY_EXPLANATIONS: Record<string, string> = {
  confiable:
    'Las reglas de calidad no encontraron alertas con la evidencia publicada.',
  exploratoria:
    'La proyección se puede leer, pero hay señales que reducen la confianza: error medio, poca evidencia en algún paso o huecos en la serie.',
  no_confiable:
    'El error medido o la cobertura de la banda no permiten tratar estas cifras como una estimación confiable.',
  sin_medicion:
    'No se pudo medir el error: no hay backtests suficientes para afirmar qué tan precisa es la proyección.',
};

/**
 * Badge de calidad con su justificación al pasar el mouse (o al enfocar con el
 * teclado). Antes el motivo viajaba en un `title` nativo: al instante, sin
 * formato y sin funcionar con teclado. La nota abre en hover/focus y muestra
 * las mismas reglas que aplicó el servidor, no una explicación inventada por la
 * UI.
 */
const QualityBadge: React.FC<{ result: ProjectionResult; compact?: boolean }> = ({
  result,
  compact = false,
}) => {
  const quality = result.quality;
  const tooltipId = useId();
  if (!quality) return null;

  const status = quality.status ?? '';
  const summary =
    QUALITY_EXPLANATIONS[status] ?? 'El servidor no publicó una explicación para este estado.';

  // Para "confiable" el backend devuelve `reasons: []`: la nota explica las
  // reglas que se cumplieron con los números que ya están en la respuesta.
  const checks: string[] = [];
  if (status === 'confiable') {
    if (quality.mape !== null && quality.mape !== undefined) {
      checks.push(
        quality.exploratory_threshold !== null && quality.exploratory_threshold !== undefined
          ? `MAPE global ${pct(quality.mape)}, por debajo del umbral exploratorio ${pct(quality.exploratory_threshold)}.`
          : `MAPE global ${pct(quality.mape)}.`
      );
    }
    if (result.beats_naive === true) {
      checks.push('Le ganó al naive en el walk-forward.');
    }
    const measured = result.total?.measured_steps;
    if (measured !== null && measured !== undefined && measured >= result.horizon) {
      checks.push(`La banda del total está medida sobre los ${result.horizon} pasos.`);
    }
    const minBacktests = Math.min(
      ...result.steps.filter((step) => step.point !== null).map((step) => step.n_backtests)
    );
    if (Number.isFinite(minBacktests) && minBacktests >= 4) {
      checks.push(`El paso con menos evidencia tiene ${minBacktests} backtests.`);
    }
    if (!result.has_gaps) {
      checks.push('La serie no tiene periodos faltantes.');
    }
  }

  const reasons = status === 'confiable' ? [] : quality.reasons ?? [];
  const hasThresholds =
    quality.exploratory_threshold !== null &&
    quality.exploratory_threshold !== undefined &&
    quality.unreliable_threshold !== null &&
    quality.unreliable_threshold !== undefined;

  return (
    <span className="group relative inline-flex">
      <span
        tabIndex={0}
        aria-describedby={tooltipId}
        className={`inline-flex cursor-help items-center rounded-md border font-semibold underline decoration-dotted underline-offset-2 ${compact ? 'px-2 py-0.5 text-[10px]' : 'px-2 py-0.5 text-[11px]'} ${qualityClass(status)}`}
      >
        {qualityLabel(status)}
      </span>
      <span
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none invisible absolute right-0 top-full z-40 mt-1.5 w-[19rem] max-w-[80vw] rounded-xl border border-app-border bg-app-surface p-3 text-left text-[11px] font-normal leading-relaxed text-app-text opacity-0 shadow-2xl transition-opacity duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        <span className="block text-xs font-bold text-app-text">{qualityLabel(status)}</span>
        <span className="mt-1 block text-app-muted">{summary}</span>

        {checks.length > 0 && (
          <span className="mt-2 block space-y-1">
            {checks.map((check) => (
              <span key={check} className="flex items-start gap-1.5">
                <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-emerald-500" />
                <span>{check}</span>
              </span>
            ))}
          </span>
        )}

        {reasons.length > 0 && (
          <span className="mt-2 block space-y-1">
            {reasons.map((reason) => (
              <span key={reason} className="flex items-start gap-1.5">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" aria-hidden />
                <span>{reason}</span>
              </span>
            ))}
          </span>
        )}

        {hasThresholds && status !== 'confiable' && (
          <span className="mt-2 block border-t border-app-border/70 pt-1.5 text-[10px] text-app-muted">
            Umbrales del servidor: exploratoria si el MAPE supera{' '}
            {pct(quality.exploratory_threshold)}; no confiable si supera{' '}
            {pct(quality.unreliable_threshold)}.
          </span>
        )}
      </span>
    </span>
  );
};

const horizonSourceLabel = (source?: string | null): string | null => {
  if (source === 'pedido') return 'elegido en el pedido';
  if (source === 'llm') return 'ajustado por la IA (Auto)';
  if (source === 'default') return 'valor por defecto de Auto';
  return null;
};

/**
 * Exportar el Informe (PDF/Excel) de la corrida auditada.
 *
 * Llama a los endpoints de export con el `audit_log_id` de la proyeccion: el
 * backend compila la seccion de proyeccion desde el snapshot de auditoria (el
 * toolbar del chat no sirve aca, esta acoplado a `QueryResult`). Sin
 * `audit_log_id` no hay documento posible y no se muestran botones: un boton
 * que siempre falla es peor que un boton ausente.
 *
 * El grafico se captura con `scope` (el contenedor del chart) para que el PDF
 * incruste ESTA figura y no la primera de la pagina.
 */
const ExportToolbar: React.FC<{
  result: ProjectionResult;
  chartScopeRef?: React.RefObject<HTMLDivElement | null>;
}> = ({ result, chartScopeRef }) => {
  const { notify } = useNotifications();
  const [busy, setBusy] = useState<'pdf' | 'excel' | null>(null);

  const auditLogId = result.audit_log_id;
  if (!auditLogId) {
    return null;
  }

  const run = async (kind: 'pdf' | 'excel') => {
    if (busy) return;
    setBusy(kind);
    try {
      if (kind === 'pdf') {
        const chartBase64 = chartScopeRef
          ? await reportService.captureChartAsBase64(chartScopeRef.current)
          : undefined;
        await reportService.exportExecutiveReportPdf({ audit_log_id: auditLogId }, { chartBase64 });
        notify('success', 'Informe PDF generado con la proyección publicada.');
      } else {
        await reportService.exportExecutiveReportExcel({ audit_log_id: auditLogId });
        notify('success', 'Excel generado con la proyección publicada.');
      }
    } catch (err: any) {
      notify('error', err?.message || 'No se pudo generar el documento de la proyección.');
    } finally {
      setBusy(null);
    }
  };

  const buttonClass =
    'flex items-center gap-2 px-3 py-2 rounded-xl bg-app-card hover:bg-app-hover text-app-muted hover:text-app-text border border-app-border text-xs font-semibold transition-colors shadow-xs disabled:opacity-50';

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => void run('pdf')}
        disabled={busy !== null}
        aria-label="Descargar informe PDF de la proyección"
        className={buttonClass}
      >
        {busy === 'pdf' ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden />
        ) : (
          <FileText className="w-3.5 h-3.5 text-rose-400" aria-hidden />
        )}
        <span>{busy === 'pdf' ? 'Generando…' : 'Informe PDF'}</span>
      </button>
      <button
        type="button"
        onClick={() => void run('excel')}
        disabled={busy !== null}
        aria-label="Descargar Excel de la proyección"
        className={buttonClass}
      >
        {busy === 'excel' ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden />
        ) : (
          <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-400" aria-hidden />
        )}
        <span>{busy === 'excel' ? 'Generando…' : 'Excel'}</span>
      </button>
    </div>
  );
};

/** Periodos que el backend descarto antes de calcular, con su motivo. */
const ExclusionNotes: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const futuros = result.future_periods_excluded ?? [];
  if (!result.partial_period_excluded && futuros.length === 0) return null;

  return (
    <div className="mt-3 flex items-start gap-1.5 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-xs leading-relaxed text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
      <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
      <span>
        {result.partial_period_excluded && (
          <>
            Se excluyó <strong>{result.partial_period_excluded}</strong> por ser un periodo en
            curso incompleto.{' '}
          </>
        )}
        {futuros.length > 0 && (
          <>
            También se descartaron periodos futuros: {futuros.join(', ')}.{' '}
          </>
        )}
      </span>
    </div>
  );
};

/**
 * Proyeccion no disponible: es una respuesta, no un error.
 *
 * El backend manda `reason` (serie corta, columna bloqueada por el rol, falta
 * de eje temporal) y las columnas que alcanzo a elegir. Todo eso se muestra:
 * negar sin decir que falta obliga a adivinar.
 */
const UnavailableBlock: React.FC<{ result: ProjectionResult }> = ({ result }) => (
  <section
    role="status"
    className="rounded-2xl border border-amber-300 bg-amber-50 p-5 dark:border-amber-800/60 dark:bg-amber-950/40"
  >
    <h2 className="flex items-center gap-2 text-sm font-bold text-amber-900 dark:text-amber-200">
      <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
      Proyección no disponible
    </h2>
    <p className="mt-2 text-sm leading-relaxed text-amber-900 dark:text-amber-100">
      {result.reason || 'El servidor no pudo calcular una proyección para esta pregunta.'}
    </p>

    {(result.table || result.metric_column || result.date_column) && (
      <dl className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-amber-800/80 dark:text-amber-300/80">Tabla</dt>
          <dd className="font-mono text-amber-900 dark:text-amber-100">{result.table ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-amber-800/80 dark:text-amber-300/80">Métrica</dt>
          <dd className="font-mono text-amber-900 dark:text-amber-100">{result.metric_column ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-amber-800/80 dark:text-amber-300/80">Eje temporal</dt>
          <dd className="font-mono text-amber-900 dark:text-amber-100">{result.date_column ?? '—'}</dd>
        </div>
      </dl>
    )}

    {result.series.length > 0 && (
      <p className="mt-3 text-xs text-amber-800 dark:text-amber-300">
        La serie sí se pudo leer ({result.series.length} periodos):{' '}
        {result.series
          .map((r) => r.periodo ?? 'sin periodo')
          .join(' · ')}
      </p>
    )}

    <ExclusionNotes result={result} />

    <div className="mt-4">
      <ExportToolbar result={result} />
    </div>
  </section>
);

const EvidenceChip: React.FC<{ step: ProjectionResult['steps'][number] }> = ({ step }) => {
  if (step.extrapolated) {
    return (
      <span
        className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        title="El paso no llegó a tener backtest propio: hereda la banda del último paso medido."
      >
        sin backtest propio
      </span>
    );
  }
  if (!step.reliable) {
    return (
      <span
        className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        title="Menos de 4 backtests en este paso: la banda existe pero es frágil."
      >
        poca evidencia
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-md border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
      medida
    </span>
  );
};

const TotalCard: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const total = result.total;
  if (!total || total.point === null) {
    return (
      <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <h3 className="text-sm font-bold text-app-text">Total del horizonte</h3>
          <QualityBadge result={result} />
        </header>
        <p className="mt-2 text-xs leading-relaxed text-app-muted">
          No se pudo sumar el total porque falta el punto de algún paso. Los pasos publicados
          siguen en la tabla, cada uno con su evidencia.
        </p>
        {result.quality?.reasons?.length ? (
          <ul className="mt-2 space-y-1 text-[11px] leading-relaxed text-app-muted">
            {result.quality.reasons.map((reason) => (
              <li key={reason} className="flex items-start gap-1.5">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" aria-hidden />
                {reason}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    );
  }

  const totalBandIncomplete =
    total.extrapolated ||
    (total.measured_steps !== null && total.measured_steps < result.horizon);
  const rango = totalBandIncomplete
    ? 'Sin banda para el horizonte completo'
    : total.lower !== null && total.upper !== null
      ? `${money(total.lower)} – ${money(total.upper)}`
      : 'Sin banda medida';
  const evidencia =
    total.measured_steps === null
      ? 'No hubo un prefijo medible: el total viaja sin banda.'
      : total.extrapolated
        ? `Banda heredada del total de ${total.measured_steps} ${
            total.measured_steps === 1 ? 'paso' : 'pasos'
          }; no cubre los ${result.horizon} pasos.`
        : total.reliable
          ? `Banda medida con ${total.n_backtests} backtests sobre el horizonte completo.`
          : `Banda medida con ${total.n_backtests} backtests: evidencia escasa.`;

  // Contexto, no error: se compara el total proyectado con la suma de los
  // mismos periodos inmediatamente anteriores. Con huecos no se muestra, porque
  // una suma sobre una serie incompleta no es comparable.
  const previous = result.has_gaps ? null : sumLastPeriods(result.series, result.horizon);
  const variation = previous === null ? null : variationPct(previous, total.point);
  const historyText =
    previous !== null
      ? `Contexto histórico: los ${result.horizon} periodos previos suman ${money(previous)}${
          variation !== null
            ? ` (${variation >= 0 ? '+' : ''}${pct(variation)})`
            : ''
        }. No es una medida de error de la proyección.`
      : result.has_gaps
        ? 'Sin comparación histórica: la serie tiene periodos faltantes.'
        : result.series.length > 0 && result.series.length < result.horizon
          ? `Sin comparación histórica: hay ${result.series.length} periodos previos y se necesitan ${result.horizon}.`
          : null;

  return (
    <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-app-text">Total del horizonte</h3>
          <p className="mt-0.5 text-xs text-app-muted">
            Suma de los {result.horizon} pasos publicados.
          </p>
        </div>
        <span
          className={
            total.extrapolated || !total.reliable
              ? 'inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
              : 'inline-flex items-center rounded-md border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
          }
        >
          {total.extrapolated ? 'medición parcial' : total.reliable ? 'medida' : 'poca evidencia'}
        </span>
        {result.quality && (
          <QualityBadge result={result} />
        )}
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">
        <div>
          <dt className="text-app-muted">Total</dt>
          <dd className="mt-0.5 text-lg font-bold tabular-nums text-app-text">{money(total.point)}</dd>
        </div>
        <div>
          <dt className="text-app-muted">Rango</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-app-text">{rango}</dd>
        </div>
        <div>
          <dt className="text-app-muted">Banda</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-app-text">
            {totalBandIncomplete
              ? 'Sin banda completa'
              : total.band_pct === null
                ? '—'
                : `±${pct(total.band_pct)}`}
          </dd>
        </div>
        <div>
          <dt className="text-app-muted">MAPE del total</dt>
          <dd className="mt-0.5 font-semibold tabular-nums text-app-text">{pct(total.mape)}</dd>
        </div>
      </dl>

      <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-app-muted">
        <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
        {evidencia}
      </p>
      {total.lower_clamped && (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-app-muted">
          <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          Banda inferior acotada a 0: una cantidad no puede ser negativa.
        </p>
      )}
      {result.quality?.reasons?.length ? (
        <ul className="mt-2 space-y-1 text-[11px] leading-relaxed text-app-muted">
          {result.quality.reasons.map((reason) => (
            <li key={reason} className="flex items-start gap-1.5">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" aria-hidden />
              {reason}
            </li>
          ))}
        </ul>
      ) : null}
      {historyText && (
        <p className="mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-app-muted">
          <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          {historyText}
        </p>
      )}
    </section>
  );
};

const GroupEvidence: React.FC<{ group: ProjectionGroupSummary }> = ({ group }) => {
  if (!group.available) {
    return (
      <span
        className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        title={group.reason ?? 'El grupo no tiene una proyección publicable.'}
      >
        sin proyección
      </span>
    );
  }
  if (group.has_gaps) {
    return (
      <span
        className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        title="La serie del grupo tiene periodos faltantes."
      >
        serie con huecos
      </span>
    );
  }
  if (group.total?.extrapolated || (group.total && !group.total.reliable)) {
    return (
      <span className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
        {group.total?.extrapolated ? 'medición parcial' : 'poca evidencia'}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-md border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
      medida
    </span>
  );
};

const BreakdownCard: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const breakdown = result.breakdown;
  if (!breakdown) return null;

  if (!breakdown.available) {
    return (
      <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
        <h3 className="text-sm font-bold text-app-text">Desglose por dimensión</h3>
        <p className="mt-2 flex items-start gap-1.5 text-xs leading-relaxed text-app-muted">
          <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          {breakdown.reason || 'No se pudo desglosar esta proyección.'}
        </p>
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-app-border bg-app-card shadow-xs">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-app-border/80 px-4 py-3">
        <h3 className="text-sm font-bold text-app-text">
          Desglose por {breakdown.dimension_column ?? 'dimensión'}
        </h3>
        <span className="text-[11px] text-app-muted">
          Top {breakdown.top_n} por volumen histórico
        </span>
      </header>

      {breakdown.groups.length === 0 ? (
        <p className="px-4 py-4 text-xs text-app-muted">
          No hay grupos con historia suficiente para desglosar.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <caption className="sr-only">
              Total proyectado por grupo, con su evidencia y sus periodos históricos
            </caption>
            <thead>
              <tr className="border-b border-app-border text-left text-app-muted">
                <th scope="col" className="px-4 py-2 font-medium">Grupo</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Total proyectado</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Rango</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">MAPE</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Periodos</th>
                <th scope="col" className="px-4 py-2 font-medium">Evidencia</th>
              </tr>
            </thead>
            <tbody>
              {breakdown.groups.map((group) => {
                const total = group.total;
                const rango =
                  total && total.lower !== null && total.upper !== null
                    ? `${money(total.lower)} – ${money(total.upper)}`
                    : '—';
                return (
                  <tr key={group.value} className="border-b border-app-border/50 last:border-0">
                    <td className="px-4 py-2 font-medium text-app-text">{group.value}</td>
                    {group.available ? (
                      <>
                        <td className="px-4 py-2 text-right font-semibold tabular-nums text-app-text">
                          {money(total?.point)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-app-text">{rango}</td>
                        <td className="px-4 py-2 text-right tabular-nums text-app-text">
                          {pct(group.mape)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-app-muted">
                          {group.n_periods}
                        </td>
                        <td className="px-4 py-2">
                          <GroupEvidence group={group} />
                        </td>
                      </>
                    ) : (
                      <td colSpan={5} className="px-4 py-2 text-xs text-app-muted">
                        {group.reason || 'Sin proyección publicable para este grupo.'}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="border-t border-app-border/60 px-4 py-2.5 text-[11px] leading-relaxed text-app-muted">
        Cada grupo se proyecta con su propia serie histórica. El top se eligió por volumen
        observado, no por el resultado del modelo; no incluye una categoría «Otros» y no
        reemplaza el total general.
      </p>
    </section>
  );
};

const StepsTable: React.FC<{ result: ProjectionResult }> = ({ result }) => (
  <section className="overflow-hidden rounded-2xl border border-app-border bg-app-card shadow-xs">
    <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-app-border/80 px-4 py-3">
      <h3 className="text-sm font-bold text-app-text">Error y evidencia por paso</h3>
      <span className="text-[11px] text-app-muted">
        Horizonte publicado: {result.horizon} {result.horizon === 1 ? 'paso' : 'pasos'}
        {horizonSourceLabel(result.horizon_source)
          ? ` · ${horizonSourceLabel(result.horizon_source)}`
          : ''}
      </span>
    </header>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-xs">
        <caption className="sr-only">
          Punto, banda, error y evidencia de cada paso proyectado
        </caption>
        <thead>
          <tr className="border-b border-app-border text-left text-app-muted">
            <th scope="col" className="px-4 py-2 font-medium">#</th>
            <th scope="col" className="px-4 py-2 font-medium">Periodo</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Punto</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Banda</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">MAPE</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Backtests</th>
            <th scope="col" className="px-4 py-2 font-medium">Evidencia</th>
          </tr>
        </thead>
        <tbody>
          {result.steps.map((s) => (
            <tr key={s.step} className="border-b border-app-border/50 last:border-0">
              <td className="px-4 py-2 tabular-nums text-app-muted">{s.step}</td>
              <td className="px-4 py-2 tabular-nums text-app-text">{s.period ?? '—'}</td>
              <td className="px-4 py-2 text-right font-semibold tabular-nums text-app-text">
                {money(s.point)}
              </td>
              <td className="px-4 py-2 text-right tabular-nums text-app-text">
                {s.lower === null || s.upper === null ? (
                  <span className="text-app-muted">—</span>
                ) : (
                  <>
                    <span className="block">
                      {money(s.lower)} – {money(s.upper)}
                    </span>
                    <span className="block text-[10px] text-app-muted">
                      {s.band_pct === null ? '±—' : `±${pct(s.band_pct)}`}
                    </span>
                  </>
                )}
              </td>
              <td className="px-4 py-2 text-right tabular-nums text-app-text">{pct(s.mape)}</td>
              <td className="px-4 py-2 text-right tabular-nums text-app-muted">{s.n_backtests}</td>
              <td className="px-4 py-2">
                <EvidenceChip step={s} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    <p className="border-t border-app-border/60 px-4 py-2.5 text-[11px] leading-relaxed text-app-muted">
      «Sin backtest propio»: el paso no llegó a medirse y hereda la banda del último paso
      medido (el más cercano, no el más ancho). «Poca evidencia»: menos de 4 backtests. El
      MAPE se calcula por replay real sobre esa serie; «—» significa que ese paso no dejó
      ningún residuo que medir.
      {result.steps.some((step) => step.lower_clamped) &&
        ' En los conteos, la banda inferior se acota a 0: no hay cantidades negativas.'}
    </p>
  </section>
);

const MethodCard: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const beatsNaive =
    result.beats_naive === true ? (
      <span className="inline-flex items-center rounded-md border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
        Le ganó al naive
      </span>
    ) : result.beats_naive === false ? (
      <span
        className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        title="El backtest walk-forward no encontró ningún método estrictamente mejor que el naive para esta serie."
      >
        No le ganó al naive
      </span>
    ) : (
      <span className="text-app-muted">—</span>
    );

  const candidates = Object.entries(result.mape_by_method ?? {});

  return (
    <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
      <h3 className="text-sm font-bold text-app-text">Método y evidencia</h3>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">
        <div>
          <dt className="text-app-muted">Método publicado</dt>
          <dd className="mt-0.5 text-sm font-semibold text-app-text">{methodLabel(result.method)}</dd>
        </div>
        <div>
          <dt className="text-app-muted">¿Le ganó al naive?</dt>
          <dd className="mt-0.5">{beatsNaive}</dd>
        </div>
        <div>
          <dt className="text-app-muted">Periodos históricos</dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-app-text">{result.n_periods}</dd>
        </div>
        <div>
          <dt className="text-app-muted">Tabla</dt>
          <dd className="mt-0.5 text-sm font-semibold text-app-text">{result.table ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-app-muted">Origen de tabla</dt>
          <dd className="mt-0.5 text-sm font-semibold text-app-text">
            {tableSourceLabel(result.table_source)}
          </dd>
        </div>
        <div>
          <dt className="text-app-muted">Granularidad</dt>
          <dd className="mt-0.5 text-sm font-semibold text-app-text">
            {granularityLabel(result.granularity)}
          </dd>
        </div>
        <div>
          <dt className="text-app-muted">MAPE global</dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-app-text">{pct(result.mape)}</dd>
        </div>
      </dl>

      {candidates.length > 0 && (
        <p className="mt-3 text-[11px] leading-relaxed text-app-muted">
          MAPE por candidato en el walk-forward:{' '}
          {candidates.map(([m, v], i) => (
            <span key={m}>
              {i > 0 && ' · '}
              {methodLabel(m)} {pct(v)}
            </span>
          ))}
          . Gana el menor; el desempate favorece al más simple.
        </p>
      )}

      <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-app-muted">
        <span>
          Serie: {result.income_only ? 'solo ingresos (montos positivos por periodo)' : 'montos netos (incluye valores negativos)'}.
        </span>
        <span>
          {result.analysis_source === 'manual'
            ? 'Métrica y eje elegidos manualmente por el usuario.'
            : result.analysis_source === 'llm'
              ? 'Métrica y eje elegidos por la IA local.'
              : result.analysis_source === 'heuristica'
                ? 'Métrica y eje elegidos por heurística: el modelo local no respondió.'
                : 'Métrica y eje elegidos en el servidor.'}
        </span>
      </p>

      {result.has_gaps && (
        <p className="mt-3 flex items-start gap-1.5 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          La serie tiene periodos faltantes: el error medido puede no representar el
          comportamiento completo y la proyección pierde precisión.
        </p>
      )}
      <ExclusionNotes result={result} />
    </section>
  );
};

/**
 * Lectura del resultado en primer plano.
 *
 * El modelo local devuelve dos o tres parrafos largos; publicarlos enteros al
 * final de la pagina los volvia un muro de texto que nadie lee. Se muestra el
 * primer parrafo (la conclusion) y el resto queda a un clic, con la calidad y
 * el origen de la redaccion a la vista.
 */
const NarrationCard: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();
  const narration = result.narration?.trim();
  if (!narration) return null;

  const paragraphs = narration
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
  const hasMore = paragraphs.length > 1;
  const visible = expanded ? paragraphs : paragraphs.slice(0, 1);

  return (
    <section className="rounded-2xl border border-violet-500/30 bg-gradient-to-br from-violet-500/10 via-app-card to-app-card p-4 sm:p-5 shadow-xs">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-bold text-app-text">
          <Sparkles className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" aria-hidden />
          Lectura del resultado
        </h3>
        <div className="flex flex-wrap items-center gap-1.5">
          <QualityBadge result={result} compact />
          <span className="inline-flex items-center rounded-md border border-app-border bg-app-card px-2 py-0.5 text-[10px] font-medium text-app-muted">
            {result.narration_source === 'llm' ? 'IA local' : 'plantilla'}
          </span>
        </div>
      </header>

      <div id={panelId} className="mt-2.5 max-w-3xl space-y-3">
        {visible.map((paragraph, index) => (
          <p
            key={index}
            className={
              index === 0
                ? 'whitespace-pre-line text-[15px] leading-relaxed text-app-text'
                : 'whitespace-pre-line text-sm leading-relaxed text-app-text/90'
            }
          >
            {paragraph}
          </p>
        ))}
      </div>

      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={panelId}
          className="mt-2.5 inline-flex items-center gap-1 rounded-lg border border-violet-500/30 bg-violet-500/10 px-2.5 py-1 text-[11px] font-semibold text-violet-700 transition-colors hover:bg-violet-500/20 dark:text-violet-300"
        >
          <span>
            {expanded
              ? 'Mostrar menos'
              : `Leer análisis completo (${paragraphs.length - 1} párrafo${paragraphs.length - 1 === 1 ? '' : 's'} más)`}
          </span>
          <ChevronDown
            className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
            aria-hidden
          />
        </button>
      )}

      <p className="mt-2.5 flex items-start gap-1.5 text-[11px] text-app-muted">
        <Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
        {result.narration_source === 'llm'
          ? 'Redactada por el modelo local a partir de los números ya calculados; no calcula nada por su cuenta.'
          : 'Plantilla determinista del servidor: el modelo local no respondió, y la lectura se publica igual.'}
      </p>
    </section>
  );
};

/** Leyenda compacta: tres muestras visuales en vez de un parrafo explicativo. */
const ChartLegend: React.FC = () => (
  <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-app-muted">
    <li className="flex items-center gap-1.5">
      <span aria-hidden className="inline-block h-0.5 w-5 rounded bg-[#94A3B8]" />
      Histórico real
    </li>
    <li className="flex items-center gap-1.5">
      <span aria-hidden className="inline-block w-5 border-t-2 border-dashed border-[#7C3AED]" />
      Proyección
    </li>
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block h-2.5 w-5 rounded-sm border border-[#7C3AED]/40 bg-[rgba(124,58,237,0.16)]"
      />
      banda de error (backtest)
    </li>
  </ul>
);

export const ProjectionResults: React.FC<{ result: ProjectionResult }> = ({ result }) => {
  // El scope de la captura del grafico: el contenedor de ESTE chart, para que
  // el PDF no incruste una figura ajena si la pagina llegara a tener mas de una.
  const chartScopeRef = useRef<HTMLDivElement>(null);

  if (!result.available) {
    return <UnavailableBlock result={result} />;
  }

  return (
    <div className="animate-slideUp space-y-5">
      <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-bold text-app-text">
              <TrendingUp className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-400" aria-hidden />
              Proyección publicada
            </h2>
            <p className="mt-0.5 text-xs text-app-muted">
              {result.table ?? '—'} · {result.metric_column ?? '—'} · eje {result.date_column ?? '—'}
            </p>
            {result.question && (
              <p className="mt-1 truncate text-xs italic text-app-muted" title={result.question}>
                «{result.question}»
              </p>
            )}
          </div>
          <ExportToolbar result={result} chartScopeRef={chartScopeRef} />
        </header>
      </section>

      {/* La lectura va primero: es la conclusion, no un anexo al final. */}
      <NarrationCard result={result} />

      <TotalCard result={result} />

      <section className="rounded-2xl border border-app-border bg-app-card p-4 shadow-xs">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-bold text-app-text">Serie y proyección</h3>
          <span className="text-[11px] text-app-muted">
            {result.horizon} {result.horizon === 1 ? 'paso publicado' : 'pasos publicados'}
          </span>
        </header>
        <div className="mt-2" ref={chartScopeRef}>
          <ProjectionChart result={result} />
        </div>
        <ChartLegend />
      </section>

      <BreakdownCard result={result} />
      <StepsTable result={result} />
      <MethodCard result={result} />
    </div>
  );
};
