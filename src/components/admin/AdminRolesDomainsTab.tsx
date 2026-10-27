import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Check, Layers, Pencil, Plus, RefreshCw, ShieldCheck, Trash2, X,
} from 'lucide-react';
import {
  authService, DomainCatalogItem, RoleCatalogItem,
} from '../../features/auth/services/auth_service';
import { getApiErrorMessage } from '../../shared/api/api_client';
import { useNotifications } from '../../context/NotificationContext';
import { PLATFORM_ADMIN_ROLE_NAME } from '../../constants';

/**
 * Alta, edición y baja del catálogo de roles y del de dominios.
 *
 * Los dos catálogos tienen las mismas interacciones (listar, crear, renombrar,
 * borrar), así que comparten un solo `CatalogSection` en vez de dos copias.
 * Las cuentas de uso (`user_count`, `catalog_entries_count`) vienen del
 * servidor: son la razón por la que un borrado puede fallar y la UI las muestra
 * ANTES de ofrecer el botón, no después del 409.
 */
interface CatalogRow {
  id: number;
  name: string;
  description: string;
  usage: number;
  usageLabel: string;
  deleteLockedReason?: string;
  renameLockedReason?: string;
}

interface CatalogSectionProps {
  slug: string;
  icon: React.ReactNode;
  title: string;
  hint: string;
  singular: string;
  rows: CatalogRow[];
  loaded: boolean;
  error: string | null;
  onRefresh: () => void;
  onCreate: (name: string, description: string) => Promise<void>;
  onUpdate: (id: number, name: string, description: string) => Promise<void>;
  onDelete: (row: CatalogRow) => Promise<void>;
}

const inputClass =
  'w-full bg-slate-50 dark:bg-dark-base border border-slate-300 dark:border-dark-border ' +
  'rounded-xl px-3 py-1.5 text-xs text-slate-900 dark:text-white placeholder-slate-400 ' +
  'dark:placeholder-gray-500 focus:outline-none focus:border-brand-500';

