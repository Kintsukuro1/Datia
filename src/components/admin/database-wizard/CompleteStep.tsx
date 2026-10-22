import React from 'react';
import { CheckCircle2 } from 'lucide-react';
import type { CorporateConnection } from '../../../features/admin/services/connector_service';
import type { DataDictionaryResponse } from '../../../features/admin/services/catalog_service';
import type { CreationMode } from './wizardConfig';

interface CompleteStepProps {
  connection: CorporateConnection;
  creationMode: CreationMode;
  dictionaryData: DataDictionaryResponse | null;
}

export const CompleteStep: React.FC<CompleteStepProps> = ({
  connection,
  creationMode,
  dictionaryData,
}) => {
  return (
    <div className="py-8 px-4 text-center space-y-5 animate-fadeIn max-w-md mx-auto">
      <div className="w-16 h-16 rounded-2xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex items-center justify-center mx-auto shadow-lg shadow-emerald-500/20">
        <CheckCircle2 className="w-8 h-8" />
      </div>
      <div className="space-y-1.5">
        <h4 className="text-base font-bold text-app-text">
          ¡Base de Datos y Diccionario Instalados Exitosamente!
        </h4>
        <p className="text-xs text-app-muted">
          La fuente de datos <strong className="text-app-text">{connection.name}</strong> ha sido registrada, estructurada y catalogada.
        </p>
      </div>

      <div className="p-4 rounded-2xl bg-app-bg border border-app-border text-left text-xs space-y-2">
        <div className="flex justify-between">
          <span className="text-app-muted">Fuente:</span>
          <span className="text-app-text font-semibold">{connection.name}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-app-muted">Modo de Creación:</span>
          <span className="text-brand-600 dark:text-brand-400 font-semibold">{creationMode === 'ai' ? 'Asistido por IA (Auditado)' : 'Manual por Usuario'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-app-muted">Tablas Catalogadas:</span>
          <span className="text-emerald-600 dark:text-emerald-400 font-semibold">{dictionaryData?.total_tables || 1} Tablas</span>
        </div>
        <div className="flex justify-between">
          <span className="text-app-muted">Gobernanza RBAC:</span>
          <span className="text-cyan-700 dark:text-cyan-300 font-semibold">Reglas Activas</span>
        </div>
      </div>
    </div>
  );
};
