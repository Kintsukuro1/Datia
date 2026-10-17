import React from 'react';
import { CorporateConnection } from '../../features/admin/services/connector_service';
import { Database, RefreshCw, CheckCircle2, AlertCircle, Save, X } from 'lucide-react';
import { ConnectorFormFields } from './ConnectorFormFields';
import { useConnectorForm } from '../../features/admin/hooks/useConnectorForm';
import { useModalA11y } from '../../hooks/useModalA11y';

interface ConnectorModalProps {
  isOpen: boolean;
  editingConnector: CorporateConnection | null;
  onClose: () => void;
  onSaveSuccess: () => void;
}

export const ConnectorModal: React.FC<ConnectorModalProps> = ({
  isOpen,
  editingConnector,
  onClose,
  onSaveSuccess,
}) => {
  const { state, dispatch, handleTestConnection, handleSubmit } = useConnectorForm(
    isOpen,
    editingConnector,
    onSaveSuccess,
    onClose
  );

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, onClose);

  if (!isOpen) return null;

  return (
    <div ref={modalRef} role="dialog" aria-modal="true" aria-label="Configurar fuente de datos" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div className="glass-panel w-full max-w-xl rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-app-border flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-xl bg-brand-500/10 text-brand-600 dark:text-brand-300 border border-brand-500/20">
              <Database className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-app-text">
                {editingConnector ? 'Editar Conexión BD Corporativa' : 'Registrar Nueva Conexión BD Corporativa'}
              </h3>
              {/* Claim verificable: encrypt_credential antes de persistir (connector_service.py)
                  y CorporateConnectionOut no expone el campo password. */}
              <p className="text-xs text-app-muted">
                Solo lectura en las consultas; la contraseña se cifra antes de guardarse y no se devuelve por la API
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Cerrar modal"
            className="text-app-muted hover:text-app-text p-2 rounded-lg hover:bg-app-hover transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form Body */}
        <form id="connector-modal-form" onSubmit={handleSubmit} className="flex-1 overflow-y-auto min-h-0 p-5 sm:p-6 space-y-4">
          {state.errorMessage && (
            <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/20 text-rose-800 dark:text-rose-400 text-xs flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{state.errorMessage}</span>
            </div>
          )}

          <ConnectorFormFields
            name={state.name}
            dbType={state.dbType}
            host={state.host}
            port={state.port}
            databaseName={state.databaseName}
            username={state.username}
            password={state.password}
            isActive={state.isActive}
            editingConnector={editingConnector}
            onFieldChange={(field, value) => dispatch({ type: 'SET_FIELD', field, value })}
            onDbTypeChange={(dbType) => dispatch({ type: 'CHANGE_DB_TYPE', dbType })}
          />

          {/* Test Result Banner */}
          {state.testResult && (
            <div
              className={`p-3 rounded-xl border text-xs flex items-center space-x-2 animate-fadeIn ${
                state.testResult.success
                  ? 'bg-emerald-50 border-emerald-200 text-emerald-800 dark:bg-emerald-500/10 dark:border-emerald-500/20 dark:text-emerald-400'
                  : 'bg-rose-50 border-rose-200 text-rose-800 dark:bg-rose-500/10 dark:border-rose-500/20 dark:text-rose-400'
              }`}
            >
              {state.testResult.success ? (
                <CheckCircle2 className="w-4 h-4 shrink-0" />
              ) : (
                <AlertCircle className="w-4 h-4 shrink-0" />
              )}
              <span>{state.testResult.message} ({state.testResult.latency_ms} ms)</span>
            </div>
          )}
        </form>

        {/* Fixed Sticky Footer Actions */}
        <div className="shrink-0 px-5 sm:px-6 py-3.5 border-t border-app-border bg-app-bg/70 backdrop-blur flex items-center justify-between z-10">
          <button
            type="button"
            onClick={handleTestConnection}
            disabled={state.testingConn}
            className="flex items-center space-x-1.5 text-xs bg-app-card hover:bg-app-hover text-app-text border border-app-border px-3.5 py-2 rounded-xl transition-colors font-medium cursor-pointer disabled:opacity-60"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${state.testingConn ? 'animate-spin' : ''}`} />
            <span>{state.testingConn ? 'Probando Red...' : 'Probar Conexión BD'}</span>
          </button>

          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-app-muted hover:text-app-text hover:bg-app-hover text-xs font-medium transition-colors cursor-pointer"
            >
              Cancelar
            </button>

            <button
              form="connector-modal-form"
              type="submit"
              disabled={state.isSubmitting}
              className="flex items-center space-x-2 bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold px-5 py-2 rounded-xl shadow-sm transition-colors cursor-pointer disabled:opacity-60"
            >
              <Save className="w-4 h-4" />
              <span>{state.isSubmitting ? 'Guardando...' : editingConnector ? 'Guardar Cambios' : 'Registrar Conexión BD'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