const CatalogSection: React.FC<CatalogSectionProps> = ({
  slug, icon, title, hint, singular, rows, loaded, error, onRefresh,
  onCreate, onUpdate, onDelete,
}) => {
  const [draftName, setDraftName] = useState('');
  const [draftDescription, setDraftDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = draftName.trim();
    if (!name) {
      setFormError(`El nombre del ${singular} no puede estar vacío.`);
      return;
    }
    setCreating(true);
    setFormError(null);
    try {
      await onCreate(name, draftDescription.trim());
      setDraftName('');
      setDraftDescription('');
    } catch (err) {
      setFormError(getApiErrorMessage(err, `No se pudo crear el ${singular}.`));
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (row: CatalogRow) => {
    // Un campo bloqueado no bloquea la fila entera: del rol administrador sí se
    // puede cambiar la descripción, solo su NOMBRE está congelado (el backend
    // reconoce al admin por nombre exacto). El nombre se pinta como texto en el
    // modo edición y el PATCH viaja sin cambio de nombre.
    setEditingId(row.id);
    setEditName(row.name);
    setEditDescription(row.description);
    setFormError(null);
  };

  const handleSave = async (row: CatalogRow) => {
    const name = editName.trim();
    if (!name) {
      setFormError(`El nombre del ${singular} no puede estar vacío.`);
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await onUpdate(row.id, name, editDescription.trim());
      setEditingId(null);
    } catch (err) {
      setFormError(getApiErrorMessage(err, `No se pudo actualizar el ${singular}.`));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (row: CatalogRow) => {
    if (row.deleteLockedReason) {
      setFormError(row.deleteLockedReason);
      return;
    }
    if (!window.confirm(`¿Eliminar el ${singular} '${row.name}'?`)) return;
    setFormError(null);
    try {
      await onDelete(row);
    } catch (err) {
      setFormError(getApiErrorMessage(err, `No se pudo eliminar el ${singular}.`));
    }
  };

  return (
    <section className="glass-panel rounded-2xl p-5 border border-slate-200 dark:border-white/10 space-y-4 bg-white dark:bg-zinc-900/90 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white flex items-center gap-2">
            {icon}
            {title}
          </h3>
          <p className="text-xs text-slate-600 dark:text-gray-400 mt-0.5">{hint}</p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          aria-label={`Recargar ${title.toLowerCase()}`}
          title="Recargar"
          className="p-2 rounded-xl text-slate-500 dark:text-gray-400 hover:bg-slate-100 dark:hover:bg-dark-card transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {error && (
        <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-500 text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {formError && (
        <div role="alert" className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-500 text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{formError}</span>
        </div>
      )}

      <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-[1fr_1.4fr_auto] gap-2 items-end">
        <div>
          <label htmlFor={`${slug}-new-name`} className="block text-[11px] font-medium text-slate-600 dark:text-gray-400 mb-1">
            Nombre
          </label>
          <input
            id={`${slug}-new-name`}
            type="text"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            maxLength={50}
            placeholder={singular === 'rol' ? 'Ej: Jefatura de Operaciones' : 'Ej: Logística & Cadena de Suministro'}
            className={inputClass}
            disabled={creating}
          />
        </div>
        <div>
          <label htmlFor={`${slug}-new-description`} className="block text-[11px] font-medium text-slate-600 dark:text-gray-400 mb-1">
            Descripción <span className="font-normal">(opcional)</span>
          </label>
          <input
            id={`${slug}-new-description`}
            type="text"
            value={draftDescription}
            onChange={(e) => setDraftDescription(e.target.value)}
            maxLength={500}
            placeholder="Para qué sirve este perfil o área"
            className={inputClass}
            disabled={creating}
          />
        </div>
        <button
          type="submit"
          disabled={creating}
          className="flex items-center justify-center gap-1.5 text-xs bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-xl shadow-xs transition-colors"
        >
          <Plus className="w-4 h-4" />
          <span>{creating ? 'Creando...' : `Agregar ${singular}`}</span>
        </button>
      </form>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500 dark:text-gray-500 border-b border-slate-200 dark:border-dark-border">
              <th scope="col" className="py-2 pr-3 font-bold">Nombre</th>
              <th scope="col" className="py-2 pr-3 font-bold">Descripción</th>
              <th scope="col" className="py-2 pr-3 font-bold">Uso</th>
              <th scope="col" className="py-2 text-right font-bold">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-b border-slate-100 dark:border-dark-border/60 last:border-0">
                {editingId === row.id ? (
                  <>
                    <td className="py-2 pr-3">
                      {row.renameLockedReason ? (
                        <span
                          className="block text-xs font-medium text-slate-900 dark:text-white"
                          title={row.renameLockedReason}
                        >
                          {row.name}
                        </span>
                      ) : (
                        <>
                          <label htmlFor={`${slug}-edit-name-${row.id}`} className="sr-only">Nuevo nombre</label>
                          <input
                            id={`${slug}-edit-name-${row.id}`}
                            type="text"
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            maxLength={50}
                            className={inputClass}
                            disabled={saving}
                          />
                        </>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <label htmlFor={`${slug}-edit-description-${row.id}`} className="sr-only">Nueva descripción</label>
                      <input
                        id={`${slug}-edit-description-${row.id}`}
                        type="text"
                        value={editDescription}
                        onChange={(e) => setEditDescription(e.target.value)}
                        maxLength={500}
                        className={inputClass}
                        disabled={saving}
                      />
                    </td>
                    <td className="py-2 pr-3 text-slate-500 dark:text-gray-400 whitespace-nowrap">
                      {row.usage} {row.usageLabel}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => handleSave(row)}
                        disabled={saving}
                        aria-label={`Guardar ${singular} ${row.name}`}
                        title="Guardar"
                        className="p-1.5 rounded-lg text-emerald-600 hover:bg-emerald-500/10 disabled:opacity-50"
                      >
                        <Check className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => { setEditingId(null); setFormError(null); }}
                        disabled={saving}
                        aria-label={`Cancelar edición de ${row.name}`}
                        title="Cancelar"
                        className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-dark-card disabled:opacity-50"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </td>
                  </>
                ) : (
                  <>
                    <td className="py-2 pr-3 font-medium text-slate-900 dark:text-white">{row.name}</td>
                    <td className="py-2 pr-3 text-slate-600 dark:text-gray-400">
                      {row.description || <span className="text-slate-400 dark:text-gray-600">—</span>}
                    </td>
                    <td className="py-2 pr-3 text-slate-500 dark:text-gray-400 whitespace-nowrap">
                      {row.usage} {row.usageLabel}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        aria-label={`Editar ${singular} ${row.name}`}
                        title={row.renameLockedReason ? 'Editar descripción' : 'Editar'}
                        className="p-1.5 rounded-lg text-brand-600 dark:text-brand-400 hover:bg-brand-500/10"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(row)}
                        disabled={Boolean(row.deleteLockedReason)}
                        aria-label={`Eliminar ${singular} ${row.name}`}
                        title={row.deleteLockedReason || 'Eliminar'}
                        className="p-1.5 rounded-lg text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>

        {!loaded && (
          <p className="py-3 text-xs text-slate-500 dark:text-gray-400">Cargando catálogo...</p>
        )}
        {loaded && rows.length === 0 && !error && (
          <p className="py-3 text-xs text-slate-500 dark:text-gray-400">
            Todavía no hay {singular === 'rol' ? 'roles' : 'dominios'} en el catálogo.
          </p>
        )}
      </div>
    </section>
  );
};

interface AdminRolesDomainsTabProps {
  // Se dispara tras una mutación: el panel de usuarios muestra el nombre del
  // rol y tiene que reflejar un renombrado sin recargar la página.
  onChanged?: () => void;
}

export const AdminRolesDomainsTab: React.FC<AdminRolesDomainsTabProps> = ({ onChanged }) => {
  const { notify } = useNotifications();
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [rolesLoaded, setRolesLoaded] = useState(false);
  const [rolesError, setRolesError] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainCatalogItem[]>([]);
  const [domainsLoaded, setDomainsLoaded] = useState(false);
  const [domainsError, setDomainsError] = useState<string | null>(null);

  const refreshRoles = useCallback(async () => {
    try {
      setRoles(await authService.getAvailableRoles());
      setRolesError(null);
    } catch (err) {
      setRoles([]);
      setRolesError(getApiErrorMessage(err, 'No se pudo cargar el catálogo de roles del servidor.'));
    } finally {
      setRolesLoaded(true);
    }
  }, []);

  const refreshDomains = useCallback(async () => {
    try {
      setDomains(await authService.getDomains());
      setDomainsError(null);
    } catch (err) {
      setDomains([]);
      setDomainsError(getApiErrorMessage(err, 'No se pudo cargar el catálogo de dominios del servidor.'));
    } finally {
      setDomainsLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refreshRoles();
    void refreshDomains();
  }, [refreshRoles, refreshDomains]);

  const afterChange = async () => {
    await Promise.all([refreshRoles(), refreshDomains()]);
    onChanged?.();
  };

  const roleRows: CatalogRow[] = roles.map((role) => ({
    id: role.id,
    name: role.name,
    description: role.description || '',
    usage: role.user_count ?? 0,
    usageLabel: (role.user_count ?? 0) === 1 ? 'usuario' : 'usuarios',
    deleteLockedReason: (role.user_count ?? 0) > 0
      ? `El rol tiene ${role.user_count} usuario(s) asignado(s): reasígnalos antes de eliminarlo.`
      : undefined,
    // El rol administrador se reconoce por nombre exacto (`is_admin_user`).
    // Renombrarlo apagaría el panel para todas sus cuentas.
    renameLockedReason: role.name === PLATFORM_ADMIN_ROLE_NAME
      ? 'El rol administrador de la plataforma no se puede renombrar.'
      : undefined,
  }));

  const domainRows: CatalogRow[] = domains.map((domain) => ({
    id: domain.id,
    name: domain.name,
    description: domain.description || '',
    usage: domain.catalog_entries_count ?? 0,
    usageLabel: (domain.catalog_entries_count ?? 0) === 1 ? 'entrada de catálogo' : 'entradas de catálogo',
  }));

  return (
    <div className="space-y-5">
      <div className="glass-panel rounded-2xl p-4 border border-slate-200 dark:border-white/10 bg-white dark:bg-zinc-900/90 shadow-sm">
        <p className="text-xs text-slate-600 dark:text-gray-400">
          Un rol recién creado nace <strong>sin permisos de tabla</strong>: asígnale acceso en
          <span className="font-semibold text-slate-800 dark:text-gray-200"> Permisos de Tablas</span>.
          La gobernanza de dominio decide por la matriz resultante, no por el nombre del rol.
        </p>
      </div>

      <CatalogSection
        slug="roles"
        icon={<ShieldCheck className="w-4 h-4 text-brand-600 dark:text-brand-400" />}
        title="Catálogo de roles"
        hint="Perfiles que se asignan a las cuentas. La matriz de permisos es la autoridad."
        singular="rol"
        rows={roleRows}
        loaded={rolesLoaded}
        error={rolesError}
        onRefresh={() => { void refreshRoles(); }}
        onCreate={async (name, description) => {
          await authService.createRole({ name, description: description || undefined });
          notify('success', `El rol '${name}' fue creado.`);
          await afterChange();
        }}
        onUpdate={async (id, name, description) => {
          await authService.updateRole(id, { name, description: description || null });
          notify('success', `El rol '${name}' fue actualizado.`);
          await afterChange();
        }}
        onDelete={async (row) => {
          const res = await authService.deleteRole(row.id);
          notify('success', res.message);
          await afterChange();
        }}
      />

      <CatalogSection
        slug="domains"
        icon={<Layers className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />}
        title="Dominios de negocio"
        hint="Áreas a las que se adscribe el catálogo semántico. Borrar uno deja sus entradas sin dominio."
        singular="dominio"
        rows={domainRows}
        loaded={domainsLoaded}
        error={domainsError}
        onRefresh={() => { void refreshDomains(); }}
        onCreate={async (name, description) => {
          await authService.createDomain({ name, description: description || undefined });
          notify('success', `El dominio '${name}' fue creado.`);
          await afterChange();
        }}
        onUpdate={async (id, name, description) => {
          await authService.updateDomain(id, { name, description: description || null });
          notify('success', `El dominio '${name}' fue actualizado.`);
          await afterChange();
        }}
        onDelete={async (row) => {
          const res = await authService.deleteDomain(row.id);
          notify('success', res.message);
          await afterChange();
        }}
      />
    </div>
  );
};
