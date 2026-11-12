import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Database, Loader2, TrendingUp, X } from 'lucide-react';
import { useChatEngine } from '../features/chat/hooks/useChatEngine';
import {
  projectionService,
  type ProjectionHistoryItem,
  type ProjectionResult,
} from '../features/projections/services/projection_service';
import { ProjectionResults } from '../features/projections/components/ProjectionResults';
import { ProjectionHistory } from '../features/projections/components/ProjectionHistory';
import { getApiErrorMessage } from '../shared/api/api_client';
import { useNotifications } from '../context/NotificationContext';

/**
 * Seccion "Proyecciones": pregunta -> Enter -> estimacion de duracion ->
 * calculo con explicacion -> resultados.
 *
 * Decisiones del plan que se ven en este archivo:
 * - Un solo Enter lanza estimate -> run; mientras corre, Enter no re-dispara.
 * - La llegada desde el chat (fase 5) trae la pregunta en `location.state` y el
 *   input aparece pre-cargado; el Enter del usuario es la confirmacion.
 * - La barra de progreso es tiempo transcurrido contra la estimacion medida
 *   (patron del wizard de catalogos): piso 3%, tope 96%, y el 100% solo llega
 *   con la respuesta del backend. La espera real es UNA llamada HTTP sin
 *   streaming: las fases son ritmo visual y el aviso de "tarda mas" es real.
 */

// Fases por tiempo: la corrida no informa en que etapa esta hasta responder.
// Se muestran como contexto del trabajo del servidor, no como medicion.
const GENERATION_PHASES = [
  'Entendiendo tu pregunta…',
  'Leyendo tus datos…',
  'Calculando la proyección…',
  'Preparando el resumen…',
];

// Ejemplos de pedido: al hacer clic cargan la pregunta en el campo, sin
// lanzarla. El usuario la revisa y la envía cuando quiere.
const EXAMPLE_QUESTIONS = [
  '¿Cuánto voy a vender el próximo año?',
  'Proyecta un crecimiento del 10% anual a 5 años',
  '¿Cuál es el producto con más ventas proyectadas?',
];

