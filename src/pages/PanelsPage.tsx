import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlignLeft,
  ArrowDown,
  ArrowUp,
  BarChart3,
  CircleDot,
  Database,
  Download,
  Eye,
  Gauge,
  Hash,
  Layers,
  LineChart,
  Loader2,
  Mountain,
  Pencil,
  PieChart,
  Plus,
  RefreshCw,
  Save,
  ScatterChart,
  SlidersHorizontal,
  Sparkles,
  Table2,
  Trash2,
  X,
} from 'lucide-react';
import { useNotifications } from '../context/NotificationContext';
import { getApiErrorMessage } from '../shared/api/api_client';
import { downloadBlob } from '../shared/download';
import { biService, BIVisualCreateBody } from '../features/bi/services/bi_service';
import {
  BIChartType,
  BIColumn,
  BIConnection,
  BIFilter,
  BIPanel,
  BIPanelFilter,
  BIQueryResult,
  BISchema,
  BISuggestion,
  BIVisual,
  BIVisualSpec,
} from '../features/bi/types';
import {
  AGGREGATIONS,
  FILTER_OPS,
  SORTS,
  chartNeedsNoDimension,
  csvFilename,
  describeVisualSpec,
  emptyVisualSpec,
  visualToCsv,
} from '../features/bi/panels_logic';
import { VisualRenderer } from '../features/bi/components/VisualRenderer';

const CHART_TYPES: Array<{ id: BIChartType; label: string; icon: React.FC<{ className?: string }> }> = [
  { id: 'bar', label: 'Columnas', icon: BarChart3 },
  { id: 'hbar', label: 'Barras', icon: AlignLeft },
  { id: 'line', label: 'Líneas', icon: LineChart },
  { id: 'area', label: 'Área', icon: Mountain },
  { id: 'pie', label: 'Circular', icon: PieChart },
  { id: 'donut', label: 'Dona', icon: CircleDot },
  { id: 'scatter', label: 'Dispersión', icon: ScatterChart },
  { id: 'stacked', label: 'Apiladas', icon: Layers },
  { id: 'kpi', label: 'Tarjeta', icon: Hash },
  { id: 'gauge', label: 'Gauge', icon: Gauge },
  { id: 'table', label: 'Tabla', icon: Table2 },
];

const inputCls =
  'w-full rounded-lg border border-app-border bg-app-surface px-2.5 py-1.5 text-xs text-app-text focus:outline-none focus:ring-2 focus:ring-brand-500';
const labelCls = 'block text-[11px] font-semibold text-app-muted mb-1';

const SavedVisualCard: React.FC<{
  visual: BIVisual;
  connectionName: string;
  refreshKey: number;
  onDelete: (visual: BIVisual) => void;
  onEdit: (visual: BIVisual) => void;
  onMoveUp: (visual: BIVisual) => void;
  onMoveDown: (visual: BIVisual) => void;
}> = ({ visual, connectionName, refreshKey, onDelete, onEdit, onMoveUp, onMoveDown }) => {
  const [result, setResult] = useState<BIQueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showSql, setShowSql] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await biService.visualData(visual.id));
    } catch (err) {
      setError(getApiErrorMessage(err, 'No se pudo cargar este visual.', true));
    } finally {
      setLoading(false);
    }
  }, [visual.id]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // Exporta EXACTAMENTE lo que ya está en pantalla (mismo endpoint auditado).
  // El escape de fórmulas vive en `csvCell`, igual que el backend en Excel.
  const downloadCsv = () => {
    if (!result) return;
    const csv = `\uFEFF${visualToCsv(result.rows)}`;
    downloadBlob(
      new Blob([csv], { type: 'text/csv;charset=utf-8' }),
      csvFilename(visual.title),
    );
  };

  return (
    <div className="rounded-2xl border border-app-border bg-app-card p-4 shadow-sm flex flex-col h-full">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-app-text truncate">{visual.title}</h3>
          <p className="text-[11px] text-app-muted truncate">
            {connectionName} · {visual.spec.table}
          </p>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            type="button"
            onClick={() => onEdit(visual)}
            title="Editar visual"
            aria-label="Editar visual"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onMoveUp(visual)}
            title="Mover arriba en el panel"
            aria-label="Mover arriba en el panel"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
          >
            <ArrowUp className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onMoveDown(visual)}
            title="Mover abajo en el panel"
            aria-label="Mover abajo en el panel"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
          >
            <ArrowDown className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setShowSql((value) => !value)}
            title="Ver consulta"
            aria-label="Ver consulta"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
          >
            <Eye className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={downloadCsv}
            disabled={!result}
            title="Descargar los datos que ves en CSV"
            aria-label="Descargar CSV"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover disabled:opacity-40"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => void load()}
            title="Actualizar"
            aria-label="Actualizar visual"
            className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => onDelete(visual)}
            title="Quitar del panel"
            aria-label="Quitar del panel"
            className="p-1.5 rounded-lg text-app-muted hover:bg-rose-500/10 hover:text-rose-500"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {showSql && result && (
        <pre className="mb-2 max-h-24 overflow-auto rounded-lg bg-app-surface p-2 text-[10px] text-app-muted whitespace-pre-wrap">
          {result.sql}
        </pre>
      )}

      <div className="flex-1">
        {loading && !result ? (
          <div className="flex items-center justify-center text-app-muted" style={{ height: 260 }}>
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-center px-4 text-center text-xs text-rose-600 dark:text-rose-400" style={{ height: 260 }}>
            {error}
          </div>
        ) : result ? (
          <VisualRenderer
            spec={visual.spec}
            result={result}
            height={visual.spec.size === 'full' ? 320 : 260}
          />
        ) : null}
      </div>
    </div>
  );
};

