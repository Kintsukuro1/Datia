import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Database, Loader2, TrendingUp } from 'lucide-react';
import { useChatEngine } from '../features/chat/hooks/useChatEngine';
import { projectionService, type ProjectionResult } from '../features/projections/services/projection_service';
import { ProjectionResults } from '../features/projections/components/ProjectionResults';

/**
 * Seccion "Proyecciones": pregunta -> Enter -> estimacion de duracion ->
 * walk-forward con narrativa -> resultados.
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
  'Analizando la pregunta y eligiendo tabla y métrica...',
  'Leyendo los datos con tus permisos vigentes...',
  'Calculando la proyección (walk-forward de 3 métodos)...',
  'Redactando la lectura de los resultados...',
];

const HORIZONS = [3, 6, 12] as const;

export const ProjectionsPage: React.FC = () => {
  const location = useLocation();
  const { connectors, activeConnectionId, activeDatabaseName, handleSelectConnection } =
    useChatEngine();

  // Pregunta pegada por la redireccion del chat (plan §5). Sin estado, vacia.
  const [question, setQuestion] = useState<string>(
    () => (location.state as { question?: string } | null)?.question ?? ''
  );
  const [horizon, setHorizon] = useState<number>(6);
  const [phase, setPhase] = useState<'idle' | 'running' | 'done'>('idle');
  const [result, setResult] = useState<ProjectionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Foto del pedido al lanzar: la pantalla de generacion tiene que mostrar lo
  // que corre de verdad, aunque el textarea o el selector cambien despues.
  const [runInfo, setRunInfo] = useState<{ question: string; horizon: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  // Referencia nominal de 60s mientras no llegue la estimacion (mismo criterio
  // que el wizard): un numero conservador declarado, no una medicion fingida.
  const [expected, setExpected] = useState<{ seconds: number; source: string | null }>({
    seconds: 60,
    source: null,
  });
  const startedAtRef = useRef<number | null>(null);

  // Ticker de 1s mientras corre: alimenta el contador y el avance de la barra.
  useEffect(() => {
    if (phase !== 'running') return;
    const id = setInterval(() => {
      const started = startedAtRef.current;
      if (started) setElapsed(Math.max(0, Math.round((Date.now() - started) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  const isRunning = phase === 'running';
  const canRun = question.trim().length > 0 && !isRunning;

  const runProjection = async () => {
    const prompt = question.trim();
    if (!prompt || isRunning) return;

    setError(null);
    setResult(null);
    setRunInfo({ question: prompt, horizon });
    startedAtRef.current = Date.now();
    setElapsed(0);
    setExpected({ seconds: 60, source: null });
    setPhase('running');

    try {
      // La estimacion es un adorno de la barra: si falla, la corrida sigue con
      // la referencia nominal y la pantalla lo dice ("sin medición reciente").
      try {
        const estimate = await projectionService.estimate(prompt, horizon);
        if (estimate && estimate.estimated_seconds > 0) {
          setExpected({ seconds: estimate.estimated_seconds, source: estimate.source });
        }
      } catch {
        /* referencia nominal de 60s */
      }

      const response = await projectionService.run(prompt, activeConnectionId ?? null, horizon);
      setResult(response);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || err?.message || 'No se pudo ejecutar la proyección.'
      );
    }
    setPhase('done');
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
                Proyecciones con Machine Learning
              </h1>
              <p className="text-xs leading-relaxed text-app-muted">
                Analiza tu pregunta, elige la serie y proyecta con backtest walk-forward sobre
                datos reales. El modelo de lenguaje narra, pero no calcula: cada número sale
                medido y sus límites se publican.
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

            <div className="flex items-center gap-1.5">
              <label htmlFor="projections-horizon-select" className="text-app-muted">
                Horizonte
              </label>
              <select
                id="projections-horizon-select"
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
                className="cursor-pointer rounded-lg border border-app-border bg-app-surface px-2.5 py-1 text-xs font-medium text-app-text shadow-xs transition-colors hover:border-brand-500/50 focus:border-brand-500 focus:outline-none"
              >
                {HORIZONS.map((h) => (
                  <option key={h} value={h}>
                    {h} pasos
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Pregunta */}
          <div className="mt-3">
            <label htmlFor="projections-question" className="sr-only">
              Pregunta de proyección
            </label>
            <textarea
              id="projections-question"
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (canRun) void runProjection();
                }
              }}
              disabled={isRunning}
              placeholder="Ej: ¿cuánto voy a facturar en los próximos 6 meses si la tendencia sigue?"
              className="w-full resize-none rounded-xl border border-app-border bg-app-surface px-3.5 py-2.5 text-xs leading-relaxed text-app-text placeholder:text-app-muted transition-colors focus:border-brand-500 focus:outline-none disabled:opacity-60 sm:text-sm"
            />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] leading-relaxed text-app-muted">
                Un Enter lanza el análisis y el cálculo. La IA puede ajustar el horizonte si tu
                pregunta lo dice; el resultado muestra el horizonte publicado.
              </span>
              <button
                type="button"
                onClick={() => void runProjection()}
                disabled={!canRun}
                className="flex items-center gap-1.5 rounded-xl bg-violet-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-violet-500 disabled:pointer-events-none disabled:opacity-40"
              >
                {isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                <span>{isRunning ? 'Proyectando…' : 'Proyectar ↵'}</span>
              </button>
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
              <p className="text-xs text-app-muted">
                Horizonte: {runInfo?.horizon} pasos
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
                  ? `Estimado: ~${Math.round(expected.seconds)} s (velocidad medida del modelo local).`
                  : expected.source === 'fallback'
                    ? `Estimación conservadora: ~${Math.round(expected.seconds)} s (sin medición reciente del modelo).`
                    : 'Sin estimación todavía: referencia conservadora de 60 s.'}
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
              La pregunta quedó en el campo: corregí lo que haga falta y volvé a dar Enter.
            </p>
          </section>
        )}

        {/* Resultado (disponible o "sin evidencia") */}
        {!isRunning && result && <ProjectionResults result={result} />}

        {/* Estado inicial: sin corrida y sin resultado */}
        {!isRunning && !result && !error && (
          <section className="rounded-2xl border border-dashed border-app-border/80 bg-app-card/50 p-6 text-center">
            <p className="text-sm font-medium text-app-text">Todavía no hay ninguna proyección</p>
            <p className="mx-auto mt-1 max-w-xl text-xs leading-relaxed text-app-muted">
              Escribí una pregunta con período explícito («los próximos 6 meses», «el próximo
              trimestre») y das Enter. Si la serie no alcanza o tu rol bloquea alguna columna,
              vas a ver el motivo exacto en vez de números inventados.
            </p>
          </section>
        )}
      </div>
    </div>
  );
};
