import React from 'react';
import {
  Sparkles,
  DollarSign,
  Users,
  Calendar,
  FileText,
  Package,
  ArrowUpRight,
  ShieldCheck,
  Cpu,
} from 'lucide-react';
import logoDatiaDark from '../../pages/Logo_datia_2.png';
import logoDatiaLight from '../../pages/Logo_Datia_3.png';
import { stripSuggestionEmoji } from '../../features/chat/utils/query_builder';

interface ChatEmptyStateProps {
  promptSuggestions: string[];
  onSelectSuggestion: (suggestion: string) => void;
}

// El backend antepone un emoji a cada sugerencia. La tarjeta ya tiene su propio
// icono, y el emoji se renderiza como un glifo suelto (o roto) al lado: se quita
// solo para mostrar y para enviar, sin tocar el texto de la pregunta.
const suggestionIcon = (suggestion: string) => {
  const text = suggestion.toLowerCase();
  if (/(cliente|comprador|usuario|vendedor|proveedor)/.test(text)) {
    return <Users className="w-4 h-4 text-purple-400 shrink-0" />;
  }
  if (/(documento|boleta|factura)/.test(text)) {
    return <FileText className="w-4 h-4 text-cyan-400 shrink-0" />;
  }
  if (/(fecha|periodo|período|mes|año|anio|evoluci|tendencia|tiempo)/.test(text)) {
    return <Calendar className="w-4 h-4 text-cyan-400 shrink-0" />;
  }
  if (/(monto|total|ingreso|venta|precio|costo|margen)/.test(text)) {
    return <DollarSign className="w-4 h-4 text-emerald-400 shrink-0" />;
  }
  if (/(producto|filamento|cantidad|unidad|inventario)/.test(text)) {
    return <Package className="w-4 h-4 text-amber-400 shrink-0" />;
  }
  return <Sparkles className="w-4 h-4 text-brand-400 shrink-0" />;
};

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
                key={`${idx}-${suggestion}`}
                type="button"
                onClick={() => onSelectSuggestion(stripSuggestionEmoji(suggestion))}
                className="glass-card-interactive group flex flex-col gap-3 p-4 rounded-xl w-full cursor-pointer"
              >
                <div className="flex items-start justify-between w-full gap-2">
                  <div className="p-2 rounded-lg bg-app-surface border border-app-border group-hover:border-brand-500/40 transition-colors">
                    {suggestionIcon(suggestion)}
                  </div>
                  <ArrowUpRight className="w-4 h-4 text-app-muted group-hover:text-brand-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all shrink-0" />
                </div>
                <span className="text-xs sm:text-sm font-medium text-app-text leading-snug">
                  {stripSuggestionEmoji(suggestion)}
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
