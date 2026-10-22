import React from 'react';
import { Bot, Loader2 } from 'lucide-react';
import type { EnrichEstimateResponse } from '../../../features/admin/services/catalog_service';
import { GENERATION_PHASES, formatDuration } from './wizardConfig';

interface GeneratingStepProps {
  estimate: EnrichEstimateResponse | null;
  generationPhaseIndex: number;
  generationElapsed: number;
  generationPct: number;
}

export const GeneratingStep: React.FC<GeneratingStepProps> = ({
  estimate,
  generationPhaseIndex,
  generationElapsed,
  generationPct,
}) => {
  return (
    <div className="py-10 px-4 text-center space-y-6 animate-fadeIn max-w-lg mx-auto">
      <div className="relative mx-auto w-16 h-16">
        <div className="absolute inset-0 rounded-2xl bg-brand-600/20 animate-ping"></div>
        <div className="relative w-16 h-16 rounded-2xl bg-brand-600 flex items-center justify-center text-white shadow-xl shadow-brand-600/30">
          <Bot className="w-8 h-8 animate-pulse" />
        </div>
      </div>

      <div className="space-y-1.5">
        <h4 className="text-base font-bold text-app-text">
          Generando Diccionario de Datos con IA
        </h4>
        <p className="text-xs text-app-muted">
          {estimate
            ? `El modelo local está redactando descripciones de negocio para ${estimate.total_columns} columna${estimate.total_columns === 1 ? '' : 's'} de ${estimate.total_tables} tabla${estimate.total_tables === 1 ? '' : 's'}.`
            : 'El modelo local está redactando descripciones de negocio y fórmulas para cada campo.'}
        </p>
      </div>

      {/* Progress Bar: avance real = tiempo transcurrido contra la
          estimación medida del modelo. El 100% solo llega con la
          respuesta del backend; por eso el tope visual es 96%. */}
      <div className="space-y-2">
        <div className="w-full bg-app-bg rounded-full h-2.5 overflow-hidden border border-app-border">
          <div
            className="bg-brand-600 h-2.5 rounded-full transition-all duration-700 ease-out"
            style={{ width: `${generationPct}%` }}
          ></div>
        </div>
        <div className="flex justify-between text-[11px] text-app-muted font-mono">
          <span>Fase {generationPhaseIndex + 1} de 4 · {generationElapsed} s</span>
          <span className="text-brand-600 dark:text-brand-400 font-semibold">{generationPct}%</span>
        </div>
        <p className="text-[11px] text-app-muted leading-relaxed">
          {estimate
            ? `Estimado: ${formatDuration(estimate.min_seconds)} a ${formatDuration(estimate.max_seconds)} (${estimate.tokens_per_second} tok/s medidos del modelo). Podés revisar y ajustar todo al terminar.`
            : 'Con modelos locales esto puede tardar de uno a tres minutos. Podés revisar y ajustar todo al terminar.'}
        </p>
        {estimate?.beyond_budget && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400 leading-relaxed">
            Supera el límite de {Math.round(estimate.budget_seconds / 60)} min por corrida:
            se describirá lo que entre en ese tiempo y el resto queda heurístico para revisión.
          </p>
        )}
      </div>

      {/* Active Step Indicator */}
      <div className="p-3.5 rounded-xl bg-app-card border border-brand-500/30 text-xs text-app-text flex items-center justify-center space-x-2.5">
        <Loader2 className="w-4 h-4 animate-spin text-brand-600 dark:text-brand-400 shrink-0" />
        <span>{GENERATION_PHASES[generationPhaseIndex]}</span>
      </div>
    </div>
  );
};
