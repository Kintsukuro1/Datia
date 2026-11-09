import React, { useState, useEffect } from 'react';
import { X, Save } from 'lucide-react';
import { useModalA11y } from '../../hooks/useModalA11y';
import type { CatalogItem } from '../../features/admin/services/catalog_service';

interface CatalogEditModalProps {
  isOpen: boolean;
  item: CatalogItem | null;
  onClose: () => void;
  onSave: (desc: string, formula: string) => Promise<void> | void;
  errorMsg?: string | null;
}

export const CatalogEditModal: React.FC<CatalogEditModalProps> = ({
  isOpen,
  item,
  onClose,
  onSave,
  errorMsg,
}) => {
  const [formDesc, setFormDesc] = useState('');
  const [formFormula, setFormFormula] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (item) {
      setFormDesc(item.description || '');
      setFormFormula(item.business_formula || '');
    }
  }, [item]);

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, onClose);

  if (!isOpen || !item) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSaving) return;
    setIsSaving(true);
    try {
      await onSave(formDesc, formFormula);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div ref={modalRef} role="dialog" aria-modal="true" aria-label="Editar regla de catálogo" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm animate-fadeIn">
      <div className="glass-panel w-full max-w-md rounded-2xl sm:rounded-3xl border border-white/10 shadow-2xl overflow-hidden flex flex-col max-h-[88vh] sm:max-h-[90vh]">
        {/* Header */}
        <div className="shrink-0 px-5 sm:px-6 py-4 border-b border-app-border flex items-center justify-between bg-app-surface/95 backdrop-blur">
          <h4 className="text-sm font-bold text-app-text truncate">
            Editar Regla Semántica: {item.table_name}.{item.column_name || 'tabla'}
          </h4>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar modal"
            className="text-gray-400 hover:text-app-text p-1 rounded-lg hover:bg-app-card transition-colors shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {errorMsg && (
          <p role="alert" className="mx-5 mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
            {errorMsg}
          </p>
        )}

        {/* Scrollable Form Body */}
        <form id="catalog-edit-form" onSubmit={handleSubmit} className="flex-1 overflow-y-auto min-h-0 p-5 sm:p-6 space-y-3.5 text-xs">
          <div>
            <label htmlFor="catalog-edit-desc" className="block text-gray-300 font-medium mb-1">
              Descripción Semántica (para LLM)
            </label>
            <textarea
              id="catalog-edit-desc"
              aria-label="Descripción Semántica (para LLM)"
              value={formDesc}
              onChange={(e) => setFormDesc(e.target.value)}
              rows={3}
              className="w-full bg-app-bg border border-app-border rounded-xl px-3 py-2 text-app-text focus:outline-none focus:border-purple-500"
              required
            />
          </div>

          <div>
            <label htmlFor="catalog-edit-formula" className="block text-gray-300 font-medium mb-1">
              Fórmula o Regla de Cálculo
            </label>
            <input
              id="catalog-edit-formula"
              aria-label="Fórmula o Regla de Cálculo"
              type="text"
              value={formFormula}
              onChange={(e) => setFormFormula(e.target.value)}
              className="w-full bg-app-bg border border-app-border rounded-xl px-3 py-2 text-app-text focus:outline-none focus:border-purple-500 font-mono"
            />
          </div>
        </form>

        {/* Fixed Sticky Footer Actions */}
        <div className="shrink-0 px-5 sm:px-6 py-3.5 border-t border-app-border bg-app-surface/95 backdrop-blur flex justify-end space-x-2 z-10">
          <button
            type="button"
            onClick={onClose}
            disabled={isSaving}
            className="px-4 py-2 rounded-xl bg-app-card text-gray-300 text-xs hover:bg-app-border transition-colors disabled:opacity-40 disabled:pointer-events-none"
          >
            Cancelar
          </button>
          <button
            form="catalog-edit-form"
            type="submit"
            disabled={isSaving}
            className="flex items-center space-x-1 bg-purple-600 hover:bg-purple-500 text-white font-semibold px-4 py-2 rounded-xl text-xs transition-colors disabled:opacity-40 disabled:pointer-events-none"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{isSaving ? 'Guardando...' : 'Guardar Cambios'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
