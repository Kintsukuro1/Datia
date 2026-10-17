import React, { useState } from 'react';
import { Database, Plus, Edit3, Trash2, RefreshCw, CheckCircle2, RotateCcw, Filter, UploadCloud, HardDrive, Sparkles, AlertCircle, Search, Zap, PowerOff, ShieldAlert, Brain } from 'lucide-react';
import { CorporateConnection } from '../../features/admin/services/connector_service';
import { DatabaseWizardModal } from './DatabaseWizardModal';
import { useAdminConnectors } from '../../features/admin/hooks/useAdminConnectors';

interface AdminConnectorsTabProps {
  connectors: CorporateConnection[];
  onOpenCreateModal: () => void;
  onOpenEditModal: (conn: CorporateConnection) => void;
  onDeleteConnector: (id: number, name: string) => void;
  onToggleActive: (id: number) => void;
  onResetDemoConnectors: () => void;
  onRefreshConnectors?: () => void;
}

export const AdminConnectorsTab: React.FC<AdminConnectorsTabProps> = ({
  connectors,
  onOpenCreateModal: _onOpenCreateModal,
  onOpenEditModal,
  onDeleteConnector,
  onToggleActive,
  onResetDemoConnectors,
  onRefreshConnectors,
}) => {
  const {
    filterDbType,
    setFilterDbType,
    filterStatus,
    setFilterStatus,
    searchQuery,
    setSearchQuery,
    testingId,
    testResultsMap,
    handleTestCardConnection,
    counters,
    filteredConnectors,
  } = useAdminConnectors(connectors);

  const [wizardMode, setWizardMode] = useState<'file' | 'remote'>('file');
  const [isWizardOpen, setIsWizardOpen] = useState(false);

  const handleOpenWizard = (mode: 'file' | 'remote' = 'file') => {
    setWizardMode(mode);
    setIsWizardOpen(true);
  };

  const getDbBadgeColor = (dbType: string) => {
    switch (dbType.toLowerCase()) {
      case 'postgresql':
        return 'bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/30';
      case 'mysql':
        return 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30';
      case 'mssql':
        return 'bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30';
      default:
        return 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border-cyan-500/30';
    }
  };

  // "hace X" calculado en render. Solo hay datos de los tests corridos en esta
  // sesion; sin test reciente la columna muestra "—" en lugar de inventar uno.
  const relativeTime = (testedAt: number) => {
    const s = Math.floor((Date.now() - testedAt) / 1000);
    if (s < 60) return `hace ${s} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `hace ${m} min`;
    return `hace ${Math.floor(m / 60)} h`;
  };

  return (
    <div className="glass-panel rounded-2xl sm:rounded-3xl p-5 sm:p-6 space-y-5 shadow-sm font-sans">
      {/* Header with Title and Action Controls */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <h3 className="text-sm sm:text-base font-bold text-app-text tracking-tight flex items-center gap-2">
            <Database className="w-4 h-4 sm:w-5 sm:h-5 text-brand-500" />
            <span>Fuentes de Datos Corporativas</span>
            <span className="text-[10px] font-semibold px-2.5 py-0.5 rounded-full bg-app-card text-app-muted border border-app-border">
              {counters.total} {counters.total === 1 ? 'registrada' : 'registradas'}
            </span>
          </h3>
          <p className="text-xs text-app-muted mt-0.5">
            Conexiones operativas en modo solo lectura (<code>READ ONLY</code>) con soporte para PostgreSQL, MySQL y SQL Server
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {/* Action 1: Import Local File (SQLite / Excel / CSV) */}
          <button
            type="button"
            onClick={() => handleOpenWizard('file')}
            className="flex items-center space-x-1.5 text-xs bg-brand-600 hover:bg-brand-500 text-white font-bold px-4 py-2 rounded-xl shadow-sm transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <UploadCloud className="w-4 h-4" />
            <span>Importar Archivo (SQLite / Excel / CSV)</span>
          </button>

          {/* Action 2: Connect Remote DB (PostgreSQL / MSSQL / MySQL) */}
          <button
            type="button"
            onClick={() => handleOpenWizard('remote')}
            className="flex items-center space-x-1.5 text-xs bg-app-card hover:bg-app-hover text-app-text border border-app-border font-bold px-4 py-2 rounded-xl transition-colors cursor-pointer focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <Plus className="w-4 h-4 text-brand-500" />
            <span>Conectar BD Remota (Postgres / SQL Server)</span>
          </button>
        </div>
      </div>

      {/* KPI Summary Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="rounded-xl bg-app-card border border-app-border p-4 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">Total conectadas</span>
            <Database className="w-4 h-4 text-brand-500 shrink-0" />
          </div>
          <div className="mt-2 text-2xl font-bold text-app-text font-mono tabular-nums">{counters.total}</div>
          <div className="mt-1 text-[11px] text-app-muted">Fuentes registradas</div>
        </div>

        <div className="rounded-xl bg-app-card border border-app-border p-4 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">Activas</span>
            <Zap className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          </div>
          <div className="mt-2 text-2xl font-bold text-app-text font-mono tabular-nums">{counters.active}</div>
          <div className="mt-1 text-[11px] text-app-muted flex items-center gap-1.5">
            <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            <span>Disponibles para consultas</span>
          </div>
        </div>

        <div className="rounded-xl bg-app-card border border-app-border p-4 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">Inactivas</span>
            <PowerOff className="w-4 h-4 text-app-muted shrink-0" />
          </div>
          <div className="mt-2 text-2xl font-bold text-app-text font-mono tabular-nums">{counters.inactive}</div>
          <div className="mt-1 text-[11px] text-app-muted">Deshabilitadas para consultas</div>
        </div>

        <div className="rounded-xl bg-app-card border border-app-border p-4 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-app-muted">Revisión de permisos</span>
            <ShieldAlert className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
          </div>
          <div className="mt-2 text-2xl font-bold text-app-text font-mono tabular-nums">{counters.permissionReview}</div>
          <div className="mt-1 text-[11px] text-app-muted">Permisos de tablas pendientes</div>
        </div>
      </div>

      {/* Toolbar: search + filters + reload */}
      <div className="bg-app-card border border-app-border rounded-xl p-3 flex flex-col md:flex-row md:items-center gap-3">
        <div className="flex-1 flex items-center gap-2 bg-app-bg border border-app-border rounded-lg h-10 px-3 min-w-0">
          <Search className="w-4 h-4 text-app-muted shrink-0" />
          <label htmlFor="admin-connector-search" className="sr-only">
            Buscar fuente de datos
          </label>
          <input
            id="admin-connector-search"
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filtrar por nombre, host o base de datos..."
            className="w-full bg-transparent text-xs text-app-text placeholder:text-app-muted outline-none"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Filter by DB Type */}
          <div className="flex items-center gap-1.5 text-xs bg-app-bg border border-app-border rounded-lg h-10 px-3">
            <Filter className="w-3.5 h-3.5 text-app-muted" />
            <label htmlFor="admin-filter-db-type" className="sr-only">
              Filtrar por motor de base de datos
            </label>
            <select
              id="admin-filter-db-type"
              aria-label="Filtrar por motor de base de datos"
              value={filterDbType}
              onChange={(e) => setFilterDbType(e.target.value)}
              className="bg-transparent text-app-text focus:outline-none text-xs pr-1 font-medium cursor-pointer"
            >
              <option value="ALL">Todos los Motores</option>
              <option value="postgresql">PostgreSQL</option>
              <option value="mssql">SQL Server</option>
              <option value="mysql">MySQL</option>
            </select>
          </div>

          {/* Filter by Status */}
          <div className="flex items-center gap-1.5 text-xs bg-app-bg border border-app-border rounded-lg h-10 px-3">
            <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            <label htmlFor="admin-filter-status" className="sr-only">
              Filtrar por estado
            </label>
            <select
              id="admin-filter-status"
              aria-label="Filtrar por estado"
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value as 'ALL' | 'active' | 'inactive')}
              className="bg-transparent text-app-text focus:outline-none text-xs pr-1 font-medium cursor-pointer"
            >
              <option value="ALL">Estado: Todos</option>
              <option value="active">Solo activas</option>
              <option value="inactive">Solo inactivas</option>
            </select>
          </div>

          {onRefreshConnectors && (
            <button
              type="button"
              onClick={onRefreshConnectors}
              title="Recargar fuentes de datos"
              aria-label="Recargar fuentes de datos"
              className="h-10 w-10 rounded-lg bg-app-bg hover:bg-app-hover border border-app-border text-app-muted hover:text-app-text flex items-center justify-center transition-colors cursor-pointer"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* Empty States */}
      {counters.total === 0 ? (
        <div className="text-center py-12 border border-dashed border-app-border rounded-2xl p-8 space-y-4 bg-app-bg/50">
          <div className="w-12 h-12 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-300 flex items-center justify-center mx-auto border border-brand-500/20 shadow-xs">
            <Database className="w-6 h-6" />
          </div>
          <div className="space-y-1">
            <h4 className="text-sm font-bold text-app-text">No hay fuentes de datos registradas</h4>
            <p className="text-xs text-app-muted max-w-sm mx-auto">
              Importa un archivo SQLite, Excel (.xlsx) o CSV (.csv), o registra una conexión remota a PostgreSQL, SQL Server o MySQL.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
            <button
              type="button"
              onClick={onResetDemoConnectors}
              className="flex items-center space-x-1.5 text-xs bg-app-card hover:bg-app-hover text-app-text border border-app-border px-4 py-2 rounded-xl transition-colors font-semibold cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5 text-brand-500" />
              <span>Restablecer Fuentes Demo</span>
            </button>
            <button
              type="button"
              onClick={() => handleOpenWizard('file')}
              className="flex items-center space-x-1.5 text-xs bg-brand-600 hover:bg-brand-500 text-white font-bold px-4 py-2 rounded-xl transition-colors shadow-sm cursor-pointer"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Iniciar Asistente / Wizard BD</span>
            </button>
            <button
              type="button"
              onClick={() => handleOpenWizard('file')}
              className="flex items-center space-x-1.5 text-xs bg-app-card hover:bg-app-hover text-app-text border border-app-border font-bold px-4 py-2 rounded-xl transition-colors cursor-pointer"
            >
              <UploadCloud className="w-3.5 h-3.5 text-brand-500" />
              <span>Importar Archivo</span>
            </button>
          </div>
        </div>
      ) : filteredConnectors.length === 0 ? (
        <div className="text-center py-10 border border-dashed border-app-border rounded-2xl bg-app-bg/50 space-y-1">
          <p className="text-xs font-semibold text-app-text">Ningún resultado coincide con el filtro</p>
          <p className="text-[11px] text-app-muted">Ajusta la búsqueda o los filtros para ver más conexiones.</p>
        </div>
      ) : (
        /* Dense connectors table */
        <div className="w-full overflow-hidden rounded-xl border border-app-border bg-app-surface shadow-sm">
          <div className="w-full overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[900px]">
              <thead>
                <tr className="bg-app-bg text-app-muted text-[10px] uppercase tracking-wider select-none">
                  <th className="px-4 py-2.5 font-semibold">Nombre</th>
                  <th className="px-4 py-2.5 font-semibold">Motor</th>
                  <th className="px-4 py-2.5 font-semibold">Host &amp; puerto</th>
                  <th className="px-4 py-2.5 font-semibold">Base de datos</th>
                  <th className="px-4 py-2.5 font-semibold">Estado</th>
                  <th className="px-4 py-2.5 font-semibold">Último test</th>
                  <th className="px-4 py-2.5 font-semibold text-right">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filteredConnectors.map((c, i) => {
                  const testRes = testResultsMap[c.id];
                  const badgeStyle = getDbBadgeColor(c.db_type);
                  const rowBg = i % 2 === 0 ? 'bg-app-surface' : 'bg-app-bg/50';

                  return (
                    <React.Fragment key={c.id}>
                      <tr className={`${rowBg} hover:bg-app-hover transition-colors`}>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2 min-w-0">
                            <div className="w-6 h-6 rounded flex items-center justify-center bg-brand-500/10 text-brand-600 dark:text-brand-300 shrink-0">
                              <Database className="w-3.5 h-3.5" />
                            </div>
                            <span className="text-xs font-semibold text-app-text truncate" title={c.name}>
                              {c.name}
                            </span>
                            {c.is_uploaded && (
                              <span className="text-[9px] bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30 px-1.5 py-0.5 rounded font-semibold flex items-center gap-1 shrink-0">
                                <HardDrive className="w-2.5 h-2.5" />
                                Importado
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-2.5">
                          <span className={`text-[10px] border px-2 py-0.5 rounded-md font-mono uppercase tracking-wider font-bold ${badgeStyle}`}>
                            {c.db_type}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 font-mono text-[11px] text-app-muted truncate max-w-[220px]" title={c.host}>
                          {c.host}{c.port ? `:${c.port}` : ''}
                        </td>
                        <td className="px-4 py-2.5 font-mono text-[11px] text-app-text truncate max-w-[180px]" title={c.database_name}>
                          {c.database_name}
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            type="button"
                            onClick={() => onToggleActive(c.id)}
                            title={c.is_active ? 'Desactivar esta fuente' : 'Activar esta fuente'}
                            aria-label={`${c.is_active ? 'Desactivar' : 'Activar'} ${c.name}`}
                            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-semibold transition-colors cursor-pointer ${
                              c.is_active
                                ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/20'
                                : 'bg-app-bg text-app-muted border-app-border hover:bg-app-hover'
                            }`}
                          >
                            <span aria-hidden="true" className={`w-1.5 h-1.5 rounded-full ${c.is_active ? 'bg-emerald-500' : 'bg-slate-400 dark:bg-gray-500'}`} />
                            <span>{c.is_active ? 'Activa' : 'Inactiva'}</span>
                          </button>
                        </td>
                        <td className="px-4 py-2.5 text-[11px]">
                          {testRes ? (
                            <span className={testRes.success ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-700 dark:text-rose-400'}>
                              {relativeTime(testRes.testedAt)}
                            </span>
                          ) : (
                            <span className="text-app-muted">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center justify-end gap-1">
                            <button
                              type="button"
                              onClick={() => handleTestCardConnection(c)}
                              disabled={testingId === c.id}
                              title="Probar conexión"
                              aria-label={`Probar conexión ${c.name}`}
                              className="h-7 w-7 rounded-lg flex items-center justify-center text-app-muted hover:text-brand-600 dark:hover:text-brand-300 hover:bg-brand-500/10 transition-colors disabled:opacity-60 cursor-pointer"
                            >
                              <RefreshCw className={`w-3.5 h-3.5 ${testingId === c.id ? 'animate-spin' : ''}`} />
                            </button>
                            <button
                              type="button"
                              onClick={() => onOpenEditModal(c)}
                              title="Editar conexión"
                              aria-label={`Editar conexión ${c.name}`}
                              className="h-7 w-7 rounded-lg flex items-center justify-center text-app-muted hover:text-app-text hover:bg-app-hover transition-colors cursor-pointer"
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => onDeleteConnector(c.id, c.name)}
                              title="Eliminar conexión"
                              aria-label={`Eliminar conexión ${c.name}`}
                              className="h-7 w-7 rounded-lg flex items-center justify-center text-app-muted hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 transition-colors cursor-pointer"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>

                      {/* Resultado real del test, ligado a su fila */}
                      {testRes && (
                        <tr className={rowBg}>
                          <td colSpan={7} className="px-4 pb-3 pt-0">
                            <div
                              className={`p-2.5 rounded-xl border text-[11px] flex flex-col sm:flex-row sm:items-center justify-between gap-2 animate-fadeIn ${
                                testRes.success
                                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-800 dark:text-emerald-300'
                                  : 'bg-rose-500/10 border-rose-500/30 text-rose-800 dark:text-rose-300'
                              }`}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                {testRes.success ? (
                                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                                ) : (
                                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                                )}
                                <span className="truncate font-medium">
                                  {testRes.message}
                                  {testRes.success ? ` · ${testRes.latency_ms} ms` : ''}
                                </span>
                              </div>
                              {!testRes.success && (
                                <button
                                  type="button"
                                  onClick={() => handleTestCardConnection(c)}
                                  disabled={testingId === c.id}
                                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-rose-500/40 bg-rose-500/10 hover:bg-rose-500/20 font-semibold transition-colors shrink-0 cursor-pointer disabled:opacity-60"
                                >
                                  <RefreshCw className={`w-3 h-3 ${testingId === c.id ? 'animate-spin' : ''}`} />
                                  <span>Reintentar conexión</span>
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Table footer: solo conteo real */}
          <div className="px-4 py-2.5 bg-app-bg border-t border-app-border flex items-center text-[11px] text-app-muted">
            <span>
              Mostrando <strong className="text-app-text">{filteredConnectors.length}</strong> de{' '}
              <strong className="text-app-text">{counters.total}</strong> conexiones
            </span>
          </div>
        </div>
      )}

      {/* Help banner: feature real del catalogo semantico */}
      <div className="rounded-xl bg-app-card border border-app-border p-4 flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-brand-500/10 text-brand-600 dark:text-brand-300 flex items-center justify-center shrink-0">
          <Brain className="w-5 h-5" />
        </div>
        <div className="space-y-0.5 min-w-0">
          <span className="text-xs font-semibold text-app-text">Auto-extracción de metadatos asistida por IA</span>
          <p className="text-[11px] text-app-muted leading-relaxed">
            Al importar una fuente, DATIA analiza el esquema (tablas, columnas y tipos detectados) y genera el catálogo semántico para que puedas consultar en lenguaje natural sin escribir SQL.
          </p>
        </div>
      </div>

      {/* Database Installation Wizard Modal */}
      <DatabaseWizardModal
        isOpen={isWizardOpen}
        initialMode={wizardMode}
        onClose={() => setIsWizardOpen(false)}
        onSuccess={() => {
          if (onRefreshConnectors) {
            onRefreshConnectors();
          }
        }}
      />
    </div>
  );
};
