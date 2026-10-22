import React from 'react';
import { AlertTriangle, Edit3, Key, Table as TableIcon } from 'lucide-react';
import type {
  DataDictionaryColumn,
  DataDictionaryResponse,
} from '../../../features/admin/services/catalog_service';
import type { ColumnEdit, CreationMode } from './wizardConfig';

interface ReviewStepProps {
  dictionaryData: DataDictionaryResponse;
  creationMode: CreationMode;
  selectedTableIndex: number;
  onSelectTable: (index: number) => void;
  editedColumns: Record<string, ColumnEdit>;
  onColumnEditChange: (
    tableName: string,
    colName: string,
    field: keyof ColumnEdit,
    val: string
  ) => void;
}

export const ReviewStep: React.FC<ReviewStepProps> = ({
  dictionaryData,
  creationMode,
  selectedTableIndex,
  onSelectTable,
  editedColumns,
  onColumnEditChange,
}) => {
  const activeTable = dictionaryData.tables?.[selectedTableIndex];

  return (
    <div className="space-y-4 animate-fadeIn">
      {/* Step header */}
      <div className="flex flex-col gap-1 pb-3 border-b border-app-border">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
            Paso 3 de 5
          </span>
          <span className="text-[11px] text-app-muted">
            {creationMode === 'ai' ? 'Perfilado semántico con IA' : 'Edición manual del diccionario'}
          </span>
        </div>
        <h4 className="text-sm font-bold text-app-text">Revisión y validación del diccionario de datos</h4>
        <p className="text-xs text-app-muted leading-relaxed max-w-2xl">
          {creationMode === 'ai'
            ? 'Verificá que la IA haya generado descripciones correctas. Podés ajustar cualquier campo antes de autorizar.'
            : 'Definí los nombres de negocio, fórmulas y descripciones para cada columna detectada.'}
        </p>
      </div>

      {/* Resumen de lo procesado (conteos reales del diccionario) */}
      <div className="p-3 rounded-xl bg-app-card border border-app-border text-xs flex items-center justify-between gap-3">
        <div className="flex items-center space-x-2.5 text-app-muted min-w-0">
          <Edit3 className="w-4 h-4 text-brand-500 shrink-0" />
          <span className="truncate">
            {dictionaryData.connection_name} · <span className="font-mono uppercase">{dictionaryData.db_type}</span>
          </span>
        </div>
        <div className="text-right text-[11px] text-app-muted shrink-0">
          <span className="text-app-text font-bold">{dictionaryData.total_tables}</span> tablas •{' '}
          <span className="text-brand-600 dark:text-brand-400 font-bold">{dictionaryData.total_columns}</span> columnas
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* Master: tablas procesadas */}
        <aside className="lg:col-span-4 space-y-2">
          <div className="flex items-center justify-between px-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-app-muted">Tablas procesadas</span>
            <span className="text-[10px] text-app-muted">{dictionaryData.tables.length}</span>
          </div>
          <div className="flex flex-col gap-1.5 max-h-[46vh] overflow-y-auto custom-scrollbar pr-0.5">
            {dictionaryData.tables.map((t, idx) => {
              const isSelected = selectedTableIndex === idx;
              return (
                <button
                  key={t.table_name}
                  type="button"
                  onClick={() => onSelectTable(idx)}
                  aria-pressed={isSelected}
                  className={`w-full text-left p-2.5 rounded-lg border transition-colors cursor-pointer ${
                    isSelected
                      ? 'bg-brand-500/10 border-brand-500/50'
                      : 'bg-app-card border-app-border hover:bg-app-hover'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <TableIcon className={`w-3.5 h-3.5 shrink-0 ${isSelected ? 'text-brand-500' : 'text-app-muted'}`} />
                      <span className="font-mono text-[11px] font-semibold text-app-text truncate">{t.table_name}</span>
                    </span>
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted shrink-0">
                      {t.columns.length} col
                    </span>
                  </div>
                  <div className="text-[10px] text-app-muted pl-5 mt-0.5 truncate">
                    {t.row_count.toLocaleString()} filas · Esquema: {t.schema_name}
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {/* Detail: columnas de la tabla activa */}
        <section className="lg:col-span-8 space-y-3">
          {activeTable ? (
            <>
              <div className="flex items-center justify-between gap-3 pb-1">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="w-9 h-9 rounded-lg bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center shrink-0">
                    <TableIcon className="w-4 h-4" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-bold text-app-text font-mono truncate">{activeTable.table_name}</span>
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted uppercase">
                        {dictionaryData.db_type}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted">
                        Esquema: {activeTable.schema_name}
                      </span>
                    </div>
                    <span className="text-[10px] text-app-muted">Fuente: {dictionaryData.connection_name}</span>
                  </div>
                </div>
                {creationMode === 'ai' && (
                  <span className="text-[10px] text-amber-700 dark:text-amber-300 bg-amber-500/10 border border-amber-500/30 px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    <span>Revisá los campos de la IA</span>
                  </span>
                )}
              </div>

              <div className="space-y-2.5 max-h-[46vh] overflow-y-auto pr-1 custom-scrollbar">
                {activeTable.columns.map((col: DataDictionaryColumn) => {
                  const editKey = `${activeTable.table_name}.${col.name}`;
                  const values = editedColumns[editKey] || {
                    friendly_name: col.friendly_name || col.name,
                    description: col.description || '',
                    business_formula: col.business_formula || '',
                  };

                  return (
                    <div
                      key={col.name}
                      className="p-3 rounded-xl bg-app-card border border-app-border hover:border-brand-500/40 transition-colors space-y-2.5"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        {col.is_pk && (
                          <span title="Clave Primaria" className="text-amber-500">
                            <Key className="w-3.5 h-3.5" />
                          </span>
                        )}
                        <span className="font-mono text-xs font-bold text-app-text">{col.name}</span>
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-brand-600 dark:text-brand-400">
                          {col.data_type}
                        </span>
                        {!col.is_nullable && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted">
                            No nulo
                          </span>
                        )}

                        {/* Samples Preview */}
                        {col.sample_values && col.sample_values.length > 0 && (
                          <div className="flex items-center gap-1 text-[10px] text-app-muted ml-auto min-w-0">
                            <span className="shrink-0">Ejemplos:</span>
                            <div className="flex gap-1 overflow-hidden">
                              {col.sample_values.slice(0, 2).map((val, vIdx) => (
                                <span
                                  key={`${col.name}-sample-${val}-${vIdx}`}
                                  className="font-mono bg-app-bg px-1.5 py-0.5 rounded border border-app-border text-app-muted truncate max-w-[100px]"
                                >
                                  {val}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Editable Fields */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                        <div>
                          <label
                            htmlFor={`wizard-fn-${activeTable.table_name}-${col.name}`}
                            className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                          >
                            Nombre Amigable
                          </label>
                          <input
                            id={`wizard-fn-${activeTable.table_name}-${col.name}`}
                            type="text"
                            value={values.friendly_name}
                            onChange={(e) =>
                              onColumnEditChange(activeTable.table_name, col.name, 'friendly_name', e.target.value)
                            }
                            placeholder="Ej: Monto Facturado"
                            className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500"
                          />
                        </div>

                        <div>
                          <label
                            htmlFor={`wizard-formula-${activeTable.table_name}-${col.name}`}
                            className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                          >
                            Fórmula o Regla
                          </label>
                          <input
                            id={`wizard-formula-${activeTable.table_name}-${col.name}`}
                            type="text"
                            value={values.business_formula}
                            onChange={(e) =>
                              onColumnEditChange(activeTable.table_name, col.name, 'business_formula', e.target.value)
                            }
                            placeholder="Ej: Columna directa, sum(monto_usd)"
                            className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500 font-mono"
                          />
                        </div>

                        <div className="sm:col-span-2">
                          <label
                            htmlFor={`wizard-desc-${activeTable.table_name}-${col.name}`}
                            className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                          >
                            Definición / Descripción Funcional
                          </label>
                          <textarea
                            id={`wizard-desc-${activeTable.table_name}-${col.name}`}
                            rows={2}
                            value={values.description}
                            onChange={(e) =>
                              onColumnEditChange(activeTable.table_name, col.name, 'description', e.target.value)
                            }
                            placeholder="Describe el significado para el negocio..."
                            className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500 resize-none"
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="py-8 text-center text-app-muted text-xs">
              No se encontraron tablas para revisar en esta base de datos.
            </div>
          )}
        </section>
      </div>
    </div>
  );
};
