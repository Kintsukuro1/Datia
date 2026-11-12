import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Send, Database, Wand2, X } from 'lucide-react';
import type { BuilderColumn, BuilderMetric, BuilderOptions } from '../../features/chat/services/query_service';
import {
  buildBuilderPreview,
  buildBuilderPrompt,
  humanizeLabel,
  type BuilderPeriod,
} from '../../features/chat/utils/query_builder';

interface ChatPromptInputProps {
  promptInput: string;
  setPromptInput: (val: string) => void;
  isGenerating: boolean;
  activeDatabaseName?: string;
  builderOptions?: BuilderOptions | null;
  builderLoading?: boolean;
  textareaRef?: React.RefObject<HTMLTextAreaElement>;
  onSubmit: (promptText?: string) => void;
}

const AUTO_CHOICE = '__auto__';
const NO_GROUPING = '__none__';

const metricKey = (metric: BuilderMetric): string => metric.column ?? '__count__';

export const ChatPromptInput: React.FC<ChatPromptInputProps> = ({
  promptInput,
  setPromptInput,
  isGenerating,
  activeDatabaseName = 'Sin fuente de datos',
  builderOptions = null,
  builderLoading = false,
  textareaRef: externalTextareaRef,
  onSubmit,
}) => {
  const internalTextareaRef = useRef<HTMLTextAreaElement>(null);
  const activeTextareaRef = externalTextareaRef || internalTextareaRef;

  // Smart Query Builder state. Las opciones salen del esquema real de la fuente
  // activa (GET /chat/suggestions -> builder), no de una lista fija: un builder
  // con "Incidentes TI" sobre una base de ventas solo produce SQL invalido.
  const [showBuilder, setShowBuilder] = useState(false);
  const [metricChoice, setMetricChoice] = useState<string>(AUTO_CHOICE);
  const [dimensionChoice, setDimensionChoice] = useState<string>(AUTO_CHOICE);
  const [period, setPeriod] = useState<BuilderPeriod>('all');

  const builderTables = builderOptions?.tables ?? [];

  // La tabla seleccionada y la métrica viven separadas a propósito: al cambiar
  // de tabla hay que volver al default de ESA tabla, no arrastrar una columna
  // que quizá no existe en la nueva.
  const [selectedTableName, setSelectedTableName] = useState<string | null>(null);
  const currentTable = useMemo(() => {
    if (builderTables.length === 0) return null;
    return builderTables.find((table) => table.name === selectedTableName) ?? builderTables[0];
  }, [builderOptions, selectedTableName]);

  useEffect(() => {
    setSelectedTableName(builderOptions?.tables?.[0]?.name ?? null);
  }, [builderOptions]);

  // `metricChoice` es local a la tabla activa: al cambiar de tabla se vuelve al
  // default (la primera métrica de negocio, no el conteo).
  const activeMetric: BuilderMetric | null = useMemo(() => {
    if (!currentTable) return null;
    if (metricChoice === AUTO_CHOICE) return currentTable.metrics[0] ?? null;
    return currentTable.metrics.find((metric) => metricKey(metric) === metricChoice)
      ?? currentTable.metrics[0]
      ?? null;
  }, [currentTable, metricChoice]);

  const activeDimension: BuilderColumn | null = useMemo(() => {
    if (!currentTable) return null;
    const groupableColumns = [...currentTable.dimensions, ...currentTable.dates];
    if (groupableColumns.length === 0) return null;
    if (dimensionChoice === NO_GROUPING) return null;
    if (dimensionChoice === AUTO_CHOICE) return groupableColumns[0];
    return groupableColumns.find((column) => column.name === dimensionChoice) ?? groupableColumns[0];
  }, [currentTable, dimensionChoice]);

  // El filtro temporal necesita una columna DATE/TIMESTAMP real. `periodo`
  // VARCHAR(7) sirve para agrupar, pero "últimos 90 días" sobre un YYYY-MM
  // obliga al LLM a inventar comparaciones de texto.
  const periodColumn: BuilderColumn | null = useMemo(() => {
    if (!currentTable || currentTable.dates.length === 0) return null;
    return currentTable.dates.find((column) => /date|time/i.test(column.type ?? ''))
      ?? currentTable.dates[0];
  }, [currentTable]);

  useEffect(() => {
    setMetricChoice(AUTO_CHOICE);
    setDimensionChoice(AUTO_CHOICE);
    setPeriod('all');
  }, [currentTable?.name]);

  // Auto-resize textarea height
  useEffect(() => {
    if (activeTextareaRef.current) {
      activeTextareaRef.current.style.height = 'auto';
      activeTextareaRef.current.style.height = `${Math.min(activeTextareaRef.current.scrollHeight, 180)}px`;
    }
  }, [promptInput, activeTextareaRef]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // Entrada segura para IME: el Enter que confirma una composición
      // (chino, japonés, coreano, o teclados con composición) no es un envío.
      // `keyCode === 229` es el aviso de algunos navegadores durante la composición.
      if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
      if (promptInput.trim() && !isGenerating) {
        onSubmit(promptInput);
      }
    }
  };

  const generatedPrompt = (): string | null => {
    if (!currentTable || !activeMetric) return null;
    return buildBuilderPrompt({
      table: currentTable,
      metric: activeMetric,
      dimension: activeDimension,
      dateColumn: periodColumn,
      period,
    });
  };

  const handleApplyBuilder = () => {
    const generated = generatedPrompt();
    if (!generated) return;
    setPromptInput(generated);
    setShowBuilder(false);
    if (activeTextareaRef.current) {
      activeTextareaRef.current.focus();
    }
  };

  const handleExecuteBuilder = () => {
    const generated = generatedPrompt();
    if (!generated || isGenerating) return;
    setPromptInput(generated);
    setShowBuilder(false);
    onSubmit(generated);
  };

  const preview = activeMetric
    ? buildBuilderPreview({ metric: activeMetric, dimension: activeDimension, period })
    : '';

  return (
    <div className="glass-panel rounded-2xl sm:rounded-3xl p-3 shadow-2xl space-y-2.5 font-sans relative group transition-all">
      {/* Collapsible Smart Query Builder */}
      {showBuilder && (
        <div id="chat-smart-builder" className="p-3 rounded-2xl bg-white dark:bg-zinc-900/95 border border-indigo-500/30 shadow-xl space-y-3 animate-fadeIn">
          <div className="flex items-center justify-between pb-2 border-b border-slate-200 dark:border-zinc-800">
            <div className="flex items-center space-x-2 text-indigo-700 dark:text-indigo-300 text-xs font-semibold">
              <Wand2 className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
              <span>Armar consulta paso a paso</span>
            </div>
            <button
              type="button"
              onClick={() => setShowBuilder(false)}
              aria-label="Cerrar el asistente de consulta"
              className="p-1 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {builderLoading && builderTables.length === 0 ? (
            <p className="text-xs text-app-muted leading-relaxed py-1">
              Cargando las columnas de la fuente activa…
            </p>
          ) : builderTables.length === 0 || !currentTable ? (
            <p className="text-xs text-app-muted leading-relaxed py-1">
              No hay columnas disponibles en el esquema de la fuente activa para armar una consulta guiada.
              Verifica la conexión o los permisos de tu perfil.
            </p>
          ) : (
            <>
              <div className="space-y-2 text-xs">
                {/* Step 1: Table + metric */}
                <div>
                  <span className="text-gray-700 dark:text-zinc-400 text-[11px] font-semibold mr-2">1. ¿Qué medir?</span>
                  {builderTables.length > 1 ? (
                    <select
                      value={currentTable.name}
                      onChange={(e) => setSelectedTableName(e.target.value)}
                      aria-label="Tabla a consultar"
                      className="mt-1 mr-2 px-2 py-1 rounded-lg bg-slate-100 dark:bg-zinc-800/80 text-[11px] font-medium text-slate-700 dark:text-zinc-300 border border-slate-200 dark:border-zinc-700 focus:outline-none"
                    >
                      {builderTables.map((table) => (
                        <option key={table.name} value={table.name}>
                          {humanizeLabel(table.label)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="text-[11px] text-gray-600 dark:text-zinc-400 mr-2">
                      Tabla: <strong className="text-gray-800 dark:text-zinc-200">{humanizeLabel(currentTable.label)}</strong>
                    </span>
                  )}
                  <div className="inline-flex flex-wrap gap-1.5 mt-1">
                    {currentTable.metrics.map((metric) => {
                      const key = metricKey(metric);
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => setMetricChoice(key)}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                            activeMetric && metricKey(activeMetric) === key
                              ? 'bg-indigo-600 text-white shadow-xs'
                              : 'bg-slate-100 dark:bg-zinc-800/80 text-slate-700 dark:text-zinc-300 hover:bg-slate-200 dark:hover:bg-zinc-700 hover:text-slate-900 dark:hover:text-white'
                          }`}
                        >
                          {humanizeLabel(metric.label)}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Step 2: Grouping */}
                <div>
                  <span className="text-gray-700 dark:text-zinc-400 text-[11px] font-semibold mr-2">2. Agrupar por:</span>
                  <div className="inline-flex flex-wrap gap-1.5 mt-1">
                    <button
                      type="button"
                      onClick={() => setDimensionChoice(NO_GROUPING)}
                      className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                        activeDimension === null
                          ? 'bg-cyan-600 text-white shadow-xs'
                          : 'bg-slate-100 dark:bg-zinc-800/80 text-slate-700 dark:text-zinc-300 hover:bg-slate-200 dark:hover:bg-zinc-700 hover:text-slate-900 dark:hover:text-white'
                      }`}
                    >
                      Sin agrupar
                    </button>
                    {[...currentTable.dimensions, ...currentTable.dates].map((column) => (
                      <button
                        key={column.name}
                        type="button"
                        onClick={() => setDimensionChoice(column.name)}
                        className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                          activeDimension?.name === column.name
                            ? 'bg-cyan-600 text-white shadow-xs'
                            : 'bg-slate-100 dark:bg-zinc-800/80 text-slate-700 dark:text-zinc-300 hover:bg-slate-200 dark:hover:bg-zinc-700 hover:text-slate-900 dark:hover:text-white'
                        }`}
                      >
                        {humanizeLabel(column.label)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Step 3: Period, only if the table has a real date column */}
                {periodColumn && (
                  <div>
                    <span className="text-gray-700 dark:text-zinc-400 text-[11px] font-semibold mr-2">
                      3. Período ({humanizeLabel(periodColumn.label)}):
                    </span>
                    <div className="inline-flex flex-wrap gap-1.5 mt-1">
                      {[
                        { label: `Este Año (${new Date().getFullYear()})`, val: 'year' as BuilderPeriod },
                        { label: 'Últimos 90 Días', val: 'last90' as BuilderPeriod },
                        { label: 'Todo el Histórico', val: 'all' as BuilderPeriod },
                      ].map((item) => (
                        <button
                          key={item.val}
                          type="button"
                          onClick={() => setPeriod(item.val)}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all ${
                            period === item.val
                              ? 'bg-emerald-600 text-white shadow-xs'
                              : 'bg-slate-100 dark:bg-zinc-800/80 text-slate-700 dark:text-zinc-300 hover:bg-slate-200 dark:hover:bg-zinc-700 hover:text-slate-900 dark:hover:text-white'
                          }`}
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between pt-2.5 border-t border-slate-200 dark:border-zinc-800 gap-2 text-[11px]">
                <span className="text-gray-600 dark:text-zinc-400 truncate max-w-sm">
                  Previa: <i className="text-gray-800 dark:text-zinc-300">{preview}</i>
                </span>
                <div className="flex items-center space-x-2 self-end sm:self-auto">
                  <button
                    type="button"
                    onClick={handleApplyBuilder}
                    disabled={!activeMetric}
                    className="px-2.5 py-1.5 rounded-xl bg-slate-100 dark:bg-zinc-800 hover:bg-slate-200 dark:hover:bg-zinc-700 text-slate-700 dark:text-zinc-300 hover:text-slate-900 dark:hover:text-white font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:pointer-events-none"
                    title="Cargar el texto en el editor para modificarlo"
                  >
                    Insertar texto
                  </button>
                  <button
                    type="button"
                    onClick={handleExecuteBuilder}
                    disabled={!activeMetric || isGenerating}
                    className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-brand-600 hover:bg-brand-500 text-white font-semibold transition-colors shadow-xs cursor-pointer disabled:opacity-40 disabled:pointer-events-none"
                    title="Ejecutar la consulta"
                  >
                    <Send className="w-3.5 h-3.5" />
                    <span>Consultar Ahora ↵</span>
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Auto-resizing Textarea */}
      <div className="relative">
        <textarea
          ref={activeTextareaRef}
          rows={2}
          value={promptInput}
          onChange={(e) => setPromptInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isGenerating}
          aria-label="Pregunta analítica sobre datos corporativos"
          className="w-full bg-app-card/70 text-xs sm:text-sm text-app-text placeholder:text-app-muted rounded-xl px-3.5 py-2.5 resize-none focus:outline-none border border-app-border transition-colors font-sans leading-relaxed"
        />
      </div>

      {/* Atajo al constructor guiado. Solo con el campo vacio y el constructor
          cerrado: con texto escrito el usuario ya sabe que pedir, y con el
          constructor abierto el toggle de la toolbar ya esta a la vista. */}
      {!promptInput.trim() && !showBuilder && (
        <button
          type="button"
          onClick={() => setShowBuilder(true)}
          className="text-[11px] text-app-muted hover:text-brand-500 dark:hover:text-brand-400 transition-colors text-left"
        >
          ¿No sabes cómo pedirlo? Arma la consulta paso a paso
        </button>
      )}

      {/* Toolbar & Action Controls */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-1 pt-1 border-t border-app-border/60">
        {/* Left Items: Active DB Badge & Model Pill */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Smart Builder Toggle Button */}
          <button
            type="button"
            onClick={() => setShowBuilder((prev) => !prev)}
            aria-expanded={showBuilder}
            aria-controls="chat-smart-builder"
            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-xl border text-[11px] font-semibold transition-colors shadow-xs ${
              showBuilder
                ? 'bg-brand-600 text-white border-brand-500'
                : 'bg-app-bg hover:bg-brand-950/40 text-brand-300 border-brand-500/30 hover:border-brand-500/50'
            }`}
            title="Abrir el constructor guiado con las tablas y columnas de la fuente activa"
          >
            <Wand2 className="w-3.5 h-3.5 text-brand-400" />
            <span>Armar consulta</span>
          </button>

          {/* Active Database Badge */}
          <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded-xl bg-app-surface border border-app-border text-[11px] font-medium text-app-muted shadow-xs">
            <Database className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
            <span className="truncate max-w-[140px] sm:max-w-[200px] font-semibold">{activeDatabaseName}</span>
          </div>
        </div>

        {/* Right Action: Send Button */}
        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={() => {
              if (promptInput.trim() && !isGenerating) {
                onSubmit(promptInput);
              }
            }}
            disabled={!promptInput.trim() || isGenerating}
            aria-label="Enviar consulta"
            title="Enviar consulta"
            className="p-2.5 rounded-full bg-brand-600 hover:bg-brand-500 active:scale-[0.98] text-white disabled:opacity-30 disabled:pointer-events-none transition-all shadow-sm cursor-pointer"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
