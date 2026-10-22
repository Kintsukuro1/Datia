import React from 'react';
import { AlertCircle, Box, Check, CheckCircle2, Cylinder, Database, Server, ShieldCheck, UploadCloud } from 'lucide-react';
import { UploadDropzone } from '../upload/UploadDropzone';
import { ConnectorFormFields } from '../ConnectorFormFields';
import type { ConnectionTestResult } from '../../../features/admin/services/connector_service';
import type { IngestionMode, RemoteDbType, RemoteFormState } from './wizardConfig';

// Motores soportados por el backend, con el puerto por defecto real que aplica
// el formulario al seleccionarlos.
const ENGINES: {
  id: RemoteDbType;
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { id: 'postgresql', label: 'PostgreSQL', hint: 'Puerto 5432', icon: Database },
  { id: 'mssql', label: 'MS SQL Server', hint: 'Puerto 1433', icon: Server },
  { id: 'mysql', label: 'MySQL / MariaDB', hint: 'Puerto 3306', icon: Box },
  { id: 'oracle', label: 'Oracle Database', hint: 'Puerto 1521', icon: Cylinder },
];

const DEFAULT_PORTS: Record<RemoteDbType, number> = {
  postgresql: 5432,
  mssql: 1433,
  mysql: 3306,
  oracle: 1521,
};

interface InputStepProps {
  ingestionMode: IngestionMode;
  onIngestionModeChange: (mode: IngestionMode) => void;
  selectedFile: File | null;
  onRemoveFile: () => void;
  customName: string;
  onCustomNameChange: (name: string) => void;
  isDragging: boolean;
  onDraggingChange: (dragging: boolean) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileChange: (file: File) => void;
  onDrop: (e: React.DragEvent<HTMLDivElement>) => void;
  remoteForm: RemoteFormState;
  setRemoteForm: React.Dispatch<React.SetStateAction<RemoteFormState>>;
  testResult: ConnectionTestResult | null;
  setTestResult: (result: ConnectionTestResult | null) => void;
  onSubmit: (e: React.FormEvent) => void;
}

