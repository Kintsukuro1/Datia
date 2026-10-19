import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, TrendingUp, User as UserIcon, X } from 'lucide-react';

interface ProjectionSuggestionCardProps {
  question: string;
  onDismiss: () => void;
}

/**
 * El chat no proyecta: el LLM solo tiene filas historicas y cualquier cifra
 * futura seria inventada. Cuando la pregunta pide una proyeccion, el asistente
 * no finge un calculo: ofrece abrir la seccion Proyecciones con la pregunta ya
 * cargada, donde corre el motor determinista (serie real, banda de error medida
 * y backtests).
 *
 * La oferta vive FUERA de la lista de mensajes, igual que la vieja tarjeta de
 * prediccion: no es un `QueryResult` y meterla en el hilo haria que la
 * trazabilidad y los exportadores leyeran campos inexistentes.
 */
export function ProjectionSuggestionCard({ question, onDismiss }: ProjectionSuggestionCardProps) {
  const navigate = useNavigate();

  return (
    <div className="space-y-4 sm:space-y-6 pt-4 border-t border-dark-border/40 first:border-0 first:pt-0 animate-fadeIn">
      {/* La pregunta como burbuja de usuario: ancla que se proyecta exactamente eso. */}
      <div className="flex items-start space-x-2 sm:space-x-3 justify-end">
        <div className="chat-user-bubble bg-brand-600/15 dark:bg-brand-600/20 border border-brand-500/30 rounded-2xl rounded-tr-sm p-3.5 sm:p-4 max-w-[85%] sm:max-w-2xl shadow-md">
          <div className="flex items-center space-x-1.5 text-[10px] text-brand-700 dark:text-brand-400 font-semibold mb-1">
            <UserIcon className="w-3 h-3" />
            <span>Tú</span>
          </div>
          <p className="text-xs sm:text-sm text-gray-900 dark:text-white font-medium break-words">{question}</p>
        </div>
        <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-xl bg-brand-600 flex items-center justify-center text-white shrink-0 shadow-lg shadow-brand-600/30">
          <UserIcon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
        </div>
      </div>

      {/* Respuesta del asistente: no hay nada calculandose, y lo dice. */}
      <div className="flex items-start space-x-2 sm:space-x-3 justify-start">
        <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-xl bg-brand-600 flex items-center justify-center text-white shrink-0 shadow-sm">
          <Bot className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
        </div>
        <div className="chat-bot-response bg-white dark:bg-slate-900/90 border border-slate-200 dark:border-slate-700/60 rounded-2xl rounded-tl-sm p-4 max-w-[85%] sm:max-w-2xl space-y-2.5 shadow-xl">
          <div className="flex items-center space-x-2 text-[10px] text-brand-700 dark:text-brand-400 font-semibold">
            <Bot className="w-3 h-3" />
            <span>DATIA IA</span>
            <span className="text-slate-400 dark:text-slate-500">•</span>
            <span className="text-slate-600 dark:text-slate-400 font-normal">Proyección</span>
          </div>
          <p className="text-xs sm:text-sm leading-relaxed text-slate-700 dark:text-slate-200">
            Esto es una proyección y no se calcula en el chat: el modelo de texto solo tiene datos
            históricos. La sección <strong>Proyecciones</strong> corre el motor determinista sobre tu
            serie (banda de error medida y backtests incluidos) y publica el error junto al número.
          </p>
          <div className="flex items-center justify-between gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => navigate('/projections', { state: { question } })}
              aria-label="Abrir la pregunta en la sección Proyecciones"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-600 text-white text-[11px] font-semibold hover:bg-violet-500 transition-colors cursor-pointer"
            >
              <TrendingUp className="w-3.5 h-3.5" />
              <span>Abrir en Proyecciones</span>
            </button>
            <button
              type="button"
              onClick={onDismiss}
              aria-label="Descartar la sugerencia"
              title="Quitar esta tarjeta (la pregunta no se pierde: puedes volver a escribirla)"
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
