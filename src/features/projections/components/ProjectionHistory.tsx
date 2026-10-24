import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  History,
  Loader2,
  ShieldCheck,
} from 'lucide-react';
import {
  projectionService,
  type ProjectionHistoryItem,
  type ProjectionVerification,
} from '../services/projection_service';
import { getApiErrorMessage } from '../../../shared/api/api_client';

/**
 * Historial de proyecciones del usuario y verificacion contra el dato actual.
 *
 * La verificacion no modifica la proyeccion auditada: solo compara pasos de
 * periodos ya cerrados. Un periodo futuro se muestra "pendiente", no como un
 * error; un periodo sin dato se muestra "sin dato", no como cero.
 */

const money = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `$${Math.round(n).toLocaleString('es-CL', { maximumFractionDigits: 0 })}`;

const pct = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `${Number(n).toLocaleString('es-CL', { maximumFractionDigits: 1 })}%`;

const dateLabel = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('es-CL');
};

const STATUS_LABEL: Record<string, string> = {
  medido: 'Medido',
  pendiente: 'Pendiente',
  sin_dato: 'Sin dato',
  sin_periodo: 'Sin periodo',
  incompleto: 'Incompleto',
};

const BandLabel: React.FC<{ inside: boolean | null | undefined }> = ({ inside }) => {
  if (inside === null || inside === undefined) {
    return <span className="text-app-muted">—</span>;
  }
  return inside ? (
    <span className="inline-flex items-center rounded-md border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">
      Dentro
    </span>
  ) : (
    <span className="inline-flex items-center rounded-md border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
      Fuera
    </span>
  );
};

