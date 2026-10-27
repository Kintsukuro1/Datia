import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, EyeOff, Lock, RefreshCw } from 'lucide-react';
import { ErrorBanner } from '../shared/ErrorBanner';
import { PLATFORM_ADMIN_ROLE_NAME } from '../../constants';
import { AdminRole } from '../../features/admin/hooks/useAdminPermissions';
import {
  ColumnPermissionType,
  RoleColumnPermission,
  permissionService,
  resolveColumnPolicy,
} from '../../features/admin/services/permission_service';
import {
  DataDictionaryResponse,
  catalogService,
} from '../../features/admin/services/catalog_service';
import { getApiErrorMessage } from '../../shared/api/api_client';

/**
 * Editor de la CLS (seguridad a nivel de columna) por rol y conexión.
 *
 * Es la contraparte escribible del panel de cobertura: antes las columnas
 * BLOCKED/MASKED solo se podían sembrar para el conector de plataforma, así que
 * un dataset subido nacía sin ninguna política y no había forma de marcar su RUT
 * o su sueldo. Acá el admin clasifica cualquier columna real del diccionario.
 *
 * Guarda al cambiar el selector, sin borrador: cada fila es una decisión
 * independiente y el servidor confirma la fila aplicada. Un fallo deja la
 * política anterior a la vista y muestra el error, en vez de pintar un cambio que
 * no existe.
 */
interface AdminColumnPermissionsPanelProps {
  connectionId: number | null;
  roles: AdminRole[];
  /** Tablas ya conocidas por la matriz; el diccionario tiene prioridad. */
  tables: string[];
  /** La cobertura se recalcula desde el guardarraíl tras cada cambio. */
  onSaved?: () => void;
}

const TYPE_LABEL: Record<ColumnPermissionType, string> = {
  ALLOWED: 'Permitida',
  MASKED: 'Enmascarada',
  BLOCKED: 'Bloqueada',
};

const selectClass =
  'bg-slate-50 dark:bg-dark-base border border-slate-300 dark:border-dark-border ' +
  'rounded-lg px-2 py-1 text-[11px] font-medium text-slate-900 dark:text-white ' +
  'focus:outline-none focus:border-brand-500 disabled:opacity-50 cursor-pointer';

const keyOf = (table: string, column: string) =>
  `${table.toLowerCase()}.${column.toLowerCase()}`;

