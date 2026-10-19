import React from 'react';
import { AlertTriangle, HardDrive } from 'lucide-react';
import type { CorporateConnection } from '../../features/admin/services/connector_service';

interface ConnectionPillsProps {
  connectors: CorporateConnection[];
  selectedConnectionId: number | null;
  onSelect: (id: number) => void;
  /** Chip con el motor (`POSTGRESQL`, `SQLITE`...) — lo usa el catalogo. */
  showDbType?: boolean;
  /** Marca las fuentes pendientes de revisar permisos — lo usa la matriz RBAC. */
  showReviewWarning?: boolean;
  /** Marca los datasets importados — catalogo y permisos. */
  showUploaded?: boolean;
}

/**
 * Lista de fuentes de datos de las pestañas de administracion.
 *
 * El toolbar de cada pestaña NO se comparte: el catalogo agrega un subtitulo y
 * el boton de refresco, permisos arranca el refresco desde su encabezado y la
 * memoria aprendida solo rotula la lista. Lo que si era identico (y se
 * desincronizaba) es este bloque de botones; las insignias son opcionales
 * porque cada pantalla muestra la que le importa.
 */
export const ConnectionPills: React.FC<ConnectionPillsProps> = ({
  connectors,
  selectedConnectionId,
  onSelect,
  showDbType = false,
  showReviewWarning = false,
  showUploaded = false,
}) => (
  <>
    {connectors.map((c) => {
      const isSelected = c.id === selectedConnectionId;
      return (
        <button
          key={c.id}
          type="button"
          onClick={() => onSelect(c.id)}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-xl text-xs transition-all ${
            isSelected
              ? 'bg-brand-50 text-brand-700 border border-brand-300 dark:bg-purple-600/20 dark:text-purple-300 dark:border-purple-500/50 shadow-xs font-bold'
              : 'bg-white dark:bg-dark-card/60 text-slate-600 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-dark-card border border-slate-200 dark:border-dark-border font-medium'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${
              c.is_active ? 'bg-emerald-500 dark:bg-emerald-400' : 'bg-slate-400 dark:bg-gray-500'
            }`}
          />
          <span className="truncate max-w-[150px] sm:max-w-[200px]">{c.name}</span>
          {showDbType && (
            <span className="text-[9px] uppercase font-mono px-1.5 py-0.5 bg-slate-100 dark:bg-dark-base rounded text-slate-600 dark:text-gray-400">
              {c.db_type}
            </span>
          )}
          {showReviewWarning && c.requires_permission_review && (
            <span title="Pendiente de revisión de permisos">
              <AlertTriangle className="w-3 h-3 text-amber-500 dark:text-amber-400 shrink-0" />
            </span>
          )}
          {showUploaded && c.is_uploaded && (
            <span title="Archivo importado">
              <HardDrive className="w-3 h-3 text-cyan-600 dark:text-cyan-400 shrink-0" />
            </span>
          )}
        </button>
      );
    })}
  </>
);