// Filtros compartidos de la pestaña activa: cada uno declara base y tabla, y
// el backend los mergea al ejecutar cada visual que coincida, con los
// permisos de hoy. No aplican a la vista previa del constructor: ahí se edita
// un visual, no el panel. Columnas confidenciales: ni se ofrecen (no-admin).
const PanelFiltersBar: React.FC<{
  connections: BIConnection[];
  filters: BIPanelFilter[];
  schemas: Record<number, BISchema>;
  saving: boolean;
  onChange: (filters: BIPanelFilter[]) => void;
  onEnsureSchema: (connectionId: number) => Promise<BISchema | null>;
  onSave: () => void;
}> = ({ connections, filters, schemas, saving, onChange, onEnsureSchema, onSave }) => {
  const update = (index: number, patch: Partial<BIPanelFilter>) =>
    onChange(
      filters.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );

  const setConnection = (index: number, connectionId: number) => {
    void onEnsureSchema(connectionId).then((schema) => {
      const table = schema?.tables[0];
      update(index, {
        connection_id: connectionId,
        table: table?.name ?? '',
        column: table?.columns.find((column) => !column.masked)?.name ?? '',
      });
    });
  };

  const setTable = (index: number, table: string, schema?: BISchema) => {
    const columns =
      schema?.tables.find((item) => item.name === table)?.columns ?? [];
    update(index, {
      table,
      column: columns.find((column) => !column.masked)?.name ?? '',
    });
  };

  const addFilter = () => {
    if (filters.length >= 5) return;
    const preferred = connections.find((c) => c.is_active) ?? connections[0];
    if (!preferred) return;
    void onEnsureSchema(preferred.id).then((schema) => {
      const table = schema?.tables[0];
      onChange([
        ...filters,
        {
          connection_id: preferred.id,
          table: table?.name ?? '',
          column: table?.columns.find((column) => !column.masked)?.name ?? '',
          op: '=',
          value: '',
        },
      ]);
    });
  };

  return (
    <div className="rounded-2xl border border-app-border bg-app-card p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-app-muted">
          Filtros de la pestaña: aplican a todos los visuales cuya base y tabla coincidan.
        </p>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={addFilter}
            disabled={!connections.length || filters.length >= 5}
            className="text-[11px] font-semibold text-brand-600 disabled:opacity-40"
          >
            + Agregar filtro
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand-600 text-white text-[11px] font-semibold hover:bg-brand-700 disabled:opacity-40"
          >
            {saving ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Save className="w-3 h-3" />
            )}
            Guardar filtros
          </button>
        </div>
      </div>
      {filters.length === 0 ? (
        <p className="text-[11px] text-app-muted">
          Sin filtros compartidos: cada visual usa solo sus filtros propios.
        </p>
      ) : (
        <div className="space-y-1.5">
          {filters.map((filter, index) => {
            const schema = schemas[filter.connection_id];
            const columns =
              schema?.tables.find((table) => table.name === filter.table)
                ?.columns ?? [];
            return (
              <div
                key={index}
                className="grid grid-cols-2 lg:grid-cols-[1.4fr_1.4fr_1.4fr_90px_1fr_auto] gap-1"
              >
                <select
                  aria-label="Base del filtro de pestaña"
                  className={inputCls}
                  value={filter.connection_id || ''}
                  onChange={(event) =>
                    setConnection(index, Number(event.target.value))
                  }
                >
                  <option value="">Base…</option>
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Tabla del filtro de pestaña"
                  className={inputCls}
                  value={filter.table}
                  disabled={!schema}
                  onChange={(event) => setTable(index, event.target.value, schema)}
                >
                  <option value="">{schema ? 'Tabla…' : 'Cargando…'}</option>
                  {schema?.tables.map((table) => (
                    <option key={table.name} value={table.name}>
                      {table.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Columna del filtro de pestaña"
                  className={inputCls}
                  value={filter.column}
                  disabled={!columns.length}
                  onChange={(event) => update(index, { column: event.target.value })}
                >
                  <option value="">Columna…</option>
                  {columns
                    .filter((column) => !column.masked)
                    .map((column) => (
                      <option key={column.name} value={column.name}>
                        {column.name}
                      </option>
                    ))}
                </select>
                <select
                  aria-label="Condición del filtro de pestaña"
                  className={inputCls}
                  value={filter.op}
                  onChange={(event) =>
                    update(index, { op: event.target.value as BIPanelFilter['op'] })
                  }
                >
                  {FILTER_OPS.map((operator) => (
                    <option key={operator.id} value={operator.id}>
                      {operator.label}
                    </option>
                  ))}
                </select>
                <input
                  aria-label="Valor del filtro de pestaña"
                  className={inputCls}
                  value={filter.value}
                  placeholder="valor"
                  onChange={(event) => update(index, { value: event.target.value })}
                />
                <button
                  type="button"
                  aria-label="Quitar filtro de pestaña"
                  className="px-1.5 text-app-muted hover:text-rose-500"
                  onClick={() =>
                    onChange(filters.filter((_, itemIndex) => itemIndex !== index))
                  }
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export const PanelsPage: React.FC = () => {
  const { notify } = useNotifications();
  const [connections, setConnections] = useState<BIConnection[]>([]);
  const [visuals, setVisuals] = useState<BIVisual[]>([]);
  const [panels, setPanels] = useState<BIPanel[]>([]);
  const [activePanel, setActivePanel] = useState('Mi panel');
  const [refreshKey, setRefreshKey] = useState(0);

  // Gestión de pestañas: renombrar en línea, borrar y filtros compartidos.
  const [renamingPanel, setRenamingPanel] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [showPanelFilters, setShowPanelFilters] = useState(false);
  const [panelFiltersDraft, setPanelFiltersDraft] = useState<BIPanelFilter[]>([]);
  const [panelSchemas, setPanelSchemas] = useState<Record<number, BISchema>>({});
  const [savingPanelFilters, setSavingPanelFilters] = useState(false);

  const [builderOpen, setBuilderOpen] = useState(false);
  const [editing, setEditing] = useState<BIVisual | null>(null);
  const [schema, setSchema] = useState<BISchema | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [spec, setSpec] = useState<BIVisualSpec>(emptyVisualSpec(0));
  const [preview, setPreview] = useState<BIQueryResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [title, setTitle] = useState('');
  const [panelForSave, setPanelForSave] = useState('Mi panel');
  const [saving, setSaving] = useState(false);

  const [isCreatingPanel, setIsCreatingPanel] = useState(false);
  const [newPanelName, setNewPanelName] = useState('');
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null);
  const previewRequest = useRef(0);

  // Sugerencias IA dentro del constructor.
  const [suggesting, setSuggesting] = useState(false);
  const [suggestions, setSuggestions] = useState<BISuggestion[]>([]);
  const [suggestError, setSuggestError] = useState<string | null>(null);

  const loadVisuals = useCallback(async () => {
    try {
      setVisuals(await biService.listVisuals());
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudieron cargar tus paneles.'));
    }
  }, [notify]);

  const loadPanels = useCallback(async () => {
    try {
      setPanels(await biService.listPanels());
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudieron cargar tus paneles.'));
    }
  }, [notify]);

  useEffect(() => {
    void (async () => {
      try {
        setConnections(await biService.listConnections());
      } catch (err) {
        notify('error', getApiErrorMessage(err, 'No se pudieron cargar las bases de datos.'));
      }
      void loadPanels();
      void loadVisuals();
    })();
  }, [notify, loadPanels, loadVisuals]);

  const panelNames = useMemo(() => {
    // Unión defensiva: el backend ya une filas de `bi_panels` con los nombres
    // de los visuales, pero visuals y panels cargan por separado.
    const names = Array.from(
      new Set([
        ...panels.map((panel) => panel.name),
        ...visuals.map((visual) => visual.panel_name),
      ]),
    );
    if (!names.includes(activePanel)) names.unshift(activePanel);
    return names;
  }, [panels, visuals, activePanel]);

  // Al cambiar de pestaña (o al recargar la lista), el borrador de filtros
  // arranca desde lo que tiene guardado la pestaña activa.
  useEffect(() => {
    const current = panels.find((panel) => panel.name === activePanel);
    setPanelFiltersDraft(current?.filters ?? []);
  }, [activePanel, panels]);

  const panelVisuals = useMemo(
    () =>
      visuals
        .filter((visual) => visual.panel_name === activePanel)
        .sort((a, b) => a.position - b.position || a.id - b.id),
    [visuals, activePanel],
  );
  const connectionName = (id: number) =>
    connections.find((connection) => connection.id === id)?.name ?? `Base ${id}`;
  const activeFilterCount =
    panels.find((panel) => panel.name === activePanel)?.filters.length ?? 0;

  const selectConnection = async (id: number, preset?: BIVisualSpec) => {
    setSpec(emptyVisualSpec(id));
    setPreview(null);
    setPreviewError(null);
    setSchema(null);
    if (!id) return;
    setSchemaLoading(true);
    try {
      const nextSchema = await biService.getSchema(id);
      setSchema(nextSchema);
      // Al editar, el visual existente se carga tal cual; si su tabla o alguna
      // columna ya no está permitida, la vista previa lo dirá y el usuario la
      // corrige antes de guardar.
      setSpec(preset ?? emptyVisualSpec(id));
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudo leer la estructura de la base.'));
    } finally {
      setSchemaLoading(false);
    }
  };

  const currentTable = schema?.tables.find((table) => table.name === spec.table);
  const columns: BIColumn[] = currentTable?.columns ?? [];
  const colByName = (name?: string | null): BIColumn | undefined =>
    columns.find((column) => column.name === name);
  const dimensionCol = colByName(spec.dimension);
  const measureCol = colByName(spec.measure);

  const update = (patch: Partial<BIVisualSpec>) =>
    setSpec((current) => ({ ...current, ...patch }));

  const openBuilder = () => {
    setEditing(null);
    setBuilderOpen(true);
    setTitle('');
    setPanelForSave(activePanel);
    if (!schema && connections.length > 0) {
      const preferred = connections.find((connection) => connection.is_active) ?? connections[0];
      void selectConnection(preferred.id);
    }
  };

  const openEdit = (visual: BIVisual) => {
    setEditing(visual);
    setBuilderOpen(true);
    setTitle(visual.title);
    setPanelForSave(visual.panel_name);
    if (!schema || schema.connection_id !== visual.spec.connection_id) {
      void selectConnection(visual.spec.connection_id, visual.spec);
    } else {
      setSpec(visual.spec);
    }
  };

  useEffect(() => {
    if (!builderOpen || !spec.connection_id || !spec.table) return;
    if (!chartNeedsNoDimension(spec.chart_type) && !spec.dimension) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const requestId = ++previewRequest.current;
    const handle = window.setTimeout(async () => {
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const result = await biService.preview(
          chartNeedsNoDimension(spec.chart_type) ? { ...spec, dimension: null } : spec,
        );
        if (requestId === previewRequest.current) setPreview(result);
      } catch (err) {
        if (requestId === previewRequest.current) {
          setPreview(null);
          setPreviewError(getApiErrorMessage(err, 'No se pudo calcular el visual.', true));
        }
      } finally {
        if (requestId === previewRequest.current) setPreviewLoading(false);
      }
    }, 350);
    return () => window.clearTimeout(handle);
  }, [spec, builderOpen]);

  const handleSave = async () => {
    const finalTitle = title.trim() || describeVisualSpec(spec);
    const finalPanel = panelForSave.trim() || 'Mi panel';
    setSaving(true);
    try {
      const toSave =
        chartNeedsNoDimension(spec.chart_type)
          ? { ...spec, dimension: null, series: null }
          : spec.chart_type === 'stacked'
            ? spec
            : { ...spec, series: null };
      const body: BIVisualCreateBody = {
        title: finalTitle,
        panel_name: finalPanel,
        spec: toSave,
      };
      if (editing) {
        await biService.updateVisual(editing.id, body);
        notify('success', `"${finalTitle}" se actualizó.`);
      } else {
        await biService.saveVisual(body);
        notify('success', `"${finalTitle}" se agregó al panel "${finalPanel}".`);
      }
      setActivePanel(finalPanel);
      setEditing(null);
      setBuilderOpen(false);
      await loadVisuals();
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudo guardar el visual.', true));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (visual: BIVisual) => {
    if (!window.confirm(`¿Quitar "${visual.title}" del panel?`)) return;
    try {
      await biService.deleteVisual(visual.id);
      setVisuals((list) => list.filter((item) => item.id !== visual.id));
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudo quitar el visual.', true));
    }
  };

  // El orden se aplica optimista en la UI y se persiste en un solo PUT con la
  // lista COMPLETA del panel: si falla la red, se recarga el orden del servidor
  // y no queda un estado intermedio.
  const commitOrder = (ordered: BIVisual[]) => {
    const ids = ordered.map((visual) => visual.id);
    setVisuals((current) => {
      const others = current.filter((visual) => visual.panel_name !== activePanel);
      const reordered = ordered.map((visual, index) => ({ ...visual, position: index }));
      return [...others, ...reordered];
    });
    void (async () => {
      try {
        await biService.reorderVisuals(activePanel, ids);
      } catch (err) {
        notify('error', getApiErrorMessage(err, 'No se pudo guardar el orden del panel.', true));
        await loadVisuals();
      }
    })();
  };

  const moveVisual = (visualId: number, direction: -1 | 1) => {
    const list = [...panelVisuals];
    const index = list.findIndex((visual) => visual.id === visualId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= list.length) return;
    const [moved] = list.splice(index, 1);
    list.splice(target, 0, moved);
    commitOrder(list);
  };

  const handleDragStart = (event: React.DragEvent<HTMLDivElement>, visualId: number) => {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(visualId));
    setDragId(visualId);
  };

  const handleDrop = (targetId: number) => {
    if (!dragId || dragId === targetId) return;
    const list = [...panelVisuals];
    const from = list.findIndex((visual) => visual.id === dragId);
    const to = list.findIndex((visual) => visual.id === targetId);
    if (from < 0 || to < 0) return;
    const [moved] = list.splice(from, 1);
    list.splice(to, 0, moved);
    setDragId(null);
    setDragOverId(null);
    commitOrder(list);
  };

  const confirmCreatePanel = () => {
    const name = newPanelName.trim().slice(0, 120);
    if (name) setActivePanel(name);
    setNewPanelName('');
    setIsCreatingPanel(false);
  };

  // La pestaña activa "existe" si tiene fila propia, visuales o ambos. Una
  // pestaña recién nombrada (sin guardar nada) es un fantasma local: renombrar
  // o eliminarla daría 404 del servidor.
  const activePanelExists =
    panels.some((panel) => panel.name === activePanel) ||
    visuals.some((visual) => visual.panel_name === activePanel);

  const startRename = () => {
    setRenameValue(activePanel);
    setRenamingPanel(true);
  };

  const doRename = async () => {
    const newName = renameValue.trim().slice(0, 120);
    setRenamingPanel(false);
    if (!newName || newName === activePanel) return;
    try {
      setPanels(await biService.renamePanel(activePanel, newName));
      setActivePanel(newName);
      await loadVisuals();
      notify('success', `La pestaña ahora se llama "${newName}".`);
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudo renombrar la pestaña.'));
    }
  };

  const doDeletePanel = async () => {
    if (
      !window.confirm(
        `¿Eliminar la pestaña "${activePanel}" con todos sus visuales y filtros?`,
      )
    )
      return;
    try {
      await biService.deletePanel(activePanel);
      const remaining = panelNames.filter((name) => name !== activePanel);
      setActivePanel(remaining[0] ?? 'Mi panel');
      await Promise.all([loadPanels(), loadVisuals()]);
      notify('success', `Pestaña "${activePanel}" eliminada.`);
    } catch (err) {
      notify('error', getApiErrorMessage(err, 'No se pudo eliminar la pestaña.'));
    }
  };

  const ensurePanelSchema = useCallback(
    async (connectionId: number): Promise<BISchema | null> => {
      const cached = panelSchemas[connectionId];
      if (cached) return cached;
      try {
        const schema = await biService.getSchema(connectionId);
        setPanelSchemas((current) => ({ ...current, [connectionId]: schema }));
        return schema;
      } catch (err) {
        notify(
          'error',
          getApiErrorMessage(err, 'No se pudo leer la estructura de la base.'),
        );
        return null;
      }
    },
    [panelSchemas, notify],
  );

  const savePanelFilters = async () => {
    setSavingPanelFilters(true);
    try {
      setPanels(await biService.updatePanelFilters(activePanel, panelFiltersDraft));
      // Recalcula todas las tarjetas: cada una re-ejecuta con el merge nuevo.
      setRefreshKey((key) => key + 1);
      notify('success', 'Filtros de la pestaña guardados.');
    } catch (err) {
      notify(
        'error',
        getApiErrorMessage(err, 'No se pudieron guardar los filtros de la pestaña.', true),
      );
    } finally {
      setSavingPanelFilters(false);
    }
  };

  const canPreview =
    Boolean(spec.table) &&
    (chartNeedsNoDimension(spec.chart_type) || Boolean(spec.dimension));

  // --- Sugerencias IA: el backend valida cada spec contra el esquema
  // autorizado; aquí solo se muestra lo que volvió y se precarga el
  // constructor al hacer clic. Nada se guarda sin acción del usuario.
  const runSuggest = async () => {
    if (!spec.connection_id || !spec.table) return;
    setSuggesting(true);
    setSuggestError(null);
    setSuggestions([]);
    try {
      const out = await biService.suggestVisuals(spec.connection_id, spec.table);
      setSuggestions(out.suggestions);
      if (!out.suggestions.length) {
        setSuggestError('La IA no encontró visualizaciones útiles para esta tabla.');
      }
    } catch (err) {
      setSuggestError(
        getApiErrorMessage(err, 'No se pudieron generar sugerencias.', true),
      );
    } finally {
      setSuggesting(false);
    }
  };

  const applySuggestion = (suggestion: BISuggestion) => {
    // La sugerencia viene validada para la conexión y tabla actuales.
    setSpec({ ...suggestion.spec, connection_id: spec.connection_id, table: spec.table });
    setTitle(suggestion.title);
    setSuggestions([]);
    setSuggestError(null);
  };
  const maskedMeasure = Boolean(measureCol?.masked && !schema?.is_admin);
  const aggregationOptions = AGGREGATIONS.filter(
    (aggregation) => !aggregation.numericOnly || measureCol?.kind === 'number',
  ).filter((aggregation) => !maskedMeasure || ['count', 'count_distinct'].includes(aggregation.id));

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--app-bg)]">
      <div className="max-w-[1440px] mx-auto px-3 sm:px-5 py-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-extrabold text-app-text">Paneles</h1>
            <p className="text-xs text-app-muted">
              Crea, edita y ordena gráficos con cualquier base incorporada. Se recalculan con los datos y permisos
              actuales cada vez que abres el panel.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setRefreshKey((key) => key + 1)}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-app-border text-xs font-semibold text-app-text hover:bg-app-hover"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Actualizar todo
            </button>
            <button
              type="button"
              onClick={openBuilder}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-brand-600 text-white text-xs font-semibold hover:bg-brand-700"
            >
              <Plus className="w-3.5 h-3.5" /> Nuevo visual
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Paneles">
          {panelNames.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={name === activePanel}
              onClick={() => setActivePanel(name)}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
                name === activePanel
                  ? 'bg-brand-600 text-white border-brand-600'
                  : 'border-app-border text-app-muted hover:text-app-text hover:bg-app-hover'
              }`}
            >
              {name}
            </button>
          ))}
          {!isCreatingPanel ? (
            <button
              type="button"
              onClick={() => setIsCreatingPanel(true)}
              className="px-3 py-1.5 rounded-full text-xs font-semibold border border-dashed border-app-border text-app-muted hover:text-app-text"
            >
              + Panel
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <input
                value={newPanelName}
                onChange={(event) => setNewPanelName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') confirmCreatePanel();
                  if (event.key === 'Escape') setIsCreatingPanel(false);
                }}
                placeholder="Nombre del panel"
                aria-label="Nombre del nuevo panel"
                maxLength={120}
                className="rounded-full border border-app-border bg-app-surface px-3 py-1.5 text-xs text-app-text focus:outline-none focus:ring-2 focus:ring-brand-500"
                autoFocus
              />
              <button
                type="button"
                onClick={confirmCreatePanel}
                className="px-3 py-1.5 rounded-full bg-brand-600 text-white text-xs font-semibold"
              >
                Crear
              </button>
              <button
                type="button"
                onClick={() => setIsCreatingPanel(false)}
                aria-label="Cancelar nuevo panel"
                className="p-1.5 rounded-full text-app-muted hover:bg-app-hover"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>

        {renamingPanel ? (
          <div className="flex items-center gap-1">
            <input
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void doRename();
                if (event.key === 'Escape') setRenamingPanel(false);
              }}
              placeholder="Nombre nuevo de la pestaña"
              aria-label="Nombre nuevo de la pestaña"
              maxLength={120}
              autoFocus
              className="rounded-full border border-app-border bg-app-surface px-3 py-1.5 text-xs text-app-text focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
            <button
              type="button"
              onClick={() => void doRename()}
              className="px-3 py-1.5 rounded-full bg-brand-600 text-white text-xs font-semibold"
            >
              Renombrar
            </button>
            <button
              type="button"
              onClick={() => setRenamingPanel(false)}
              aria-label="Cancelar renombrado"
              className="p-1.5 rounded-full text-app-muted hover:bg-app-hover"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={startRename}
              disabled={!activePanelExists}
              title="Renombrar la pestaña activa"
              aria-label="Renombrar la pestaña activa"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border border-app-border text-[11px] font-semibold text-app-text hover:bg-app-hover disabled:opacity-40"
            >
              <Pencil className="w-3.5 h-3.5" /> Renombrar
            </button>
            <button
              type="button"
              onClick={() => void doDeletePanel()}
              disabled={!activePanelExists}
              title="Eliminar la pestaña activa, con todos sus visuales y filtros"
              aria-label="Eliminar la pestaña activa"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border border-app-border text-[11px] font-semibold text-app-text hover:bg-rose-500/10 hover:text-rose-500 disabled:opacity-40"
            >
              <Trash2 className="w-3.5 h-3.5" /> Eliminar
            </button>
            <button
              type="button"
              onClick={() => setShowPanelFilters((value) => !value)}
              title="Filtros compartidos de la pestaña activa"
              aria-label="Filtros compartidos de la pestaña activa"
              aria-expanded={showPanelFilters}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border border-app-border text-[11px] font-semibold text-app-text hover:bg-app-hover"
            >
              <SlidersHorizontal className="w-3.5 h-3.5" /> Filtros
              {activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
            </button>
          </div>
        )}

        {showPanelFilters && (
          <PanelFiltersBar
            connections={connections}
            filters={panelFiltersDraft}
            schemas={panelSchemas}
            saving={savingPanelFilters}
            onChange={setPanelFiltersDraft}
            onEnsureSchema={ensurePanelSchema}
            onSave={() => void savePanelFilters()}
          />
        )}

        {builderOpen && (
          <div className="rounded-2xl border border-brand-500/40 bg-app-card p-4 shadow-sm">
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-sm font-bold text-app-text">
                {editing ? `Editar visual · ${editing.title}` : 'Nuevo visual'}
              </h2>
              <button
                type="button"
                onClick={() => {
                  setBuilderOpen(false);
                  setEditing(null);
                }}
                aria-label="Cerrar constructor"
                className="p-1.5 rounded-lg text-app-muted hover:bg-app-hover"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-[11px] text-app-muted mb-3">
              {editing
                ? 'Los cambios se recalculan con tus permisos de hoy antes de guardarse.'
                : 'Elige tabla, campos y agregación; la vista previa se calcula en vivo.'}
            </p>

            <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-5">
              <div className="space-y-3">
                <div>
                  <label className={labelCls} htmlFor="bi-conn">1. Base de datos</label>
                  <select
                    id="bi-conn"
                    className={inputCls}
                    value={spec.connection_id || ''}
                    onChange={(event) => void selectConnection(Number(event.target.value))}
                  >
                    <option value="">Elige una base…</option>
                    {connections.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name}{connection.is_active ? '' : ' (inactiva)'}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls} htmlFor="bi-table">2. Tabla</label>
                  <select
                    id="bi-table"
                    className={inputCls}
                    value={spec.table}
                    disabled={!schema || schemaLoading}
                    onChange={(event) =>
                      update({
                        table: event.target.value,
                        dimension: null,
                        measure: null,
                        aggregation: 'count',
                        filters: [],
                        date_grain: null,
                      })
                    }
                  >
                    <option value="">
                      {schemaLoading
                        ? 'Cargando…'
                        : schema && schema.tables.length === 0
                          ? 'Sin tablas permitidas para tu perfil'
                          : 'Elige una tabla…'}
                    </option>
                    {schema?.tables.map((table) => (
                      <option key={table.name} value={table.name}>{table.name}</option>
                    ))}
                  </select>
                </div>

                {spec.table && (
                  <div>
                    <button
                      type="button"
                      onClick={() => void runSuggest()}
                      disabled={suggesting}
                      className="flex items-center gap-1.5 w-full px-3 py-2 rounded-lg border border-brand-500/40 text-[11px] font-semibold text-brand-600 dark:text-brand-300 hover:bg-brand-500/10 disabled:opacity-40"
                    >
                      {suggesting ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="w-3.5 h-3.5" />
                      )}
                      {suggesting ? 'Pensando…' : 'Sugerir visuales (IA local)'}
                    </button>
                    {suggestError && (
                      <p className="mt-1 text-[10px] text-rose-600 dark:text-rose-400">
                        {suggestError}
                      </p>
                    )}
                    {suggestions.length > 0 && (
                      <div className="mt-1.5 space-y-1">
                        {suggestions.map((suggestion) => (
                          <button
                            key={`${suggestion.title}|${suggestion.spec.chart_type}|${suggestion.spec.dimension ?? ''}`}
                            type="button"
                            onClick={() => applySuggestion(suggestion)}
                            title="Cargar en el constructor"
                            className="block w-full text-left px-2.5 py-1.5 rounded-lg border border-app-border text-[11px] hover:bg-app-hover"
                          >
                            <span className="font-semibold text-app-text">{suggestion.title}</span>
                            <span className="text-app-muted">
                              {' · '}
                              {describeVisualSpec(suggestion.spec)}
                            </span>
                          </button>
                        ))}
                        <p className="text-[10px] text-app-muted">
                          Toca una sugerencia para cargarla; se guarda solo si tú decides.
                        </p>
                      </div>
                    )}
                  </div>
                )}

                <div>
                  <span className={labelCls}>3. Tipo de visual</span>
                  <div className="grid grid-cols-4 gap-1.5">
                    {CHART_TYPES.map(({ id, label, icon: Icon }) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() =>
                          update({
                            chart_type: id,
                            sort: id === 'line' || id === 'area' ? 'label_asc' : spec.sort,
                            // La serie solo tiene sentido apilada: al cambiar
                            // de tipo se limpia para no guardar un spec que el
                            // backend agruparía por serie sin querer.
                            series: id === 'stacked' ? (spec.series ?? null) : null,
                          })
                        }
                        aria-pressed={spec.chart_type === id}
                        className={`flex flex-col items-center gap-1 py-2 rounded-lg border text-[10px] font-semibold transition-colors ${
                          spec.chart_type === id
                            ? 'border-brand-500 bg-brand-500/10 text-brand-600 dark:text-brand-300'
                            : 'border-app-border text-app-muted hover:bg-app-hover'
                        }`}
                      >
                        <Icon className="w-4 h-4" />
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <span className={labelCls}>4. Tamaño en el panel</span>
                  <div className="grid grid-cols-3 gap-1.5">
                    {(
                      [
                        ['small', '⅓ Estrecho'],
                        ['wide', '⅔ Ancho'],
                        ['full', 'Completo'],
                      ] as const
                    ).map(([sizeId, label]) => (
                      <button
                        key={sizeId}
                        type="button"
                        onClick={() => update({ size: sizeId })}
                        aria-pressed={(spec.size ?? 'small') === sizeId}
                        className={`py-2 rounded-lg border text-[10px] font-semibold transition-colors ${
                          (spec.size ?? 'small') === sizeId
                            ? 'border-brand-500 bg-brand-500/10 text-brand-600 dark:text-brand-300'
                            : 'border-app-border text-app-muted hover:bg-app-hover'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {spec.chart_type !== 'kpi' && spec.chart_type !== 'gauge' && (
                  <div>
                    <label className={labelCls} htmlFor="bi-dim">5. Eje / categoría</label>
                    <select
                      id="bi-dim"
                      className={inputCls}
                      value={spec.dimension ?? ''}
                      disabled={!spec.table}
                      onChange={(event) => {
                        const column = colByName(event.target.value);
                        update({
                          dimension: event.target.value || null,
                          date_grain: column?.kind === 'date' ? 'month' : null,
                        });
                      }}
                    >
                      <option value="">Elige una columna…</option>
                      {columns.map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name}{column.masked ? ' 🔒' : ''}
                        </option>
                      ))}
                    </select>
                    {dimensionCol?.kind === 'date' && (
                      <select
                        aria-label="Agrupar fecha por"
                        className={`${inputCls} mt-1.5`}
                        value={spec.date_grain ?? ''}
                        onChange={(event) =>
                          update({ date_grain: (event.target.value || null) as BIVisualSpec['date_grain'] })
                        }
                      >
                        <option value="">Fecha exacta</option>
                        <option value="day">Por día</option>
                        <option value="month">Por mes</option>
                        <option value="year">Por año</option>
                      </select>
                    )}
                    {dimensionCol?.masked && (
                      <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-300">
                        Columna confidencial: sus valores se mostrarán enmascarados.
                      </p>
                    )}
                  </div>
                )}

                {spec.chart_type === 'stacked' && (
                  <div>
                    <label className={labelCls} htmlFor="bi-series">6. Serie (apilado)</label>
                    <select
                      id="bi-series"
                      className={inputCls}
                      value={spec.series ?? ''}
                      disabled={!spec.table}
                      onChange={(event) => update({ series: event.target.value || null })}
                    >
                      <option value="">Sin serie (barras simples)</option>
                      {columns.filter((column) => !column.masked).map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-[10px] text-app-muted">
                      Segunda dimensión: cada categoría se parte por esta columna.
                    </p>
                  </div>
                )}

                <div>
                  <label className={labelCls} htmlFor="bi-measure">
                    {spec.chart_type === 'kpi' || spec.chart_type === 'gauge'
                      ? '5'
                      : spec.chart_type === 'stacked'
                        ? '7'
                        : '6'}
                    . Valor
                  </label>
                  <div className="grid grid-cols-2 gap-1.5">
                    <select
                      id="bi-measure"
                      className={inputCls}
                      value={spec.measure ?? ''}
                      disabled={!spec.table}
                      onChange={(event) => {
                        const column = colByName(event.target.value);
                        update({
                          measure: event.target.value || null,
                          aggregation: !event.target.value
                            ? 'count'
                            : column?.kind === 'number' && !column.masked
                              ? 'sum'
                              : 'count',
                        });
                      }}
                    >
                      <option value="">Cantidad de filas</option>
                      {columns.map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name}{column.kind === 'number' ? ' (núm.)' : ''}{column.masked ? ' 🔒' : ''}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label="Agregación"
                      className={inputCls}
                      value={spec.aggregation}
                      disabled={!spec.measure}
                      onChange={(event) =>
                        update({ aggregation: event.target.value as BIVisualSpec['aggregation'] })
                      }
                    >
                      {aggregationOptions.map((aggregation) => (
                        <option key={aggregation.id} value={aggregation.id}>{aggregation.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between">
                    <span className={labelCls}>Filtros (opcional)</span>
                    <button
                      type="button"
                      disabled={!spec.table || spec.filters.length >= 5}
                      onClick={() =>
                        update({
                          filters: [
                            ...spec.filters,
                            {
                              column: columns.find((column) => !column.masked)?.name ?? '',
                              op: '=',
                              value: '',
                            },
                          ],
                        })
                      }
                      className="text-[11px] font-semibold text-brand-600 disabled:opacity-40"
                    >
                      + Agregar filtro
                    </button>
                  </div>
                  <div className="space-y-1.5">
                    {spec.filters.map((filter, index) => (
                      <div key={index} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-1">
                        <select
                          aria-label="Columna del filtro"
                          className={inputCls}
                          value={filter.column}
                          onChange={(event) =>
                            update({
                              filters: spec.filters.map((item, itemIndex) =>
                                itemIndex === index ? { ...item, column: event.target.value } : item,
                              ),
                            })
                          }
                        >
                          {columns.filter((column) => !column.masked).map((column) => (
                            <option key={column.name} value={column.name}>{column.name}</option>
                          ))}
                        </select>
                        <select
                          aria-label="Condición"
                          className={inputCls}
                          value={filter.op}
                          onChange={(event) =>
                            update({
                              filters: spec.filters.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, op: event.target.value as BIFilter['op'] }
                                  : item,
                              ),
                            })
                          }
                        >
                          {FILTER_OPS.map((operator) => (
                            <option key={operator.id} value={operator.id}>{operator.label}</option>
                          ))}
                        </select>
                        <input
                          aria-label="Valor del filtro"
                          className={inputCls}
                          value={filter.value}
                          placeholder="valor"
                          onChange={(event) =>
                            update({
                              filters: spec.filters.map((item, itemIndex) =>
                                itemIndex === index ? { ...item, value: event.target.value } : item,
                              ),
                            })
                          }
                        />
                        <button
                          type="button"
                          aria-label="Quitar filtro"
                          className="px-1.5 text-app-muted hover:text-rose-500"
                          onClick={() =>
                            update({
                              filters: spec.filters.filter((_, itemIndex) => itemIndex !== index),
                            })
                          }
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>

                {spec.chart_type !== 'kpi' && spec.chart_type !== 'gauge' && (
                  <div className="grid grid-cols-[1fr_90px] gap-1.5">
                    <div>
                      {spec.chart_type === 'stacked' ? (
                        // El SQL apilado ordena por eje y serie para que el
                        // pivot del render sea determinista; `sort` no aplica.
                        <>
                          <span className={labelCls}>Orden</span>
                          <p className="text-[10px] text-app-muted">
                            El apilado se ordena por eje y serie.
                          </p>
                        </>
                      ) : (
                        <>
                          <label className={labelCls} htmlFor="bi-sort">Orden</label>
                          <select
                            id="bi-sort"
                            className={inputCls}
                            value={spec.sort}
                            onChange={(event) =>
                              update({ sort: event.target.value as BIVisualSpec['sort'] })
                            }
                          >
                            {SORTS.map((sort) => (
                              <option key={sort.id} value={sort.id}>{sort.label}</option>
                            ))}
                          </select>
                        </>
                      )}
                    </div>
                    <div>
                      <label className={labelCls} htmlFor="bi-limit">Máx. filas</label>
                      <input
                        id="bi-limit"
                        type="number"
                        min={1}
                        max={500}
                        className={inputCls}
                        value={spec.limit}
                        onChange={(event) =>
                          update({ limit: Math.max(1, Math.min(500, Number(event.target.value) || 1)) })
                        }
                      />
                    </div>
                  </div>
                )}
              </div>

              <div className="flex flex-col min-w-0">
                <div className="flex-1 rounded-xl border border-app-border bg-app-surface/40 p-3 min-h-[300px]">
                  {!spec.connection_id ? (
                    <Placeholder text="Elige una base de datos para empezar." />
                  ) : !spec.table ? (
                    <Placeholder text="Elige una tabla." />
                  ) : !canPreview ? (
                    <Placeholder text="Elige la columna del eje para ver el gráfico." />
                  ) : previewError ? (
                    <div className="flex items-center justify-center h-[300px] text-xs text-rose-600 dark:text-rose-400 text-center px-6">
                      {previewError}
                    </div>
                  ) : preview ? (
                    <div className={previewLoading ? 'opacity-60 transition-opacity' : ''}>
                      <VisualRenderer
                        spec={chartNeedsNoDimension(spec.chart_type)
                          ? { ...spec, dimension: null }
                          : spec}
                        result={preview}
                        height={300}
                      />
                    </div>
                  ) : (
                    <div className="flex items-center justify-center h-[300px] text-app-muted">
                      <Loader2 className="w-5 h-5 animate-spin" />
                    </div>
                  )}
                </div>

                <div className="mt-3 grid grid-cols-1 sm:grid-cols-[1fr_180px_auto] gap-2 items-end">
                  <div>
                    <label className={labelCls} htmlFor="bi-title">Título</label>
                    <input
                      id="bi-title"
                      className={inputCls}
                      value={title}
                      maxLength={255}
                      placeholder={canPreview ? describeVisualSpec(spec) : 'Título del visual'}
                      onChange={(event) => setTitle(event.target.value)}
                    />
                  </div>
                  <div>
                    <label className={labelCls} htmlFor="bi-panel">Guardar en el panel</label>
                    <input
                      id="bi-panel"
                      className={inputCls}
                      value={panelForSave}
                      list="bi-panel-names"
                      maxLength={120}
                      onChange={(event) => setPanelForSave(event.target.value)}
                    />
                    <datalist id="bi-panel-names">
                      {panelNames.map((name) => <option key={name} value={name} />)}
                    </datalist>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={!canPreview || !preview || saving}
                    className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-brand-600 text-white text-xs font-semibold hover:bg-brand-700 disabled:opacity-40"
                  >
                    {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                    {editing ? 'Guardar cambios' : 'Guardar'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {panelVisuals.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-app-border p-10 text-center">
            <Database className="w-8 h-8 mx-auto text-app-muted mb-2" />
            <p className="text-sm font-semibold text-app-text">Este panel está vacío</p>
            <p className="text-xs text-app-muted mt-1">
              Pulsa <b>Nuevo visual</b> para agregar tu primer gráfico.
            </p>
          </div>
        ) : (
          <div
            className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4"
            role="list"
            aria-label="Visuales del panel"
          >
            {panelVisuals.map((visual) => (
              <div
                key={visual.id}
                role="listitem"
                draggable={panelVisuals.length > 1}
                onDragStart={(event) => handleDragStart(event, visual.id)}
                onDragOver={(event) => {
                  if (!dragId || dragId === visual.id) return;
                  event.preventDefault();
                  setDragOverId(visual.id);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  handleDrop(visual.id);
                }}
                onDragEnd={() => {
                  setDragId(null);
                  setDragOverId(null);
                }}
                className={`rounded-2xl transition-all ${
                  visual.spec.size === 'wide' ? 'md:col-span-2' : ''
                } ${
                  visual.spec.size === 'full' ? 'md:col-span-2 xl:col-span-3' : ''
                } ${
                  dragOverId === visual.id ? 'ring-2 ring-brand-500 scale-[1.01]' : ''
                } ${dragId === visual.id ? 'opacity-50' : ''}`}
              >
                <SavedVisualCard
                  visual={visual}
                  connectionName={connectionName(visual.spec.connection_id)}
                  refreshKey={refreshKey}
                  onDelete={(item) => void handleDelete(item)}
                  onEdit={openEdit}
                  onMoveUp={(item) => moveVisual(item.id, -1)}
                  onMoveDown={(item) => moveVisual(item.id, 1)}
                />
              </div>
            ))}
          </div>
        )}

        {panelVisuals.length > 1 && (
          <p className="text-[11px] text-app-muted text-center">
            Arrastra una tarjeta para reordenarla, o usa las flechas ↑ / ↓ del visual. El orden se guarda solo.
          </p>
        )}
      </div>
    </div>
  );
};

const Placeholder: React.FC<{ text: string }> = ({ text }) => (
  <div className="flex items-center justify-center h-[300px] text-xs text-app-muted text-center px-6">
    {text}
  </div>
);
