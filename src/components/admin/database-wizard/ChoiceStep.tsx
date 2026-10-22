import { AlertTriangle, Bot, Clock, Database, Loader2, ShieldCheck, UserCheck, WifiOff } from 'lucide-react';
import type { CorporateConnection } from '../../../features/admin/services/connector_service';
import type { EnrichEstimateResponse } from '../../../features/admin/services/catalog_service';
import { formatDuration, type CreationMode, type LlmStatus } from './wizardConfig';

interface ChoiceStepProps {
  connection: CorporateConnection;
  creationMode: CreationMode;
  onCreationModeChange: (mode: CreationMode) => void;
  llmStatus: LlmStatus;
  llmMessage: string | null;
  isEstimating: boolean;
  estimate: EnrichEstimateResponse | null;
  onRetryLlm: () => void;
}

export const ChoiceStep: React.FC<ChoiceStepProps> = ({
  connection,
  creationMode,
  onCreationModeChange,
  llmStatus,
  llmMessage,
  isEstimating,
  estimate,
  onRetryLlm,
}) => {
  // Sin LLM verificado (chequeando o caído) el modo IA no es cliqueable.
  const aiDisabled = llmStatus !== 'available';

  return (
    <div className="space-y-5 animate-fadeIn">
      {/* Step header */}
      <div className="flex flex-col gap-1 pb-3 border-b border-app-border">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
            Paso 2 de 5
          </span>
          <span className="text-[11px] text-app-muted">Fuente registrada</span>
        </div>
        <h4 className="text-sm font-bold text-app-text">Evaluación de la fuente y modo de creación</h4>
        <p className="text-xs text-app-muted leading-relaxed max-w-lg">
          Verificá el resumen de la conexión y elegí cómo construir el diccionario y catálogo de datos.
        </p>
      </div>

      {/* Evaluated summary card */}
      <div className="p-4 rounded-2xl bg-app-card border border-app-border flex items-center justify-between gap-3">
        <div className="flex items-center space-x-3 min-w-0">
          <div className="p-2.5 rounded-xl bg-brand-500/10 text-brand-600 dark:text-brand-400 border border-brand-500/30 shrink-0">
            <Database className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-bold text-app-text truncate">{connection.name}</h4>
            <p className="text-xs text-app-muted">
              Motor: <span className="font-mono text-brand-600 dark:text-brand-400 uppercase">{connection.db_type}</span> • Estado: <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Evaluada y Conectada</span>
            </p>
          </div>
        </div>
        <div className="text-right text-xs shrink-0">
          <span className="px-2.5 py-1 rounded-lg bg-app-bg border border-app-border text-app-muted font-mono">
            ID #{connection.id}
          </span>
        </div>
      </div>

      <div>
        <h4 className="text-sm font-bold text-app-text">¿Cómo deseas estructurar el Diccionario y Catálogo de Datos?</h4>
        <p className="text-xs text-app-muted mt-0.5">
          Elige si deseas que la Inteligencia Artificial analice y describa las columnas, o hacerlo manualmente:
        </p>
      </div>

      {/* Mode Selection Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Option 1: AI */}
        <div
          role="button"
          tabIndex={aiDisabled ? -1 : 0}
          aria-disabled={aiDisabled}
          onClick={() => { if (!aiDisabled) onCreationModeChange('ai'); }}
          onKeyDown={(e) => { if (!aiDisabled && (e.key === 'Enter' || e.key === ' ')) onCreationModeChange('ai'); }}
          className={`p-4 rounded-2xl border transition-all text-left space-y-3 relative ${
            aiDisabled
              ? 'border-app-border bg-app-card/40 opacity-60 cursor-not-allowed'
              : creationMode === 'ai'
                ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500 cursor-pointer'
                : 'border-app-border bg-app-card hover:border-brand-500/40 cursor-pointer'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
              <Bot className="w-5 h-5" />
            </div>
            {llmStatus === 'available' ? (
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                Recomendado
              </span>
            ) : llmStatus === 'checking' ? (
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-app-bg text-app-muted border border-app-border">
                Verificando…
              </span>
            ) : (
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-700 dark:text-rose-300 border border-rose-500/30">
                No Disponible
              </span>
            )}
          </div>
          <div>
            <h5 className="text-sm font-bold text-app-text flex items-center gap-1.5">
              <span>Generar con Inteligencia Artificial</span>
            </h5>
            <p className="text-xs text-app-muted mt-1">
              La IA examina la estructura, tipos y valores muestrales para formular descripciones semánticas, nombres de negocio y fórmulas automáticamente.
            </p>
            {llmStatus === 'available' && isEstimating && (
              <div className="mt-2 flex items-center gap-1.5 text-[10px] text-app-muted">
                <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                <span>Midiendo velocidad del modelo para estimar la duración…</span>
              </div>
            )}
            {llmStatus === 'available' && !isEstimating && estimate && (
              <div className="mt-2 space-y-1 text-[10px]">
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-brand-600 dark:text-brand-400">
                  <Clock className="w-3.5 h-3.5 shrink-0" />
                  <span>
                    Duración estimada: ~{formatDuration(estimate.min_seconds)} a {formatDuration(estimate.max_seconds)} (
                    {estimate.total_tables} tabla{estimate.total_tables === 1 ? '' : 's'} ·{' '}
                    {estimate.total_columns} columna{estimate.total_columns === 1 ? '' : 's'} ·{' '}
                    {estimate.tokens_per_second} tok/s medidos)
                  </span>
                </div>
                {estimate.beyond_budget && (
                  <div className="text-amber-700 dark:text-amber-300">
                    Supera el límite de {Math.round(estimate.budget_seconds / 60)} min por corrida: las últimas
                    tablas quedarán con reglas automáticas.
                  </div>
                )}
              </div>
            )}
          </div>
          {llmStatus === 'unavailable' ? (
            /* Sin LLM: la razón del bloqueo, el mensaje del backend y un
               reintento en caliente (encender el modelo sin cerrar). */
            <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300 text-[11px] space-y-1">
              <div className="flex items-center space-x-1.5 font-bold">
                <WifiOff className="w-3.5 h-3.5 shrink-0" />
                <span>No hay LLM conectada</span>
              </div>
              <p className="text-[10px] leading-relaxed opacity-90">
                {llmMessage || 'No se pudo contactar al servidor LLM local. Verifica que llama.exe serve u Ollama esté activo.'}{' '}
                Puedes reconectar el modelo en Opciones o continuar con la Creación Manual.
              </p>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onRetryLlm(); }}
                className="text-[10px] font-semibold underline underline-offset-2 hover:opacity-80"
              >
                Verificar de nuevo
              </button>
            </div>
          ) : (
            /* Warning disclaimer inside AI card */
            <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-300 text-[11px] space-y-1">
              <div className="flex items-center space-x-1.5 font-bold">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                <span>Aviso de Precaución</span>
              </div>
              <p className="text-[10px] leading-relaxed opacity-90">
                La IA puede cometer imprecisiones en terminología o lógica de negocio. Se te permitirá auditar y corregir cada definición en el siguiente paso.
              </p>
            </div>
          )}
        </div>

        {/* Option 2: Manual */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => onCreationModeChange('manual')}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onCreationModeChange('manual'); }}
          className={`p-4 rounded-2xl border transition-all cursor-pointer text-left space-y-3 relative ${
            creationMode === 'manual'
              ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500'
              : 'border-app-border bg-app-card hover:border-brand-500/40'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-app-bg text-app-muted border border-app-border">
              <UserCheck className="w-5 h-5" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-app-bg text-app-muted border border-app-border">
              Manual
            </span>
          </div>
          <div>
            <h5 className="text-sm font-bold text-app-text">Creación Manual por el Usuario</h5>
            <p className="text-xs text-app-muted mt-1">
              Construye las descripciones y definiciones tú mismo sin inferencias de IA, manteniendo el control absoluto de cada término.
            </p>
          </div>
          <div className="p-2.5 rounded-xl bg-app-bg/80 border border-app-border text-app-muted text-[10px] space-y-1">
            <div className="flex items-center space-x-1.5 font-semibold text-app-text">
              <ShieldCheck className="w-3.5 h-3.5 text-brand-500 shrink-0" />
              <span>Control Total</span>
            </div>
            <p className="leading-relaxed">
              El asistente introspeccionará el esquema físico y te llevará directamente a la mesa de edición para documentar las tablas.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
