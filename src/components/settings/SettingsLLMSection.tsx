import React from 'react';
import { Cpu, RefreshCw, CheckCircle2, AlertCircle, Radio, ShieldCheck } from 'lucide-react';
import { AppSettings } from '../../types';
import { LLMConnectionTestResult, LLMPolicy } from '../../features/chat/services/llm_service';
import { getProviderLabel } from '../../constants';

type LLMProvider = AppSettings['llm_provider'];

interface SettingsLLMSectionProps {
  provider: LLMProvider;
  ollamaUrl: string;
  modelName: string;
  detectedModels: string[];
  detectedEndpoint: { url: string; prov: LLMProvider } | null;
  testingLLM: boolean;
  llmTestResult: LLMConnectionTestResult | null;
  llmPolicy: LLMPolicy | null;
  llmPolicyError: string | null;
  onProviderChange: (prov: LLMProvider) => void;
  onOllamaUrlChange: (url: string) => void;
  onModelNameChange: (model: string) => void;
  onTestLLMConnection: () => void;
  onApplyDetected: () => void;
}

export const SettingsLLMSection: React.FC<SettingsLLMSectionProps> = ({
  provider,
  ollamaUrl,
  modelName,
  detectedModels,
  detectedEndpoint,
  testingLLM,
  llmTestResult,
  llmPolicy,
  llmPolicyError,
  onProviderChange,
  onOllamaUrlChange,
  onModelNameChange,
  onTestLLMConnection,
  onApplyDetected,
}) => {
  // central/external: la empresa administra el motor. El backend ignora la
  // URL/modelo del navegador; mostrar los campos editables haria creer que el
  // cambio se aplica. En `user` (o si la politica no cargo) se mantienen
  // editables como antes.
  const managed = !!llmPolicy && !llmPolicy.allow_user_llm;
  const managedProvider = (llmPolicy?.provider || 'custom') as LLMProvider;
  const shownProvider = managed ? managedProvider : provider;
  const shownModel = managed ? llmPolicy!.default_model : modelName;
  const shownUrl = managed ? '' : ollamaUrl;
  const knownProvider = ['llama_cpp', 'ollama', 'openai_compatible'].includes(String(shownProvider));

  const badge = managed
    ? { text: 'Motor de la empresa', cls: 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300 border-indigo-500/30' }
    : llmPolicy?.allow_user_llm
      ? { text: 'LLM propio habilitado', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30' }
      : { text: '100% Offline', cls: 'bg-brand-500/15 text-brand-700 dark:text-brand-300 border-brand-500/30' };

  const subtitle = managed
    ? 'El motor y el endpoint los define la empresa; la configuración del navegador no se usa.'
    : llmPolicy?.allow_user_llm
      ? 'Endpoint elegido por el usuario; el backend valida la URL y sigue auditando las consultas.'
      : 'Inferencia agnóstica sin exfiltración de datos a la nube';

  return (
    <div className="glass-panel rounded-2xl p-5 border border-white/10 space-y-4 shadow-xl font-sans">
      {/* Section Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-dark-border/80 pb-3.5">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-xl bg-gradient-to-br from-brand-600/20 to-indigo-600/20 text-brand-400 border border-brand-500/30 shadow-md">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-xs sm:text-sm font-bold text-gray-900 dark:text-white tracking-tight flex items-center gap-2">
              <span>Motor IA Local (Text-to-SQL)</span>
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${badge.cls}`}>
                {badge.text}
              </span>
            </h3>
            <p className="text-[11px] text-gray-600 dark:text-gray-400">{subtitle}</p>
          </div>
        </div>

        <button
          type="button"
          onClick={onTestLLMConnection}
          disabled={testingLLM}
          className="flex items-center justify-center space-x-1.5 text-xs font-semibold bg-slate-100 dark:bg-dark-base/80 hover:bg-slate-200 dark:hover:bg-dark-card text-brand-700 dark:text-brand-300 border border-brand-500/30 hover:border-brand-500/60 px-3.5 py-2 rounded-xl transition-all shadow-xs focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <RefreshCw className={`w-3.5 h-3.5 text-brand-600 dark:text-brand-400 ${testingLLM ? 'animate-spin' : ''}`} />
          <span>
            {testingLLM
              ? 'Comprobando...'
              : managed
                ? 'Probar motor de la empresa'
                : 'Auto-detectar / Probar LLM'}
          </span>
        </button>
      </div>

      {managed && (
        <div className="p-3 rounded-xl border border-indigo-500/30 bg-indigo-500/10 text-xs text-indigo-800 dark:text-indigo-200 flex items-start gap-2 animate-fadeIn">
          <ShieldCheck className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            La empresa administra el motor LLM (<strong>{getProviderLabel(managedProvider)}</strong>). Los campos son de
            solo lectura y las consultas usan <span className="font-mono">{managed ? llmPolicy!.default_model : shownModel}</span>.
          </span>
        </div>
      )}

      {llmPolicyError && (
        <div className="p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 text-xs text-amber-800 dark:text-amber-200 flex items-start gap-2 animate-fadeIn">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            No se pudo leer la política LLM: {llmPolicyError}. Los campos siguen editables, pero el backend puede
            ignorarlos si la empresa administra el motor.
          </span>
        </div>
      )}

      {/* Inputs Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
        <div>
          <label htmlFor="llm-provider-select" className="block text-[11px] font-bold uppercase tracking-wider text-gray-700 dark:text-gray-400 mb-1.5">
            {managed ? 'Proveedor (administrado)' : 'Proveedor de Motor LLM'}
          </label>
          <div className="relative">
            <select
              id="llm-provider-select"
              value={shownProvider}
              onChange={(e) => onProviderChange(e.target.value as LLMProvider)}
              disabled={managed}
              className="w-full bg-white dark:bg-dark-base/90 border border-slate-300 dark:border-dark-border rounded-xl px-3 py-2.5 text-xs text-gray-900 dark:text-white focus:outline-none focus:border-brand-500 transition-colors shadow-xs disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <option value="llama_cpp">llama.cpp / llama.exe serve (http://127.0.0.1:8080)</option>
              <option value="ollama">Ollama Local (http://localhost:11434)</option>
              <option value="openai_compatible">OpenAI-Compatible Local (LM Studio / vLLM)</option>
              {!knownProvider && (
                <option value={String(shownProvider)}>{getProviderLabel(shownProvider)} (empresa)</option>
              )}
            </select>
          </div>
        </div>

        <div>
          <label htmlFor="ollama-url-input" className="block text-[11px] font-bold uppercase tracking-wider text-gray-700 dark:text-gray-400 mb-1.5">
            {managed ? 'URL del Motor (administrada)' : 'URL Servidor Local'}
          </label>
          <input
            id="ollama-url-input"
            type="text"
            value={shownUrl}
            onChange={(e) => onOllamaUrlChange(e.target.value)}
            disabled={managed}
            placeholder={managed ? 'Definida por la empresa (no se muestra)' : 'http://127.0.0.1:8080'}
            className="w-full bg-white dark:bg-dark-base/90 border border-slate-300 dark:border-dark-border rounded-xl px-3 py-2.5 text-xs text-gray-900 dark:text-brand-300 font-mono focus:outline-none focus:border-brand-500 transition-colors shadow-xs disabled:opacity-60 disabled:cursor-not-allowed"
          />
        </div>

        <div className="sm:col-span-2">
          <label htmlFor="model-name-input" className="block text-[11px] font-bold uppercase tracking-wider text-gray-700 dark:text-gray-400 mb-1.5">
            {managed ? 'Modelo del Motor (administrado)' : 'Nombre del Modelo Activo'}
          </label>
          <input
            id="model-name-input"
            type="text"
            value={shownModel}
            onChange={(e) => onModelNameChange(e.target.value)}
            disabled={managed}
            placeholder={managed ? 'Definido por la empresa' : 'ej. Qwen2.5-Coder-7B-Instruct-GGUF'}
            className="w-full bg-white dark:bg-dark-base/90 border border-slate-300 dark:border-dark-border rounded-xl px-3 py-2.5 text-xs text-gray-900 dark:text-indigo-300 font-mono focus:outline-none focus:border-brand-500 transition-colors shadow-xs disabled:opacity-60 disabled:cursor-not-allowed"
          />

          {detectedModels.length > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5 pt-1">
              <span className="text-[10px] text-gray-600 dark:text-gray-400 font-semibold uppercase tracking-wider flex items-center gap-1">
                <Radio className="w-3 h-3 text-cyan-400 animate-pulse" /> Modelos Reportados por el Servidor:
              </span>
              {detectedModels.map((m) => {
                const active = shownModel === m;
                const cls = active
                  ? 'bg-brand-500/15 text-brand-700 dark:text-brand-300 border-brand-500/40 font-bold shadow-sm'
                  : 'bg-slate-100 dark:bg-dark-base/60 text-slate-700 dark:text-gray-400 border-slate-300 dark:border-dark-border';
                return managed ? (
                  <span key={m} className={`text-[10px] font-mono px-2.5 py-1 rounded-lg border ${cls}`}>
                    {m}
                  </span>
                ) : (
                  <button
                    key={m}
                    type="button"
                    onClick={() => onModelNameChange(m)}
                    className={`text-[10px] font-mono px-2.5 py-1 rounded-lg transition-all border ${cls} hover:text-slate-900 dark:hover:text-white hover:border-slate-400 dark:hover:border-gray-600`}
                  >
                    {m}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Test Feedback Banner */}
      {llmTestResult && (
        <div
          className={`p-3 rounded-xl border text-xs flex items-start space-x-2 animate-fadeIn ${
            llmTestResult.success
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300'
              : 'bg-rose-500/10 border-rose-500/30 text-rose-700 dark:text-rose-300'
          }`}
        >
          {llmTestResult.success ? <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" /> : <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-600 dark:text-rose-400" />}
          <div className="min-w-0 space-y-0.5">
            <span className="font-medium block">{llmTestResult.message} ({llmTestResult.latency_ms} ms)</span>
            {llmTestResult.active_url && (
              <span className="block font-mono text-[10px] opacity-80 truncate">Endpoint activo: {llmTestResult.active_url}</span>
            )}
          </div>
        </div>
      )}

      {/* Sugerencia de auto-deteccion: el diagnostico propone, no configura. */}
      {detectedEndpoint && (
        <div className="p-3 rounded-xl border border-cyan-500/30 bg-cyan-500/10 text-xs text-cyan-800 dark:text-cyan-200 flex flex-wrap items-center gap-2 animate-fadeIn">
          <Radio className="w-4 h-4 shrink-0" />
          <span className="font-medium">
            Se detectó un servidor LLM en <span className="font-mono">{detectedEndpoint.url}</span>. Tu configuración actual
            no se modificó.
          </span>
          <button
            type="button"
            onClick={onApplyDetected}
            className="ml-auto px-3 py-1.5 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white font-semibold transition-colors"
          >
            Usar esta configuración
          </button>
        </div>
      )}
    </div>
  );
};
