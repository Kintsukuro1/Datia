import React from 'react';
import { QueryResult } from '../../../types';
import { CheckCircle2, AlertTriangle } from 'lucide-react';

interface ReportFindingsViewProps {
  result: QueryResult;
}

// Encabezado de sección numerada del informe (01, 02, ...). Compartido con
// ExecutiveReportView para que la sección 04 use el mismo lenguaje.
export const ReportSectionHeading: React.FC<{ number: string; title: string }> = ({ number, title }) => (
  <div className="flex items-center gap-3">
    <span className="px-2 py-0.5 rounded-lg bg-brand-600 text-white font-bold text-[10px] tracking-widest shrink-0">{number}</span>
    <h4 className="text-xs font-bold text-app-text uppercase tracking-wider">{title}</h4>
    <div aria-hidden="true" className="h-px flex-1 bg-app-border/70" />
  </div>
);

export const ReportFindingsView: React.FC<ReportFindingsViewProps> = ({ result }) => {
  const rowsCount = result.data_rows?.length || 0;
  const colsCount = result.data_columns?.length || 0;

  // Build clean fallback findings if not supplied by backend
  const fallbackFindings = [
    `Se procesaron ${rowsCount} registros de la base de datos activa para esta consulta.`,
    `Estructura organizada en ${colsCount} columnas de información disponible.`,
    result.kpis && result.kpis.length > 0 && result.kpis[0]?.value
      ? `Indicador principal: ${result.kpis[0].title} = ${result.kpis[0].value}.`
      : `Consulta ejecutada sobre la fuente de datos activa.`,
  ];

  const hasRealFindings = !!result.executive_report?.key_findings?.length;

  const findings = hasRealFindings
    ? result.executive_report!.key_findings!
    : fallbackFindings;

  // Build clean fallback recommendations
  const fallbackRecommendations = [
    'Consultar la pestaña de Datos para inspeccionar los registros individuales en detalle.',
    'Aplicar filtros adicionales por periodo, categoría o dimensiones para segmentar la información.',
    'Cruzar estos resultados con métricas cuantitativas complementarias según sea necesario.',
  ];

  const recommendations =
    result.executive_report?.recommendations && result.executive_report.recommendations.length > 0
      ? result.executive_report.recommendations
      : fallbackRecommendations;

  return (
    <div className="space-y-6">
      {/* Sección 01: Diagnóstico & Contexto General */}
      <div className="space-y-3">
        <ReportSectionHeading number="01" title="Diagnóstico y contexto general" />

        <div className="bg-app-bg/50 border border-app-border rounded-xl p-5 text-sm text-app-text leading-relaxed whitespace-pre-line">
          {result.executive_report?.overview || result.summary_text}
        </div>

        {result.executive_report?.business_impact && (
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex items-start gap-3 text-xs text-amber-800 dark:text-amber-200">
            <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <div>
              <strong className="font-semibold">Impacto directo en el negocio: </strong>
              <span>{result.executive_report.business_impact}</span>
            </div>
          </div>
        )}
      </div>

      {/* Sección 02: Hallazgos Clave */}
      <div className="space-y-3">
        <ReportSectionHeading
          number="02"
          title={hasRealFindings ? 'Hallazgos clave y puntos críticos' : 'Resumen de la consulta'}
        />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {findings.map((finding, idx) => (
            <div
              key={idx}
              className="bg-app-card border border-app-border rounded-xl p-4 flex items-start gap-3 text-xs text-app-text hover:border-brand-500/40 transition-colors"
            >
              {/* A green check per card is a verdict. Only earned by real
                  backend findings; the fallback cards are query metadata. */}
              {hasRealFindings && <CheckCircle2 className="w-4 h-4 text-emerald-500 dark:text-emerald-400 shrink-0 mt-0.5" />}
              <span className="leading-relaxed">{finding}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Sección 03: Recomendaciones Estratégicas */}
      <div className="space-y-3">
        <ReportSectionHeading number="03" title="Recomendaciones estratégicas accionables" />
        <div className="space-y-2.5">
          {recommendations.map((rec, idx) => (
            <div
              key={idx}
              className="bg-app-card border border-app-border rounded-xl p-4 flex items-start gap-3 text-xs text-app-text hover:border-brand-500/40 transition-colors"
            >
              <div className="w-5 h-5 rounded-md bg-brand-600/15 text-brand-700 dark:text-brand-300 font-bold flex items-center justify-center text-[10px] shrink-0 mt-0.5 border border-brand-500/30">
                {idx + 1}
              </div>
              <span className="leading-relaxed">{rec}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