const VerificationCard: React.FC<{ verification: ProjectionVerification }> = ({
  verification,
}) => {
  if (!verification.available) {
    return (
      <section
        role="status"
        className="rounded-xl border border-amber-300 bg-amber-50 p-3 dark:border-amber-800/60 dark:bg-amber-950/40"
      >
        <h4 className="flex items-center gap-2 text-xs font-bold text-amber-900 dark:text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          Verificación no disponible
        </h4>
        <p className="mt-1.5 text-xs leading-relaxed text-amber-900 dark:text-amber-100">
          {verification.reason || 'No se pudo comparar esta proyección con el dato actual.'}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-app-border bg-app-card p-3 shadow-xs">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="flex items-center gap-2 text-xs font-bold text-app-text">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
            Verificación contra datos actuales
          </h4>
          <p className="mt-0.5 text-[11px] leading-relaxed text-app-muted">
            {verification.measured_steps} medidos · {verification.pending_steps} pendientes ·{' '}
            {verification.missing_steps} sin dato · {verification.inside_band_steps} dentro de
            la banda · {verification.outside_band_steps} fuera
          </p>
        </div>
      </header>

      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[620px] text-[11px]">
          <caption className="sr-only">
            Comparación por paso entre lo proyectado y el dato actual
          </caption>
          <thead>
            <tr className="border-b border-app-border text-left text-app-muted">
              <th scope="col" className="py-1.5 pr-2 font-medium">#</th>
              <th scope="col" className="py-1.5 pr-2 font-medium">Periodo</th>
              <th scope="col" className="py-1.5 pr-2 text-right font-medium">Proyectado</th>
              <th scope="col" className="py-1.5 pr-2 text-right font-medium">Actual</th>
              <th scope="col" className="py-1.5 pr-2 text-right font-medium">Error</th>
              <th scope="col" className="py-1.5 pr-2 font-medium">Banda</th>
              <th scope="col" className="py-1.5 font-medium">Estado</th>
            </tr>
          </thead>
          <tbody>
            {verification.steps.map((step) => (
              <tr key={step.step} className="border-b border-app-border/50 last:border-0">
                <td className="py-1.5 pr-2 tabular-nums text-app-muted">{step.step}</td>
                <td className="py-1.5 pr-2 tabular-nums text-app-text">{step.period ?? '—'}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {money(step.projected)}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {money(step.actual)}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {pct(step.error_pct)}
                </td>
                <td className="py-1.5 pr-2">
                  <BandLabel inside={step.inside_band} />
                </td>
                <td className="py-1.5 text-app-muted">
                  {STATUS_LABEL[step.status] ?? step.status}
                </td>
              </tr>
            ))}
            {verification.total && (
              <tr className="font-semibold">
                <td className="py-1.5 pr-2 text-app-muted" colSpan={2}>Total</td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {money(verification.total.projected)}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {money(verification.total.actual)}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-app-text">
                  {pct(verification.total.error_pct)}
                </td>
                <td className="py-1.5 pr-2">
                  <BandLabel inside={verification.total.inside_band} />
                </td>
                <td className="py-1.5 text-app-muted">
                  {STATUS_LABEL[verification.total.status] ?? verification.total.status}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {verification.data_note && (
        <p className="mt-2 text-[10px] leading-relaxed text-app-muted">{verification.data_note}</p>
      )}
    </section>
  );
};

export const ProjectionHistory: React.FC<{ refreshKey?: number | null }> = ({
  refreshKey,
}) => {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ProjectionHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verification, setVerification] = useState<ProjectionVerification | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [verifyingId, setVerifyingId] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await projectionService.history(10);
      setItems(data.items ?? []);
    } catch (err: any) {
      setError(getApiErrorMessage(err, 'No se pudo cargar el historial.', true));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
  }, [open, refreshKey]);

  const verify = async (item: ProjectionHistoryItem) => {
    if (!item.verifiable || verifyingId !== null) return;
    setVerifyingId(item.audit_log_id);
    setVerification(null);
    setVerificationError(null);
    try {
      const data = await projectionService.verify(item.audit_log_id);
      setVerification(data);
    } catch (err: any) {
      setVerificationError(
        getApiErrorMessage(err, 'No se pudo verificar esta proyección.', true)
      );
    } finally {
      setVerifyingId(null);
    }
  };

  return (
    <section className="rounded-2xl border border-app-border bg-app-card shadow-xs">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="projection-history-panel"
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-bold text-app-text">
          <History className="h-4 w-4 shrink-0 text-app-muted" aria-hidden />
          Proyecciones recientes
        </span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-app-muted transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>

      {open && (
        <div id="projection-history-panel" className="space-y-3 border-t border-app-border/80 px-4 py-3">
          {loading && (
            <p className="flex items-center gap-2 text-xs text-app-muted">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Cargando historial…
            </p>
          )}

          {error && (
            <p role="alert" className="text-xs leading-relaxed text-rose-700 dark:text-rose-300">
              {error}
            </p>
          )}

          {!loading && !error && items.length === 0 && (
            <p className="text-xs text-app-muted">Todavía no hay proyecciones guardadas.</p>
          )}

          {items.map((item) => (
            <article
              key={item.audit_log_id}
              className="rounded-xl border border-app-border bg-app-surface/50 p-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-app-text" title={item.question}>
                    {item.question}
                  </p>
                  <p className="mt-0.5 text-[11px] text-app-muted">
                    {dateLabel(item.timestamp)} · {item.horizon} pasos · {item.table ?? '—'} ·{' '}
                    {item.metric_column ?? '—'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold tabular-nums text-app-text">
                    {money(item.total_point)}
                  </span>
                  <button
                    type="button"
                    onClick={() => void verify(item)}
                    disabled={!item.verifiable || verifyingId !== null}
                    aria-label={`Verificar proyección ${item.audit_log_id}`}
                    className="rounded-lg border border-app-border bg-app-card px-2 py-1 text-[11px] font-semibold text-app-text transition-colors hover:border-violet-500/50 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {verifyingId === item.audit_log_id ? 'Verificando…' : 'Verificar'}
                  </button>
                </div>
              </div>
              {!item.verifiable && (
                <p className="mt-1.5 text-[11px] leading-relaxed text-app-muted">
                  {item.reason || 'Esta proyección no se puede verificar.'}
                </p>
              )}
            </article>
          ))}

          {verificationError && (
            <p role="alert" className="text-xs leading-relaxed text-rose-700 dark:text-rose-300">
              {verificationError}
            </p>
          )}

          {verification && <VerificationCard verification={verification} />}
        </div>
      )}
    </section>
  );
};
