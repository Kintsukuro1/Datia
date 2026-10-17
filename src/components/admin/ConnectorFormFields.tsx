import React, { useState } from 'react';
import { CorporateConnection } from '../../features/admin/services/connector_service';
import { Eye, EyeOff } from 'lucide-react';

export interface FormFieldsProps {
  name: string;
  dbType: 'postgresql' | 'mssql' | 'mysql' | 'oracle';
  host: string;
  port: number;
  databaseName: string;
  username: string;
  password: string;
  isActive: boolean;
  editingConnector: CorporateConnection | null;
  onFieldChange: (field: string, value: any) => void;
  onDbTypeChange: (dbType: 'postgresql' | 'mssql' | 'mysql' | 'oracle') => void;
  /** El wizard dibuja el motor como tarjetas propias; el modal lo mantiene como select. */
  hideDbTypeSelector?: boolean;
}

const LABEL_CLASS =
  'block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1.5';
const FIELD_CLASS =
  'w-full bg-app-bg border border-app-border rounded-xl px-3 py-2.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500';

export const ConnectorFormFields: React.FC<FormFieldsProps> = ({
  name,
  dbType,
  host,
  port,
  databaseName,
  username,
  password,
  isActive,
  editingConnector,
  onFieldChange,
  onDbTypeChange,
  hideDbTypeSelector = false,
}) => {
  const [showPassword, setShowPassword] = useState(false);

  return (
    <>
      {/* Row 1: Nombre identificador + Motor */}
      <div className={hideDbTypeSelector ? '' : 'grid grid-cols-1 sm:grid-cols-2 gap-4'}>
        <div className="min-w-0">
          <label htmlFor="conn-name" className={LABEL_CLASS}>
            Nombre identificador
          </label>
          <input
            id="conn-name"
            type="text"
            value={name}
            onChange={(e) => onFieldChange('name', e.target.value)}
            placeholder="ej. BD_FINANZAS_PROD"
            className={FIELD_CLASS}
            required
          />
        </div>

        {!hideDbTypeSelector && (
          <div className="min-w-0">
            <label htmlFor="conn-db-type" className={LABEL_CLASS}>
              Motor de base de datos
            </label>
            <select
              id="conn-db-type"
              value={dbType}
              onChange={(e: any) => onDbTypeChange(e.target.value)}
              className={`${FIELD_CLASS} cursor-pointer`}
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mssql">Microsoft SQL Server</option>
              <option value="mysql">MySQL / MariaDB</option>
              <option value="oracle">Oracle Database</option>
            </select>
          </div>
        )}
      </div>

      {/* Row 2: Host + Puerto */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 sm:col-span-9">
          <label htmlFor="conn-host" className={LABEL_CLASS}>
            Host / servidor
          </label>
          <input
            id="conn-host"
            type="text"
            value={host}
            onChange={(e) => onFieldChange('host', e.target.value)}
            placeholder="10.0.1.45 o localhost"
            className={`${FIELD_CLASS} font-mono`}
            required
          />
        </div>

        <div className="col-span-12 sm:col-span-3">
          <label htmlFor="conn-port" className={LABEL_CLASS}>
            Puerto
          </label>
          <input
            id="conn-port"
            type="number"
            value={port || ''}
            onChange={(e) => onFieldChange('port', parseInt(e.target.value, 10) || 0)}
            className={`${FIELD_CLASS} font-mono text-center`}
            required
          />
        </div>
      </div>

      {/* Row 3: Nombre de la base de datos */}
      <div>
        <label htmlFor="conn-dbname" className={LABEL_CLASS}>
          Nombre de la base de datos
        </label>
        <input
          id="conn-dbname"
          type="text"
          value={databaseName}
          onChange={(e) => onFieldChange('databaseName', e.target.value)}
          placeholder="ej. corp_finanzas"
          className={FIELD_CLASS}
          required
        />
      </div>

      {/* Row 4: Usuario + Contraseña */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label htmlFor="conn-user" className={LABEL_CLASS}>
            Usuario de conexión
          </label>
          <input
            id="conn-user"
            type="text"
            value={username}
            onChange={(e) => onFieldChange('username', e.target.value)}
            placeholder="usr_read_only"
            className={`${FIELD_CLASS} font-mono`}
            required
          />
        </div>

        <div>
          <label htmlFor="conn-pass" className={LABEL_CLASS}>
            Contraseña {editingConnector && <span className="normal-case">(dejar vacío para no cambiar)</span>}
          </label>
          <div className="relative flex items-center">
            <input
              id="conn-pass"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(e) => onFieldChange('password', e.target.value)}
              placeholder="••••••••"
              className={`${FIELD_CLASS} pr-9`}
              required={!editingConnector}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
              className="absolute right-2 w-6 h-6 flex items-center justify-center text-app-muted hover:text-app-text transition-colors cursor-pointer"
            >
              {showPassword ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>
      </div>

      {/* Row 5: Activación */}
      <div className="flex items-center justify-between gap-4 p-3 rounded-xl bg-app-card border border-app-border">
        <div className="min-w-0 space-y-0.5">
          <label htmlFor="isActiveCheck" className="block text-xs font-semibold text-app-text cursor-pointer">
            Activar inmediatamente tras guardar
          </label>
          <p className="text-[11px] text-app-muted leading-relaxed">
            La fuente quedará habilitada para consultas analíticas al guardar.
          </p>
        </div>
        <label className="relative inline-flex items-center cursor-pointer shrink-0">
          <input
            id="isActiveCheck"
            type="checkbox"
            checked={isActive}
            onChange={(e) => onFieldChange('isActive', e.target.checked)}
            className="sr-only peer"
          />
          <div className="w-11 h-6 rounded-full bg-app-hover border border-app-border peer-checked:bg-brand-600 peer-checked:border-brand-500 transition-colors after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:h-[18px] after:w-[18px] after:rounded-full after:bg-white after:shadow-sm after:transition-all peer-checked:after:translate-x-5" />
        </label>
      </div>
    </>
  );
};
