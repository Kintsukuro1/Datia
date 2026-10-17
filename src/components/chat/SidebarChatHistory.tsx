import React, { useState } from 'react';
import { Plus, History, Trash2, X, Database, Search } from 'lucide-react';
import { useModalA11y } from '../../hooks/useModalA11y';

export interface ChatThread {
  id: string;
  title: string;
  timestamp: string;
  dateGroup?: 'Hoy' | 'Ayer' | 'Anteriores';
}

interface SidebarChatHistoryProps {
  threads: ChatThread[];
  activeId: string | null;
  activeDatabaseName?: string;
  isOpenMobile?: boolean;
  searchInputRef?: React.RefObject<HTMLInputElement>;
  onCloseMobile?: () => void;
  onSelectThread: (id: string) => void;
  onNewThread: () => void;
  onDeleteThread: (id: string, e: React.MouseEvent) => void;
}

const DATE_GROUP_ORDER = ['Hoy', 'Ayer', 'Anteriores'] as const;

export const SidebarChatHistory: React.FC<SidebarChatHistoryProps> = ({
  threads,
  activeId,
  activeDatabaseName = 'BD Corporativa Local',
  isOpenMobile = false,
  searchInputRef,
  onCloseMobile,
  onSelectThread,
  onNewThread,
  onDeleteThread,
}) => {
  const [searchQuery, setSearchQuery] = useState('');

  // Dialog semantics, Escape, focus containment and focus restore.
  const drawerRef = useModalA11y<HTMLDivElement>(isOpenMobile, onCloseMobile);

  const filteredThreads = threads.filter((t) =>
    t.title.toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  // Agrupado por fecha. Un hilo sin `dateGroup` (estado previo del snapshot
  // local) cae en 'Anteriores'; nunca se lo inventa como de hoy.
  const groups = DATE_GROUP_ORDER.map((label) => ({
    label,
    items: filteredThreads.filter((t) => (t.dateGroup || 'Anteriores') === label),
  })).filter((g) => g.items.length > 0);

  const sidebarContent = (
    <div className="w-72 bg-app-surface border-r border-app-border flex flex-col h-full shrink-0 select-none">
      {/* New Query Button & Mobile Close Header */}
      <div className="p-3 sm:p-4 flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            onNewThread();
            if (onCloseMobile) onCloseMobile();
          }}
          className="flex-1 flex items-center justify-between gap-2 bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold py-2.5 px-3.5 rounded-xl shadow-xs transition-colors group cursor-pointer"
        >
          <span className="flex items-center gap-2">
            <Plus className="w-4 h-4 transition-transform group-hover:rotate-90" />
            <span>Nueva consulta</span>
          </span>
          <kbd className="hidden sm:inline text-[10px] text-white/80 bg-white/10 border border-white/20 rounded px-1.5 py-0.5 font-mono">
            Ctrl+N
          </kbd>
        </button>

        {onCloseMobile && (
          <button
            type="button"
            onClick={onCloseMobile}
            aria-label="Cerrar barra lateral"
            className="md:hidden p-2 rounded-xl text-app-muted hover:text-app-text hover:bg-app-hover transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Real-time Search Input */}
      <div className="px-3 pb-1">
        <div className="relative flex items-center">
          <Search className="w-3.5 h-3.5 text-app-muted absolute left-2.5 pointer-events-none" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Buscar conversación..."
            aria-label="Buscar en historial de consultas"
            className="w-full bg-app-card border border-app-border text-xs text-app-text placeholder:text-app-muted rounded-xl pl-8 pr-14 py-2 focus:outline-none focus:border-brand-500 transition-colors font-sans"
          />
          {searchQuery ? (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2 text-app-muted hover:text-app-text p-0.5 cursor-pointer"
              aria-label="Limpiar búsqueda"
            >
              <X className="w-3 h-3" />
            </button>
          ) : (
            <kbd className="absolute right-2 text-[10px] text-app-muted bg-app-card border border-app-border rounded px-1.5 py-0.5 font-mono">
              Ctrl+K
            </kbd>
          )}
        </div>
      </div>

      {/* Threads List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <div className="flex items-center justify-between px-2 text-[10px] font-semibold uppercase tracking-wider text-app-muted">
          <div className="flex items-center space-x-1.5">
            <History className="w-3 h-3 text-brand-400" />
            <span>Historial de consultas</span>
          </div>
          {searchQuery && (
            <span className="text-[9px] text-brand-700 dark:text-brand-400 bg-brand-50 dark:bg-brand-500/10 px-1.5 py-0.5 rounded border border-brand-200 dark:border-brand-500/20 font-mono">
              {filteredThreads.length} {filteredThreads.length === 1 ? 'coincidencia' : 'coincidencias'}
            </span>
          )}
        </div>

        {groups.map((group) => (
          <div key={group.label} className="space-y-1">
            <span className="block px-2 text-[10px] font-semibold uppercase tracking-wider text-app-muted">
              {group.label}
            </span>
            {group.items.map((t) => {
              const isActive = t.id === activeId;
              return (
                <div
                  key={t.id}
                  className="group relative w-full"
                >
                  {isActive && (
                    <span
                      aria-hidden="true"
                      className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r bg-brand-500"
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      onSelectThread(t.id);
                      if (onCloseMobile) onCloseMobile();
                    }}
                    className={`w-full flex flex-col gap-0.5 text-left pl-3.5 pr-8 py-2 rounded-xl cursor-pointer transition-colors border ${
                      isActive
                        ? 'bg-brand-600/10 border-brand-500/40'
                        : 'border-transparent hover:bg-app-hover/70'
                    }`}
                  >
                    <span
                      className={`text-xs truncate ${
                        isActive
                          ? 'font-semibold text-app-text'
                          : 'font-medium text-app-muted group-hover:text-app-text'
                      }`}
                    >
                      {t.title}
                    </span>
                    <span className="text-[10px] text-app-muted truncate">{t.timestamp}</span>
                  </button>

                  <button
                    type="button"
                    onClick={(e) => onDeleteThread(t.id, e)}
                    aria-label={`Eliminar conversación ${t.title}`}
                    className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 text-app-muted hover:text-rose-600 dark:hover:text-rose-400 transition-opacity absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer"
                    title="Eliminar conversación"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        ))}

        {threads.length === 0 && (
          <div className="text-center py-8 text-xs text-app-muted px-2">
            No hay conversaciones previas. Haz clic en "Nueva consulta" para iniciar una.
          </div>
        )}

        {threads.length > 0 && filteredThreads.length === 0 && (
          <div className="text-center py-8 text-xs text-app-muted px-2">
            No se encontraron consultas para "{searchQuery}".
          </div>
        )}
      </div>

      {/* Database Active Status Footer */}
      <div className="p-3 border-t border-app-border bg-app-card/60">
        <div className="flex items-center space-x-2.5 bg-app-surface p-2.5 rounded-xl border border-app-border">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 dark:bg-emerald-400 animate-pulse shrink-0" />
          <div className="truncate min-w-0">
            <div className="text-[10px] text-app-muted uppercase font-semibold flex items-center gap-1">
              <Database className="w-2.5 h-2.5 text-brand-400" />
              <span>Fuente BD activa</span>
            </div>
            <div className="text-[11px] text-app-text truncate font-mono font-medium">
              {activeDatabaseName}
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Persistent Sidebar */}
      <aside className="hidden md:flex flex-col h-full shrink-0">
        {sidebarContent}
      </aside>

      {/* Mobile Drawer Slide-over */}
      {isOpenMobile && (
        <div ref={drawerRef} role="dialog" aria-modal="true" aria-label="Historial de conversaciones" tabIndex={-1} className="fixed inset-0 z-40 md:hidden flex">
          {/* Backdrop */}
          <button
            type="button"
            aria-label="Cerrar barra lateral"
            tabIndex={-1}
            className="fixed inset-0 w-full h-full bg-black/70 backdrop-blur-xs transition-opacity animate-fadeIn cursor-default focus:outline-none"
            onClick={onCloseMobile}
          />
          {/* Drawer content */}
          <div className="relative z-50 flex h-full animate-slideInLeft">
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
};
