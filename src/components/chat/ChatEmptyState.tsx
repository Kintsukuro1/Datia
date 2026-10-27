import React from 'react';
import {
  Sparkles,
  TrendingUp,
  DollarSign,
  AlertTriangle,
  Users,
  ArrowUpRight,
  ShieldCheck,
  Cpu,
} from 'lucide-react';
import logoDatiaDark from '../../pages/Logo_datia_2.png';
import logoDatiaLight from '../../pages/Logo_Datia_3.png';

interface ChatEmptyStateProps {
  promptSuggestions: string[];
  onSelectSuggestion: (suggestion: string) => void;
}

const SUGGESTION_ICONS = [
  <TrendingUp className="w-4 h-4 text-cyan-400 shrink-0" key="trend" />,
  <DollarSign className="w-4 h-4 text-emerald-400 shrink-0" key="dollar" />,
  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" key="alert" />,
  <Users className="w-4 h-4 text-purple-400 shrink-0" key="users" />,
];

export const ChatEmptyState: React.FC<ChatEmptyStateProps> = ({
  promptSuggestions,
  onSelectSuggestion,
}) => {
  return (
    <div className="h-full flex flex-col items-center justify-center max-w-3xl mx-auto text-center space-y-6 sm:space-y-8 p-4 py-10 font-sans relative">
      {/* Datia Brand Avatar */}
      <div className="relative flex items-center justify-center">
        <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-xl bg-app-card flex items-center justify-center shadow-md border border-app-border overflow-hidden p-2">
          <img src={logoDatiaLight} alt="Logo de Datia" className="block dark:hidden w-full h-full object-contain" />
          <img src={logoDatiaDark} alt="Logo de Datia" className="hidden dark:block w-full h-full object-contain" />
        </div>
      </div>

      {/* Hero Content */}
      <div className="space-y-3 max-w-xl">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-app-card border border-app-border text-app-muted text-xs font-medium">
          <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" />
          <Sparkles className="w-3.5 h-3.5 text-brand-400" />
          <span>DATIA · Asistente Text-to-SQL</span>
        </div>
        <h2 className="text-2xl sm:text-3xl font-extrabold text-app-text tracking-tight leading-tight">
          ¿Qué datos deseas analizar hoy?
        </h2>
        <p className="text-xs sm:text-sm text-app-muted leading-relaxed max-w-md mx-auto">
          Formula tu pregunta en lenguaje natural. La IA traducirá tu consulta a SQL seguro validado por AST Guardrail según tu perfil RBAC.
        </p>
      </div>

      {/* Categorized Suggestion Cards */}
      {promptSuggestions.length > 0 && (
        <div className="w-full max-w-2xl space-y-3 pt-1">
          <div className="text-[11px] font-bold text-app-muted uppercase tracking-wider text-left pl-1">
            Sugerencias de consulta rápidas:
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-left">
            {promptSuggestions.map((suggestion, idx) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => onSelectSuggestion(suggestion)}
                className="glass-card-interactive group flex flex-col gap-3 p-4 rounded-xl w-full cursor-pointer"
              >
                <div className="flex items-start justify-between w-full gap-2">
                  <div className="p-2 rounded-lg bg-app-surface border border-app-border group-hover:border-brand-500/40 transition-colors">
                    {SUGGESTION_ICONS[idx % SUGGESTION_ICONS.length]}
                  </div>
                  <ArrowUpRight className="w-4 h-4 text-app-muted group-hover:text-brand-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all shrink-0" />
                </div>
                <span className="text-xs sm:text-sm font-medium text-app-text leading-snug">
                  {suggestion}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Governance footer: hechos verificables en el código (guardrail AST,
          permisos RBAC, LLM local). Sin latencias ni retención inventadas. */}
      <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[11px] text-app-muted pt-1">
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
          Consultas SQL validadas por AST Guardrail
        </span>
        <span className="flex items-center gap-1.5">
          <Users className="w-3.5 h-3.5 text-brand-400" />
          Permisos por perfil (RBAC)
        </span>
        <span className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5 text-brand-400" />
          Procesamiento con IA local
        </span>
      </div>
    </div>
  );
};