export const AdminColumnPermissionsPanel: React.FC<AdminColumnPermissionsPanelProps> = ({
  connectionId,
  roles,
  tables,
  onSaved,
}) => {
  const eligibleRoles = useMemo(
    () => roles.filter((r) => r.name !== PLATFORM_ADMIN_ROLE_NAME),
    [roles]
  );

  const [roleId, setRoleId] = useState<number | null>(null);
  const [table, setTable] = useState('');
  const [policies, setPolicies] = useState<RoleColumnPermission[]>([]);
  const [dictionary, setDictionary] = useState<DataDictionaryResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [lastSaved, setLastSaved] = useState<string | null>(null);

  useEffect(() => {
    if (roleId !== null && eligibleRoles.some((r) => r.id === roleId)) return;
    setRoleId(eligibleRoles[0]?.id ?? null);
  }, [eligibleRoles, roleId]);

  const load = useCallback(async () => {
    setSaveError(null);
    setLastSaved(null);
    setTable('');
    if (connectionId === null) {
      setPolicies([]);
      setDictionary(null);
      return;
    }
    setIsLoading(true);
    setLoadError(null);
    try {
      // Las dos fuentes son necesarias: las políticas dicen qué está decidido y
      // el diccionario, qué columnas existen. Sin fila la columna es legible, así
      // que sin diccionario el panel no tendría nada que ofrecer.
      const [rows, dict] = await Promise.all([
        permissionService.getColumnPermissions(connectionId),
        catalogService.getDataDictionary(connectionId),
      ]);
      setPolicies(rows);
      setDictionary(dict);
    } catch (err: any) {
      setPolicies([]);
      setDictionary(null);
      setLoadError(
        getApiErrorMessage(err, 'No se pudieron leer las columnas ni sus políticas. No se puede afirmar qué está protegido.')
      );
    } finally {
      setIsLoading(false);
    }
  }, [connectionId]);

  useEffect(() => {
    load();
  }, [load]);

  const tableOptions = useMemo(() => {
    const fromDictionary = (dictionary?.tables || []).map((t) => t.table_name);
    return fromDictionary.length > 0 ? fromDictionary : tables;
  }, [dictionary, tables]);

  useEffect(() => {
    if (table) return;
    if (tableOptions.length > 0) setTable(tableOptions[0]);
  }, [table, tableOptions]);

  const columns = useMemo(() => {
    const found = (dictionary?.tables || []).find(
      (t) => t.table_name.toLowerCase() === table.toLowerCase()
    );
    return found?.columns || [];
  }, [dictionary, table]);

  const selectedRole = eligibleRoles.find((r) => r.id === roleId) || null;

  const rolePolicies = useMemo(
    () => policies.filter((p) => p.role_id === roleId),
    [policies, roleId]
  );

  const policyFor = useCallback(
    (columnName: string) => resolveColumnPolicy(rolePolicies, table, columnName),
    [rolePolicies, table]
  );

  const rolePolicyCount = policies.filter((p) => p.role_id === roleId).length;

  const resetPolicy = async (columnName: string) => {
    if (connectionId === null || roleId === null || !table) return;
    const key = keyOf(table, columnName);
    setSavingKey(key);
    setSaveError(null);
    setLastSaved(null);
    try {
      const res = await permissionService.deleteColumnPermission({
        connectionId,
        roleId,
        tableName: table,
        columnName,
      });
      // Sin fila no hay política que mostrar; "Permitida" era el default, no
      // una decisión. El estado local se limpia aunque el servidor diga
      // `deleted: false` (reset repetido): las dos respuestas son "no hay fila".
      setPolicies((prev) =>
        prev.filter(
          (p) =>
            !(
              p.role_id === roleId &&
              keyOf(p.table_name, p.column_name) === key
            )
        )
      );
      setLastSaved(
        `${table}.${columnName} → Sin política${res.deleted ? '' : ' (ya estaba)'}`
      );
      onSaved?.();
    } catch (err: any) {
      setSaveError(
        getApiErrorMessage(err, 'El servidor no confirmó el reset. La columna conserva su política.')
      );
    } finally {
      setSavingKey(null);
    }
  };

  const changePolicy = async (columnName: string, type: ColumnPermissionType) => {
    if (connectionId === null || roleId === null || !table) return;
    const key = keyOf(table, columnName);
    setSavingKey(key);
    setSaveError(null);
    setLastSaved(null);
    try {
      const res = await permissionService.setColumnPermissions({
        connectionId,
        roleId,
        permissionType: type,
        columns: [{ tableName: table, columnName }],
      });
      const confirmed = res.permissions[0];
      if (confirmed) {
        // Solo se pinta lo que el servidor confirmó: la respuesta trae la fila
        // con su id, así que el estado local queda igual al de la base.
        setPolicies((prev) => {
          const idx = prev.findIndex(
            (p) =>
              p.role_id === confirmed.role_id &&
              keyOf(p.table_name, p.column_name) === keyOf(confirmed.table_name, confirmed.column_name)
          );
          if (idx === -1) return [...prev, confirmed];
          const next = prev.slice();
          next[idx] = confirmed;
          return next;
        });
        setLastSaved(`${table}.${columnName} → ${TYPE_LABEL[type]}`);
      }
      onSaved?.();
    } catch (err: any) {
      setSaveError(
        getApiErrorMessage(err, 'El servidor no confirmó la política. La columna conserva la anterior.')
      );
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <div id="cls-column-panel" className="p-3.5 rounded-xl border border-slate-200 dark:border-dark-border bg-slate-50 dark:bg-dark-base/80 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Lock className="w-4 h-4 text-brand-600 dark:text-purple-400 shrink-0" />
          <span className="text-xs font-bold text-slate-900 dark:text-white">
            Permisos de columna (por rol)
          </span>
          <span className="text-[11px] text-slate-500 dark:text-gray-400">
            Clasificá qué columna se permite, se enmascara o se bloquea
          </span>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={isLoading}
          className="p-1.5 text-slate-600 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white rounded-lg hover:bg-slate-200 dark:hover:bg-dark-card transition-colors disabled:opacity-50 cursor-pointer"
          title="Recargar columnas y políticas"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {connectionId === null && (
        <p className="text-[11px] text-slate-500 dark:text-gray-400">
          Elegí una fuente de datos para ver sus columnas.
        </p>
      )}

      {connectionId !== null && eligibleRoles.length === 0 && (
        <p className="text-[11px] text-slate-500 dark:text-gray-400">
          No hay roles distintos del administrador para aplicar políticas: el admin está exento de CLS por diseño.
        </p>
      )}

      {connectionId !== null && eligibleRoles.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="cls-role" className="text-[11px] font-semibold text-slate-700 dark:text-gray-300">
              Rol
            </label>
            <select
              id="cls-role"
              value={roleId ?? ''}
              onChange={(e) => {
                setRoleId(Number(e.target.value));
                setLastSaved(null);
                setSaveError(null);
              }}
              disabled={isLoading || savingKey !== null}
              className={selectClass}
            >
              {eligibleRoles.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>

            <label htmlFor="cls-table" className="text-[11px] font-semibold text-slate-700 dark:text-gray-300 ml-2">
              Tabla
            </label>
            <select
              id="cls-table"
              value={table}
              onChange={(e) => {
                setTable(e.target.value);
                setLastSaved(null);
                setSaveError(null);
              }}
              disabled={isLoading || savingKey !== null || tableOptions.length === 0}
              className={selectClass}
            >
              {tableOptions.length === 0 && <option value="">Sin tablas</option>}
              {tableOptions.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>

            {rolePolicyCount > 0 && (
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-lg bg-brand-50 text-brand-700 border border-brand-200 dark:bg-purple-500/10 dark:text-purple-300 dark:border-purple-500/20">
                {rolePolicyCount} política(s) en esta fuente
              </span>
            )}
          </div>

          {isLoading && (
            <p className="text-[11px] text-slate-500 dark:text-gray-400">Cargando columnas...</p>
          )}

          {loadError && <ErrorBanner message={loadError} />}
          {saveError && <ErrorBanner message={saveError} />}

          {lastSaved && (
            <div className="text-[11px] text-emerald-700 dark:text-emerald-400 flex items-center gap-1.5">
              <Check className="w-3.5 h-3.5 shrink-0" />
              <span>Guardado: {lastSaved}</span>
            </div>
          )}

          {!isLoading && !loadError && tableOptions.length === 0 && (
            <p className="text-[11px] text-slate-500 dark:text-gray-400">
              El diccionario no devolvió tablas para esta fuente. Si esperabas verlas, recargá la fuente desde
              «Fuentes BD Corporativas».
            </p>
          )}

          {!isLoading && !loadError && tableOptions.length > 0 && columns.length === 0 && (
            <p className="text-[11px] text-slate-500 dark:text-gray-400">
              El diccionario no devolvió columnas para «{table}».
            </p>
          )}

          {columns.length > 0 && (
            <div className="max-h-72 overflow-y-auto space-y-1.5 pr-1">
              {columns.map((col) => {
                const resolved = policyFor(col.name);
                const isSavingThis = savingKey === keyOf(table, col.name);
                return (
                  <div
                    key={col.name}
                    className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-white dark:bg-dark-card/60 border border-slate-200 dark:border-dark-border"
                  >
                    <div className="min-w-0">
                      <span className="font-mono text-[11px] font-semibold text-slate-900 dark:text-white truncate block">
                        {col.name}
                        {col.is_pk && (
                          <span className="ml-1.5 text-[9px] font-normal text-slate-400 dark:text-gray-500">PK</span>
                        )}
                      </span>
                      <span className="text-[9px] text-slate-500 dark:text-gray-500">{col.data_type}</span>
                      {resolved.enforcedBy && (
                        <span
                          className="block text-[9px] text-amber-700 dark:text-amber-400"
                          title={
                            `La política se aplica por nombre de columna en toda la conexión: ` +
                            `«${resolved.enforcedBy}» la declara ${TYPE_LABEL[resolved.effectivePolicy].toLowerCase()} ` +
                            `y el chat aplica esa, no la de «${table}».`
                          }
                        >
                          Aplica {TYPE_LABEL[resolved.effectivePolicy]} por «{resolved.enforcedBy}»
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {resolved.effectivePolicy === 'BLOCKED' && (
                        <Lock className="w-3 h-3 text-rose-600 dark:text-rose-400" aria-hidden="true" />
                      )}
                      {resolved.effectivePolicy === 'MASKED' && (
                        <EyeOff className="w-3 h-3 text-amber-600 dark:text-amber-400" aria-hidden="true" />
                      )}
                      <select
                        aria-label={`Política de ${col.name} en ${table} para ${selectedRole?.name ?? 'el rol'}`}
                        value={resolved.rowPolicy ?? 'NONE'}
                        onChange={(e) => {
                          const next = e.target.value;
                          if (next === 'NONE') {
                            void resetPolicy(col.name);
                          } else {
                            void changePolicy(col.name, next as ColumnPermissionType);
                          }
                        }}
                        disabled={savingKey !== null}
                        className={selectClass}
                      >
                        <option value="NONE">Sin política</option>
                        <option value="ALLOWED">Permitida</option>
                        <option value="MASKED">Enmascarada</option>
                        <option value="BLOCKED">Bloqueada</option>
                      </select>
                      {isSavingThis && (
                        <span className="text-[9px] text-slate-500 dark:text-gray-400">guardando…</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <p className="text-[10px] text-slate-500 dark:text-gray-400 leading-relaxed">
            <strong>Sin política</strong> borra la fila y devuelve la columna al default (legible), que es la única forma
            de deshacer una excepción <strong>Permitida</strong>. <strong>Permitida</strong> deja la columna legible y,
            si el seed la había bloqueado, registra la excepción. <strong>Enmascarada</strong> se consulta pero el valor
            sale ofuscado. <strong>Bloqueada</strong> no se devuelve ni se ofrece al modelo. La política se aplica por
            nombre de columna en toda la conexión: si dos tablas comparten el nombre, gana la más restrictiva (Bloqueada
            &gt; Enmascarada &gt; Permitida).
          </p>
        </>
      )}
    </div>
  );
};