export const ProjectionsPage: React.FC = () => {
  const { notify } = useNotifications();
  const location = useLocation();
  const { connectors, activeConnectionId, activeDatabaseName, handleSelectConnection } =
    useChatEngine();

  // Pregunta pegada por la redireccion del chat (plan §5). Sin estado, vacia.
  const [question, setQuestion] = useState<string>(
    () => (location.state as { question?: string } | null)?.question ?? ''
  );
  const [phase, setPhase] = useState<'idle' | 'running' | 'done'>('idle');
  const [result, setResult] = useState<ProjectionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Resultado guardado que se esta revisando desde el historial. Se distingue
  // de una corrida recien hecha para no presentar datos viejos como actuales.
  const [archivedView, setArchivedView] = useState<{ auditLogId: number; timestamp: string } | null>(null);
  const liveResultRef = useRef<ProjectionResult | null>(null);
  const archivedTopRef = useRef<HTMLDivElement>(null);
  // Foto del pedido al lanzar: la pantalla de generacion tiene que mostrar lo
  // que corre de verdad, aunque el textarea cambie despues.
  const [runInfo, setRunInfo] = useState<{ question: string } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  // Referencia nominal de 60s mientras no llegue la estimacion (mismo criterio
  // que el wizard): un numero conservador declarado, no una medicion fingida.
  const [expected, setExpected] = useState<{ seconds: number; source: string | null }>({
    seconds: 60,
    source: null,
  });
  const startedAtRef = useRef<number | null>(null);
  const runIdRef = useRef(0);
  const runControllerRef = useRef<AbortController | null>(null);
  // La conexion activa arranca en null mientras `useChatEngine` resuelve. El
  // primer paso null -> id NO es un cambio de fuente: si se trata como tal, una
  // corrida lanzada en esa ventana se aborta en silencio. Solo se resetea
  // cuando la fuente cambia DESPUES de haberse resuelto la primera vez.
  const connectionResolvedRef = useRef(false);
  const lastConnectionRef = useRef<number | null>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => () => {
    runIdRef.current += 1;
    runControllerRef.current?.abort();
  }, []);

  // Ticker de 1s mientras corre: alimenta el contador y el avance de la barra.
  useEffect(() => {
    if (phase !== 'running') return;
    const id = setInterval(() => {
      const started = startedAtRef.current;
      if (started) setElapsed(Math.max(0, Math.round((Date.now() - started) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  // Cambiar de conexion invalida el resultado: los números de A no se pueden
  // leer como si fueran de B. También aborta la corrida en vuelo. La primera
  // resolucion (null -> id) no cuenta como cambio.
  useEffect(() => {
    if (!connectionResolvedRef.current) {
      if (activeConnectionId === null) return;
      connectionResolvedRef.current = true;
      lastConnectionRef.current = activeConnectionId;
      return;
    }
    if (lastConnectionRef.current === activeConnectionId) return;
    lastConnectionRef.current = activeConnectionId;
    runIdRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    liveResultRef.current = null;
    setArchivedView(null);
    setResult(null);
    setError(null);
    setPhase('idle');
  }, [activeConnectionId]);

  const isRunning = phase === 'running';
  const canRun = question.trim().length > 0 && !isRunning;

  // Reabrir una corrida guardada no borra la corrida viva: al cerrar se vuelve
  // a lo que el usuario tenia en pantalla antes de entrar al historial.
  const openArchivedProjection = (saved: ProjectionResult, item: ProjectionHistoryItem) => {
    // Una sola area de resultado: abrir una guardada mientras corre otra corrida
    // corta la espera de esta, no deja dos resultados compitiendo por el mismo
    // espacio (y el vivo pisaria al guardado al terminar).
    if (isRunning) {
      runIdRef.current += 1;
      runControllerRef.current?.abort();
      runControllerRef.current = null;
      startedAtRef.current = null;
      notify('info', 'Se canceló la espera de la proyección en curso para abrir una guardada.');
    }
    if (!archivedView) liveResultRef.current = result;
    setArchivedView({ auditLogId: item.audit_log_id, timestamp: item.timestamp });
    setResult(saved);
    setRunInfo({ question: saved.question });
    setError(null);
    setPhase('done');
  };

  const closeArchivedProjection = () => {
    const previous = liveResultRef.current;
    liveResultRef.current = null;
    setArchivedView(null);
    setResult(previous);
    setRunInfo(null);
    setError(null);
    setPhase(previous ? 'done' : 'idle');
  };

  useEffect(() => {
    if (archivedView) {
      archivedTopRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }
  }, [archivedView]);

  const archivedDate = (() => {
    if (!archivedView) return '';
    const date = new Date(archivedView.timestamp);
    return Number.isNaN(date.getTime())
      ? archivedView.timestamp
      : date.toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' });
  })();

  const runProjection = async () => {
    const prompt = question.trim();
    if (!prompt || isRunning) return;

    const runId = ++runIdRef.current;
    runControllerRef.current?.abort();
    const controller = new AbortController();
    runControllerRef.current = controller;

    setError(null);
    setResult(null);
    setArchivedView(null);
    liveResultRef.current = null;
    setRunInfo({ question: prompt });
    startedAtRef.current = Date.now();
    setElapsed(0);
    setExpected({ seconds: 60, source: null });
    setPhase('running');

    try {
      // La estimacion es un adorno de la barra: si falla, la corrida sigue con
      // la referencia nominal y la pantalla lo dice.
      try {
        const estimate = await projectionService.estimate(prompt, null);
        if (estimate && estimate.estimated_seconds > 0) {
          setExpected({ seconds: estimate.estimated_seconds, source: estimate.source });
        }
      } catch {
        /* referencia nominal de 60s */
      }
      if (runId !== runIdRef.current) return;

      // El horizonte y la granularidad los decide el backend a partir de la
      // pregunta: la UI ya no manda selectores ni overrides.
      const response = await projectionService.run(
        prompt,
        activeConnectionId ?? null,
        null,
        undefined,
        controller.signal
      );
      if (runId !== runIdRef.current) return;
      setResult(response);
    } catch (err: any) {
      if (err?.name === 'AbortError' || runId !== runIdRef.current) return;
      setError(
        getApiErrorMessage(err, 'No se pudo ejecutar la proyección.', true)
      );
    }
    if (runId === runIdRef.current) {
      setPhase('done');
      runControllerRef.current = null;
    }
  };

  // Cancelar la espera. El servidor puede seguir trabajando: esto corta la
  // espera del navegador, no el cálculo remoto (misma semántica que el chat).
  const cancelProjection = () => {
    runIdRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    startedAtRef.current = null;
    setPhase('idle');
    notify('info', 'Proyección cancelada.');
  };

  // Avance: fraccion transcurrida de la estimacion. El tope de 96% no se alcanza
  // hasta que el backend responde: la barra nunca miente "100%".
  const generationPct = Math.min(96, Math.max(3, Math.round((elapsed / expected.seconds) * 100)));
  const ratio = elapsed / expected.seconds;
  const phaseIndex = ratio < 0.15 ? 0 : ratio < 0.45 ? 1 : ratio < 0.85 ? 2 : 3;

  return (
    <div className="w-full h-full flex-1 overflow-y-auto custom-scrollbar bg-app-bg text-app-text font-sans">
      <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6 pb-24">
        {/* Encabezado y pedido */}
        <section className="rounded-2xl border border-app-border bg-app-card p-5 shadow-xs">
          <div className="flex items-start gap-3.5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-violet-600 text-white shadow-sm">
              <TrendingUp className="h-5 w-5" aria-hidden />
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-extrabold tracking-tight text-app-text sm:text-xl">
                Proyecciones
              </h1>
              <p className="text-xs leading-relaxed text-app-muted">
                Qué puede pasar con tus datos: un pronóstico base o un escenario con tus propios
                supuestos.
              </p>
            </div>
          </div>

          {/* Fuente de datos y horizonte */}
          <div className="mt-4 flex flex-wrap items-center gap-3 text-xs font-medium">
            <div className="flex items-center gap-2">
              <Database size={14} className="shrink-0 text-cyan-500 dark:text-cyan-400" aria-hidden />
              {connectors.length > 1 ? (
                <div className="flex items-center gap-1.5">
                  <label htmlFor="projections-connection-select" className="sr-only">
                    Seleccionar fuente de datos
                  </label>
                  <select
                    id="projections-connection-select"
                    aria-label="Seleccionar fuente de datos activa"
                    value={activeConnectionId || ''}
                    onChange={(e) => handleSelectConnection(Number(e.target.value))}
                    className="cursor-pointer rounded-lg border border-app-border bg-app-surface px-2.5 py-1 text-xs font-medium text-app-text shadow-xs transition-colors hover:border-brand-500/50 focus:border-brand-500 focus:outline-none"
                  >
                    {connectors.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} ({c.db_type.toUpperCase()})
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <span className="truncate font-semibold text-app-text">{activeDatabaseName}</span>
              )}
            </div>
          </div>

          <div className="mt-3">
            <label htmlFor="projections-question" className="sr-only">
              Pregunta de proyección
            </label>
            <textarea
              id="projections-question"
              ref={questionRef}
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  // Entrada segura para IME: el Enter que confirma una composición
                  // no lanza la proyección.
                  if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
                  if (canRun) void runProjection();
                }
              }}
              disabled={isRunning}
              placeholder="Ej: ¿cuánto voy a facturar en los próximos 6 meses si la tendencia sigue?"
              className="w-full resize-none rounded-xl border border-app-border bg-app-surface px-3.5 py-2.5 text-xs leading-relaxed text-app-text placeholder:text-app-muted transition-colors focus:border-brand-500 focus:outline-none disabled:opacity-60 sm:text-sm"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLE_QUESTIONS.map((example) => (
                <button
                  key={example}
                  type="button"
                  disabled={isRunning}
                  onClick={() => {
                    setQuestion(example);
                    questionRef.current?.focus();
                  }}
                  title="Cargar este ejemplo en el campo"
                  className="cursor-pointer rounded-full border border-app-border bg-app-bg px-2.5 py-1 text-left text-[11px] text-app-muted transition-colors hover:border-brand-500/50 hover:text-app-text focus:border-brand-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {example}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-app-muted">
              Si pides un crecimiento («10% anual»), Dat.ia lo trata como escenario; no es un
              pronóstico estadístico.
            </p>
            <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
              {isRunning ? (
                <button
                  type="button"
                  onClick={cancelProjection}
                  title="Dejar de esperar la respuesta. El cálculo puede continuar en el servidor."
                  className="flex items-center gap-1.5 rounded-xl border border-app-border bg-app-surface px-3.5 py-1.5 text-xs font-semibold text-app-text transition-colors hover:border-rose-500/40 hover:text-rose-600 dark:hover:text-rose-400"
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                  <span>Detener</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void runProjection()}
                  disabled={!canRun}
                  className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-violet-500 disabled:pointer-events-none disabled:opacity-40"
                >
                  <span>Proyectar</span>
                </button>
              )}
            </div>
          </div>
        </section>

        {/* Pantalla de generacion */}
        {isRunning && (
          <section
            role="status"
            className="animate-fadeIn space-y-4 rounded-2xl border border-app-border bg-app-card p-6 text-center"
          >
            <div className="relative mx-auto h-14 w-14">
              <div className="absolute inset-0 animate-ping rounded-2xl bg-violet-600/20" />
              <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-600 text-white shadow-xl shadow-violet-600/30">
                <TrendingUp className="h-7 w-7 animate-pulse" aria-hidden />
              </div>
            </div>

            <div className="space-y-1">
              <h3 className="text-base font-bold text-app-text">Generando proyección</h3>
              <p className="mx-auto max-w-lg truncate text-xs italic text-app-muted" title={runInfo?.question}>
                «{runInfo?.question}»
              </p>
            </div>

            <div className="mx-auto max-w-lg space-y-2">
              <div className="h-2.5 w-full overflow-hidden rounded-full border border-app-border bg-app-bg">
                <div
                  className="h-2.5 rounded-full bg-violet-600 transition-all duration-700 ease-out"
                  style={{ width: `${generationPct}%` }}
                />
              </div>
              <div className="flex justify-between font-mono text-[11px] text-app-muted">
                <span>
                  Fase {phaseIndex + 1} de {GENERATION_PHASES.length} · {elapsed} s
                </span>
                <span className="font-semibold text-violet-600 dark:text-violet-400">
                  {generationPct}%
                </span>
              </div>
              <p className="text-[11px] leading-relaxed text-app-muted">
                {expected.source === 'medido'
                  ? `Tarda aproximadamente ${Math.round(expected.seconds)} segundos.`
                  : expected.source === 'fallback'
                    ? `Tarda aproximadamente ${Math.round(expected.seconds)} segundos (referencia conservadora).`
                    : 'Calculando cuánto va a tardar…'}
              </p>
              {elapsed > expected.seconds && (
                <p className="text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                  La corrida está tardando más de lo estimado. Sigue trabajando: la respuesta
                  solo llega cuando el cálculo termina completo.
                </p>
              )}
            </div>

            <div className="mx-auto flex max-w-lg items-center justify-center gap-2.5 rounded-xl border border-violet-500/30 bg-app-bg p-3.5 text-xs text-app-text">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-violet-600 dark:text-violet-400" aria-hidden />
              <span>{GENERATION_PHASES[phaseIndex]}</span>
            </div>

            <button
              type="button"
              onClick={cancelProjection}
              title="Dejar de esperar la respuesta. El cálculo puede continuar en el servidor."
              className="mx-auto flex items-center gap-1.5 rounded-xl border border-app-border bg-app-surface px-4 py-1.5 text-xs font-semibold text-app-text transition-colors hover:border-rose-500/40 hover:text-rose-600 dark:hover:text-rose-400"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
              <span>Detener</span>
            </button>
            <p className="mx-auto max-w-lg text-[10px] leading-relaxed text-app-muted">
              Detener corta la espera en esta pantalla; el cálculo puede continuar en el servidor.
            </p>
          </section>
        )}

        {/* Error de red o 500: no es "sin evidencia", es "no se pudo pedir". */}
        {error && (
          <section
            role="alert"
            className="rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-800/60 dark:bg-rose-950/40 dark:text-rose-200"
          >
            <p className="font-semibold">No se pudo ejecutar la proyección</p>
            <p className="mt-1 leading-relaxed">{error}</p>
            <p className="mt-2 text-xs opacity-80">
              La pregunta quedó en el campo: corrige lo que haga falta y vuelve a intentar.
            </p>
          </section>
        )}

        {/* Aviso de que se esta leyendo una corrida guardada, no la actual. */}
        {archivedView && !isRunning && (
          <section
            ref={archivedTopRef}
            className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-violet-500/40 bg-violet-500/10 p-4"
          >
            <div>
              <p className="text-xs font-bold text-violet-800 dark:text-violet-200">
                Viendo una proyección guardada del {archivedDate}
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-violet-700 dark:text-violet-300">
                Es el resultado publicado en esa fecha. Los datos actuales pueden haber cambiado;
                usa «Verificar» en el historial para compararlo con los periodos ya cerrados.
              </p>
            </div>
            <button
              type="button"
              onClick={closeArchivedProjection}
              className="inline-flex items-center gap-1.5 rounded-xl border border-violet-500/40 bg-app-card px-3 py-1.5 text-xs font-semibold text-app-text transition-colors hover:bg-app-hover"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
              Cerrar
            </button>
          </section>
        )}

        {/* Resultado (disponible o "sin evidencia") */}
        {!isRunning && result && <ProjectionResults result={result} />}

        {/* Historial propio: permite reabrir o verificar corridas anteriores. */}
        <ProjectionHistory
          refreshKey={result?.audit_log_id ?? null}
          onOpenProjection={openArchivedProjection}
        />

        {/* Estado inicial: sin corrida y sin resultado */}
        {!isRunning && !result && !error && (
          <section className="rounded-2xl border border-dashed border-app-border/80 bg-app-card/50 p-6 text-center">
            <p className="text-sm font-medium text-app-text">Todavía no hay ninguna proyección</p>
            <p className="mx-auto mt-1 max-w-xl text-xs leading-relaxed text-app-muted">
              Escribe una pregunta con período explícito («los próximos 6 meses», «el próximo
              trimestre») y das Enter. Si la serie no alcanza o tu rol bloquea alguna columna,
              vas a ver el motivo exacto en vez de números inventados.
            </p>
          </section>
        )}
      </div>
    </div>
  );
};
