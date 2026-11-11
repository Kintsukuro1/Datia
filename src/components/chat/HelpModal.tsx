import React from 'react';
import { BarChart3, Calendar, Download, HelpCircle, ShieldCheck, Sparkles, X } from 'lucide-react';
import { useModalA11y } from '../../hooks/useModalA11y';

interface HelpModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Ayuda en lenguaje de usuario: que puede preguntar, como se pide el periodo,
 * que no hace y como exportar. Sin SQL, RBAC ni nombres de motor.
 */
export const HelpModal: React.FC<HelpModalProps> = ({ isOpen, onClose }) => {
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, onClose);
  if (!isOpen) return null;

  const items = [
    {
      icon: <Sparkles className="h-4 w-4" aria-hidden />,
      title: 'Preguntá con tus palabras',
      body: 'Escribí lo que necesitás saber, como se lo preguntarías a un compañero. No hace falta conocer los nombres de las tablas ni escribir SQL.',
    },
    {
      icon: <Calendar className="h-4 w-4" aria-hidden />,
      title: 'Decí el período que querés',
      body: 'Podés escribir «el próximo año», «6 semanas» o «3 meses». Dat.ia ajusta la proyección a lo que pidas en la pregunta.',
    },
    {
      icon: <BarChart3 className="h-4 w-4" aria-hidden />,
      title: 'Mirá la respuesta',
      body: 'Cada respuesta trae una tabla o un gráfico, los números principales y una explicación en lenguaje simple.',
    },
    {
      icon: <ShieldCheck className="h-4 w-4" aria-hidden />,
      title: 'Qué no hace',
      body: 'No inventa datos ni calcula por su cuenta. Si no hay información suficiente, te lo dice y explica por qué.',
    },
    {
      icon: <Download className="h-4 w-4" aria-hidden />,
      title: 'Llevate el resultado',
      body: 'Podés descargar la proyección en PDF o Excel desde la tarjeta de resultados.',
    },
  ];

  return (
    <div
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-label="Ayuda de Dat.ia"
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm animate-fadeIn"
    >
      <div className="w-full max-w-lg overflow-hidden rounded-2xl border border-app-border bg-app-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-app-border px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-bold text-app-text">
            <HelpCircle className="h-4 w-4 text-brand-500" aria-hidden />
            Cómo usar Dat.ia
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar la ayuda"
            className="rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-4">
          {items.map((item) => (
            <div key={item.title} className="flex items-start gap-3">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-brand-500/30 bg-brand-500/10 text-brand-600 dark:text-brand-400">
                {item.icon}
              </span>
              <div>
                <h3 className="text-xs font-semibold text-app-text">{item.title}</h3>
                <p className="mt-0.5 text-xs leading-relaxed text-app-muted">{item.body}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="flex justify-end border-t border-app-border px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-brand-600 px-4 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-brand-500"
          >
            Entendido
          </button>
        </div>
      </div>
    </div>
  );
};
