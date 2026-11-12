import React from 'react';
import logoDatiaDark from '../../pages/Logo_datia_2.png';
import logoDatiaLight from '../../pages/Logo_Datia_3.png';

/**
 * Bienvenida del chat. Sin sugerencias ni sellos técnicos: el usuario llega a
 * preguntar, no a leer cómo está construido el motor. Si no sabe cómo pedirlo,
 * tiene un enlace a la ayuda en vez de tarjetas que le adivinen la pregunta.
 */
interface ChatEmptyStateProps {
  onOpenHelp: () => void;
}

export const ChatEmptyState: React.FC<ChatEmptyStateProps> = ({ onOpenHelp }) => {
  return (
    <div className="h-full flex flex-col items-center justify-center max-w-2xl mx-auto text-center space-y-6 sm:space-y-7 p-4 py-10 font-sans relative">
      {/* Datia Brand Avatar */}
      <div className="relative flex items-center justify-center">
        <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-xl bg-app-card flex items-center justify-center shadow-md border border-app-border overflow-hidden p-2">
          <img
            src={logoDatiaLight}
            alt="Logo de Dat.ia"
            width={557}
            height={448}
            decoding="async"
            className="block dark:hidden w-full h-full object-contain"
          />
          <img
            src={logoDatiaDark}
            alt="Logo de Dat.ia"
            width={528}
            height={442}
            decoding="async"
            className="hidden dark:block w-full h-full object-contain"
          />
        </div>
      </div>

      {/* Hero Content */}
      <div className="space-y-3 max-w-lg">
        <h2 className="text-2xl sm:text-3xl font-extrabold text-app-text tracking-tight leading-tight">
          ¿Qué datos deseas analizar hoy?
        </h2>
        <p className="text-xs sm:text-sm text-app-muted leading-relaxed">
          Escribe tu pregunta como se la harías a un compañero. Dat.ia busca la respuesta en tus
          datos y te la muestra en tablas y gráficos.
        </p>
        <button
          type="button"
          onClick={onOpenHelp}
          className="text-[11px] text-app-muted hover:text-brand-500 dark:hover:text-brand-400 underline underline-offset-2 transition-colors"
        >
          ¿No sabes cómo preguntar? Mira la ayuda
        </button>
      </div>
    </div>
  );
};
