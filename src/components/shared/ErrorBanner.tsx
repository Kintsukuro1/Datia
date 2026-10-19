import { AlertTriangle, type LucideIcon } from 'lucide-react';

/**
 * Banner de error de los paneles de administracion.
 *
 * El mismo bloque (paleta rose + icono + mensaje) estaba copiado en 7 lugares;
 * un ajuste de contraste o de espaciado en una copia dejaba a las otras seis
 * desincronizadas sin que nada lo notara. `items-start` es a proposito: un
 * mensaje de error puede ocupar varias lineas.
 */
export function ErrorBanner({ message, icon: Icon = AlertTriangle }: { message: string | null; icon?: LucideIcon }) {
  return (
    <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/20 text-rose-800 dark:text-rose-400 text-xs flex items-start gap-2 animate-fadeIn">
      <Icon className="w-4 h-4 shrink-0 mt-0.5" />
      <span>{message}</span>
    </div>
  );
}
