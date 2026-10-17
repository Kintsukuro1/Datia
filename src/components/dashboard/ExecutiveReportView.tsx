import React, { useState } from 'react';
import { QueryResult } from '../../types';
import {
  FileText,
  AlertTriangle,
  ShieldCheck,
} from 'lucide-react';
import { ReportExportToolbar } from './report/ReportExportToolbar';
import { ReportFindingsView, ReportSectionHeading } from './report/ReportFindingsView';
import { copyToClipboard } from '../../shared/clipboard';

interface ExecutiveReportViewProps {
  result: QueryResult;
  copiedReport?: boolean;
  onCopyReport?: () => void;
  onOpenTraceability?: () => void;
}

// Badge de riesgo: colores por severidad real del backend (BAJO/MEDIO/ALTO/CRITICO).
const RISK_BADGE_CLASSES: Record<string, string> = {
  BAJO: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30',
  MEDIO: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30',
  ALTO: 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30',
  CRITICO: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/40',
};

export const ExecutiveReportView: React.FC<ExecutiveReportViewProps> = ({
  result,
  copiedReport: externalCopied,
  onCopyReport: externalCopyHandler,
  onOpenTraceability,
}) => {
  const [exportError, setExportError] = useState<string | null>(null);
  const [internalCopied, setInternalCopied] = useState(false);

  const copiedReport = externalCopied ?? internalCopied;

  const onCopyReport = externalCopyHandler || (() => {
    const textToCopy = result.executive_report?.overview || result.summary_text || '';
    void copyToClipboard(textToCopy).then((ok: boolean) => {
      if (!ok) return;
      setInternalCopied(true);
      setTimeout(() => setInternalCopied(false), 2000);
    });
  });

  const trace = result.traceability;
  const rowsReturned = trace?.rows_returned ?? result.data_rows?.length ?? 0;
  const riskLevel = result.executive_report?.risk_level;

  return (
    <div className="executive-report-view bg-app-surface border border-app-border rounded-2xl shadow-sm animate-fadeIn overflow-hidden">
      {/* Linea de acento superior */}
      <div aria-hidden="true" className="h-0.5 w-full bg-gradient-to-r from-transparent via-brand-500/70 to-transparent" />

      <div className="p-5 sm:p-7 space-y-6">
        {/* Portada y controles de exportacion */}
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
            <div className="space-y-2 min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-app-card border border-app-border text-app-muted text-[10px] font-semibold uppercase tracking-wider">
                  <FileText className="w-3.5 h-3.5 text-brand-500" />
                  Informe ejecutivo
                </span>
                {riskLevel && (
                  <span className={`text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border ${RISK_BADGE_CLASSES[riskLevel] || RISK_BADGE_CLASSES.BAJO}`}>
                    Nivel de riesgo: {riskLevel}
                  </span>
                )}
                {trace?.audit_log_id != null && (
                  <span className="text-[10px] font-mono px-2.5 py-1 rounded-full bg-app-card border border-app-border text-app-muted">
                    Auditoría #{trace.audit_log_id}
                  </span>
                )}
              </div>
              <h3 className="text-lg sm:text-xl font-bold text-app-text tracking-tight">
                Informe Ejecutivo de Negocio
              </h3>
              <p className="text-xs text-app-muted">
                Generado automáticamente a partir de la consulta "{result.question}" sobre la base de datos activa.
              </p>
            </div>

            <ReportExportToolbar
              result={result}
              copiedReport={copiedReport}
              onCopyReport={onCopyReport}
              onExportError={setExportError}
            />
          </div>

          {exportError && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/25 text-rose-700 dark:text-rose-300 text-xs flex items-center gap-2 animate-fadeIn">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{exportError}</span>
            </div>
          )}

          {/* Franja de metadatos: solo campos que el backend reporta. */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 bg-app-card border border-app-border rounded-xl p-4">
            <div className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Emitido</span>
              <span className="block mt-0.5 text-xs font-semibold text-app-text truncate">{result.timestamp || '—'}</span>
            </div>
            <div className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Base de datos</span>
              <span className="block mt-0.5 text-xs font-semibold text-app-text truncate font-mono">{trace?.target_database || '—'}</span>
            </div>
            <div className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Validación SQL</span>
              <span className="block mt-0.5 text-xs font-semibold text-app-text truncate">{trace?.validation_status || 'Sin registro'}</span>
            </div>
            <div className="min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Filas devueltas</span>
              <span className="block mt-0.5 text-xs font-semibold text-app-text font-mono tabular-nums">{rowsReturned}</span>
            </div>
          </div>
        </div>

        {/* Secciones 01-03: diagnostico, hallazgos y recomendaciones */}
        <ReportFindingsView result={result} />

        {/* Seccion 04: trazabilidad y auditoria (datos reales de traceability) */}
        <div className="space-y-3">
          <ReportSectionHeading number="04" title="Trazabilidad y auditoría" />
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <div className="p-3.5 rounded-xl bg-app-card border border-app-border min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Latencia de ejecución</span>
              <span className="block mt-1 text-lg font-bold text-app-text font-mono tabular-nums">
                {trace?.execution_time_ms != null ? `${trace.execution_time_ms} ms` : '—'}
              </span>
            </div>
            <div className="p-3.5 rounded-xl bg-app-card border border-app-border min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Tablas del esquema usadas</span>
              <span className="block mt-1 text-lg font-bold text-app-text font-mono tabular-nums">
                {trace?.schema_tables_used?.length ?? 0}
              </span>
            </div>
            <div className="p-3.5 rounded-xl bg-app-card border border-app-border min-w-0">
              <span className="block text-[10px] uppercase tracking-wider text-app-muted">Registro de auditoría</span>
              <span className="block mt-1 text-lg font-bold text-app-text font-mono tabular-nums">
                {trace?.audit_log_id != null ? `#${trace.audit_log_id}` : '—'}
              </span>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-app-muted">
            <span>
              Estado de validación: <strong className="text-app-text">{trace?.validation_status || 'Sin registro'}</strong>
            </span>
            {onOpenTraceability && (
              <button
                type="button"
                onClick={onOpenTraceability}
                className="flex items-center gap-1.5 text-brand-700 dark:text-brand-300 hover:text-brand-800 dark:hover:text-brand-200 font-medium transition-colors cursor-pointer"
              >
                <ShieldCheck className="w-4 h-4" />
                <span>Auditar código SQL completo</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