export const InputStep: React.FC<InputStepProps> = ({
  ingestionMode,
  onIngestionModeChange,
  selectedFile,
  onRemoveFile,
  customName,
  onCustomNameChange,
  isDragging,
  onDraggingChange,
  fileInputRef,
  onFileChange,
  onDrop,
  remoteForm,
  setRemoteForm,
  testResult,
  setTestResult,
  onSubmit,
}) => {
  const handleRemoteDbTypeChange = (dbType: RemoteDbType) => {
    setRemoteForm((prev) => ({ ...prev, dbType, port: DEFAULT_PORTS[dbType] ?? prev.port }));
    setTestResult(null);
  };

  return (
    <form id="wizard-step-1" onSubmit={onSubmit} className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 pb-3 border-b border-app-border">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
              Paso 1 de 5
            </span>
            <span className="text-[11px] text-app-muted">Configuración de la fuente</span>
          </div>
          <h4 className="text-sm font-bold text-app-text">Selecciona el método de ingreso de la Base de Datos</h4>
          <p className="text-xs text-app-muted leading-relaxed max-w-lg">
            Puedes importar un archivo o conectar un servidor corporativo en red.
          </p>
        </div>
        {/* Tabs */}
        <div className="flex items-center bg-app-bg rounded-xl p-1 border border-app-border shrink-0 self-start">
          <button
            type="button"
            onClick={() => onIngestionModeChange('file')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors flex items-center space-x-1.5 ${
              ingestionMode === 'file' ? 'bg-brand-600 text-white' : 'text-app-muted hover:text-app-text'
            }`}
          >
            <UploadCloud className="w-3.5 h-3.5" />
            <span>Archivo</span>
          </button>
          <button
            type="button"
            onClick={() => onIngestionModeChange('remote')}
            className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors flex items-center space-x-1.5 ${
              ingestionMode === 'remote' ? 'bg-brand-600 text-white' : 'text-app-muted hover:text-app-text'
            }`}
          >
            <Server className="w-3.5 h-3.5" />
            <span>Conexión Remota</span>
          </button>
        </div>
      </div>

      {ingestionMode === 'file' ? (
        <div className="space-y-4">
          <UploadDropzone
            selectedFile={selectedFile}
            fileInputRef={fileInputRef}
            isDragging={isDragging}
            setIsDragging={onDraggingChange}
            handleFileChange={onFileChange}
            handleDrop={onDrop}
            onRemoveFile={onRemoveFile}
          />
          <div className="space-y-1">
            <label htmlFor="wizard-custom-name" className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider">
              Nombre identificador de la fuente <span className="text-brand-500">*</span>
            </label>
            <input
              type="text"
              id="wizard-custom-name"
              value={customName}
              onChange={(e) => onCustomNameChange(e.target.value)}
              placeholder="Ej: FINANZAS_2026, INVENTARIO_GENERAL..."
              required
              className="w-full bg-app-bg border border-app-border rounded-xl px-3.5 py-2.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500"
            />
          </div>
        </div>
      ) : (
        <div className="space-y-5">
          {/* Motor de datos */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider">
                1. Motor de datos <span className="text-rose-500">*</span>
              </label>
              <span className="text-[10px] text-app-muted">{ENGINES.length} motores soportados</span>
            </div>
            <div role="radiogroup" aria-label="Motor de base de datos" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
              {ENGINES.map((engine) => {
                const selected = remoteForm.dbType === engine.id;
                const Icon = engine.icon;
                return (
                  <button
                    key={engine.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => handleRemoteDbTypeChange(engine.id)}
                    className={`relative flex flex-col items-start gap-2 p-3 rounded-xl border text-left transition-colors cursor-pointer ${
                      selected
                        ? 'bg-brand-500/10 border-brand-500/60 ring-1 ring-brand-500/40'
                        : 'bg-app-card border-app-border hover:bg-app-hover'
                    }`}
                  >
                    {selected && (
                      <span className="absolute top-2 right-2 w-4 h-4 rounded-full bg-brand-600 text-white flex items-center justify-center">
                        <Check className="w-2.5 h-2.5" />
                      </span>
                    )}
                    <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                      selected ? 'bg-brand-600 text-white' : 'bg-app-bg text-app-muted border border-app-border'
                    }`}>
                      <Icon className="w-4 h-4" />
                    </span>
                    <span className="flex flex-col min-w-0">
                      <span className="text-[11px] font-bold text-app-text leading-tight">{engine.label}</span>
                      <span className="text-[10px] text-app-muted">{engine.hint}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Parámetros de endpoint y autenticación */}
          <div className="rounded-xl bg-app-card border border-app-border p-4 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h5 className="text-xs font-bold text-app-text">2. Parámetros de endpoint y autenticación</h5>
                <p className="text-[11px] text-app-muted">Las credenciales se cifran antes de guardarse y no se devuelven por la API.</p>
              </div>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-app-bg border border-app-border text-[10px] font-semibold text-app-muted shrink-0 self-start">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                <span>Cifrado de credenciales activo</span>
              </span>
            </div>
            <ConnectorFormFields
              name={remoteForm.name}
              dbType={remoteForm.dbType}
              host={remoteForm.host}
              port={remoteForm.port}
              databaseName={remoteForm.databaseName}
              username={remoteForm.username}
              password={remoteForm.password}
              isActive={remoteForm.isActive}
              editingConnector={null}
              hideDbTypeSelector
              onFieldChange={(field, val) => {
                setTestResult(null);
                setRemoteForm((prev) => {
                  const updated = { ...prev, [field]: val };
                  if (field === 'name') {
                    const oldSlug = prev.name.toLowerCase().replace(/[^a-z0-9_]/g, '_');
                    if (!prev.databaseName || prev.databaseName === oldSlug) {
                      updated.databaseName = String(val).toLowerCase().replace(/[^a-z0-9_]/g, '_');
                    }
                  }
                  return updated;
                });
              }}
              onDbTypeChange={handleRemoteDbTypeChange}
            />
          </div>

          {/* Resultado de la prueba de conectividad */}
          {testResult && (
            <div
              role="status"
              className={`p-3 rounded-xl border flex items-start gap-2.5 text-xs animate-fadeIn ${
                testResult.success
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300'
                  : 'bg-rose-500/10 border-rose-500/30 text-rose-700 dark:text-rose-300'
              }`}
            >
              {testResult.success ? (
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              )}
              <div className="min-w-0 space-y-0.5">
                <span className="font-semibold block">{testResult.message}</span>
                {testResult.success && testResult.latency_ms > 0 && (
                  <span className="text-[11px] opacity-90">Latencia medida: {testResult.latency_ms} ms</span>
                )}
              </div>
            </div>
          )}

          <p className="text-[10px] text-app-muted">
            Cada base de datos se crea de forma individual e independiente en el sistema.
          </p>
        </div>
      )}
    </form>
  );
};
