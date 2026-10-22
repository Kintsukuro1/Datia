import React from 'react';
import { AlertTriangle, BarChart3, MapPin, Sparkles, Trash2 } from 'lucide-react';
import type { NullsAuditResponse } from '../../../features/admin/services/catalog_service';
import type { NullPolicy } from './wizardConfig';

interface NullsStepProps {
  nullsData: NullsAuditResponse;
  selectedNullPolicy: NullPolicy;
  onPolicyChange: (policy: NullPolicy) => void;
}

export const NullsStep: React.FC<NullsStepProps> = ({
  nullsData,
  selectedNullPolicy,
  onPolicyChange,
}) => {
  return (
    <div className="space-y-5 animate-fadeIn">
      {/* Alert Header */}
      <div className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center space-x-2.5 min-w-0">
            <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0" />
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0">
                Paso 4 de 5
              </span>
              <h4 className="text-sm font-bold text-app-text truncate">Auditoría de Calidad: Valores Nulos Detectados</h4>
            </div>
          </div>
          <span className="text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0">
            {nullsData.total_tables_with_nulls} {nullsData.total_tables_with_nulls === 1 ? 'tabla afectada' : 'tablas afectadas'}
          </span>
        </div>
        <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
          Se detectaron campos con valores <code className="font-mono text-amber-700 dark:text-amber-200">NULL</code> en los datos recién ingresados.
          Como administrador, define la política corporativa que aplicará a estos registros:
        </p>
      </div>

      {/* Table / Column Breakdown List */}
      <div className="p-3.5 rounded-2xl bg-app-bg border border-app-border space-y-2.5 max-h-36 overflow-y-auto custom-scrollbar">
        <span className="text-[10px] font-bold text-app-muted uppercase tracking-wider block">
          Columnas afectadas por tabla:
        </span>
        <div className="space-y-2">
          {nullsData.tables.map((tbl) => (
            <div key={tbl.table_name} className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="font-mono font-bold text-app-text bg-app-card px-2 py-0.5 rounded border border-app-border">
                {tbl.table_name}
              </span>
              <span className="text-app-muted">→</span>
              {tbl.columns_with_nulls.map((col) => (
                <span
                  key={col.column_name}
                  className="font-mono bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded text-[11px]"
                >
                  {col.column_name}: <strong>{col.null_count}</strong> nulos ({col.null_percentage}%)
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>

      {/* 4 Interactive Choice Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
        {/* Option 1: Delete Rows */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => onPolicyChange('delete_rows')}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onPolicyChange('delete_rows'); }}
          className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
            selectedNullPolicy === 'delete_rows'
              ? 'border-rose-500 bg-rose-500/10 shadow-lg shadow-rose-500/10 ring-1 ring-rose-500'
              : 'border-app-border bg-app-card hover:border-rose-500/40'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30">
              <Trash2 className="w-4 h-4" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-700 dark:text-rose-300 border border-rose-500/30">
              Depuración
            </span>
          </div>
          <h5 className="text-xs font-bold text-app-text">1. Eliminar registros con nulos</h5>
          <p className="text-[11px] text-app-muted leading-relaxed">
            Elimina físicamente de la base de datos las filas que contienen campos vacíos, garantizando que solo existan filas 100% completas.
          </p>
        </div>

        {/* Option 2: Mode */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => onPolicyChange('mode')}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onPolicyChange('mode'); }}
          className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
            selectedNullPolicy === 'mode'
              ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500'
              : 'border-app-border bg-app-card hover:border-brand-500/40'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
              <BarChart3 className="w-4 h-4" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
              Estadística
            </span>
          </div>
          <h5 className="text-xs font-bold text-app-text">2. Adaptar a la moda estadística</h5>
          <p className="text-[11px] text-app-muted leading-relaxed">
            Imputa cada valor nulo con el valor más frecuente de su columna, preservando la tendencia y distribución mayoritaria.
          </p>
        </div>

        {/* Option 3: Nearest */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => onPolicyChange('nearest')}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onPolicyChange('nearest'); }}
          className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
            selectedNullPolicy === 'nearest'
              ? 'border-cyan-500 bg-cyan-500/10 shadow-lg shadow-cyan-500/10 ring-1 ring-cyan-500'
              : 'border-app-border bg-app-card hover:border-cyan-500/40'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30">
              <MapPin className="w-4 h-4" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30">
              Proximidad
            </span>
          </div>
          <h5 className="text-xs font-bold text-app-text">3. Adaptar al más cercano</h5>
          <p className="text-[11px] text-app-muted leading-relaxed">
            Rellena cada valor nulo con el dato contiguo no-nulo más próximo (interpolación de arrastre / forward-fill).
          </p>
        </div>

        {/* Option 4: Open to User */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => onPolicyChange('open')}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onPolicyChange('open'); }}
          className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
            selectedNullPolicy === 'open'
              ? 'border-emerald-500 bg-emerald-500/10 shadow-lg shadow-emerald-500/10 ring-1 ring-emerald-500'
              : 'border-app-border bg-app-card hover:border-emerald-500/40'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="p-2 rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
              <Sparkles className="w-4 h-4" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
              Recomendado
            </span>
          </div>
          <h5 className="text-xs font-bold text-app-text">4. Dejar la opción abierta al usuario</h5>
          <p className="text-[11px] text-app-muted leading-relaxed">
            Conserva los datos intactos. Cuando una consulta maneje nulos, la IA le advertirá previamente y le dará a elegir qué hacer.
          </p>
        </div>
      </div>
    </div>
  );
};
