import { useState, useRef, useEffect, useCallback, useMemo, createContext, useContext } from 'react';
import { useAuth } from '../../auth/context/AuthContext';
import { useNotifications } from '../../../context/NotificationContext';
import { QueryResult } from '../../../types';
import { createStreamBuffer } from '../../../shared/stream_buffer';
import { ChatThread } from '../../../components/chat/SidebarChatHistory';
import { queryService, isPredictionQuestion } from '../services/query_service';
import { connectorService, CorporateConnection, CONNECTORS_CHANGED_EVENT } from '../../admin/services/connector_service';
import { resolveRoleLabel } from '../../../constants';

// El motor encadena hasta 3 llamadas al LLM en CPU (clasificar intencion ->
// recuperar esquema -> generar SQL) mas la validacion y la ejecucion. Medido en
// este equipo: 8 s el mejor caso, 25 s lo tipico de una pregunta con analisis.
// A los 8 s el usuario ya se esta preguntando si se colgo, y ese es el momento
// de decirle por que sigue esperando. Antes de 8 s no dice nada: menos de 8 s de
// espera no se percibe como colgado.
const LONG_WAIT_NOTICE_SECONDS = 8;

// Corta la espera del cliente a los 90 s. El servidor puede seguir trabajando:
// el AbortController corta la espera, no deshace el trabajo del backend.
const REQUEST_TIMEOUT_MS = 90_000;

// Cuantos hilos caben en la COPIA LOCAL del historial. Es un tope de
// persistencia, no de pantalla: `threads` en memoria nunca se poda, asi que
// recorta el snapshot y jamas lo que el usuario tiene a la vista.
// El corte va por la cabeza porque el array ya viene ordenado del mas reciente
// al mas viejo (aca los nuevos se anteponen; el servidor los devuelve con
// `order_by(updated_at.desc())`), y lo que pesa son las filas: cada `QueryResult`
// arrastra `data_rows` de la BD del cliente dentro del snapshot.
// ponytail: techo de 50 hilos en localStorage; los descartados sobreviven en el
// servidor (saveThread los POSTea) y `handleSelectThread` los rehidrata al
// hacer clic. Subir el tope, o vaciar `data_rows` y guardar solo la metadata de
// las filas, si el historial local llegara a pesar.
const PERSISTED_THREADS_LIMIT = 50;

// Ventana de agrupado del stream. Los deltas llegan token por token y cada
// `setState` re-renderiza el arbol entero del dashboard: escribir cada uno es
// un render por token. Volcar cada ~80 ms deja el texto creciendo a la vista
// (12 actualizaciones por segundo es indistinguible de token a token para el
// ojo) y baja el render de O(tokens) a O(ventana). Intervalo y no
// `requestAnimationFrame`: con la pestana en background el rAF se congela y la
// narrativa se frena hasta que vuelve el foco, y en ese rato no hay nada que
// mirar.
const STREAM_FLUSH_MS = 80;

// `localStorage` no da un codigo unico de cuota agotada, pero si un nombre
// consistente: `QuotaExceededError` en los navegadores modernos,
// `NS_ERROR_DOM_QUOTA_REACHED` (y codigos 22/1014) en los viejos. Antes esto
// terminaba en un `catch { // ignore }` y el historial local dejaba de guardarse
// sin decir una sola palabra.
const isQuotaError = (err: unknown): boolean => {
  const e = err as { name?: string; code?: number } | null;
  return (
    e?.name === 'QuotaExceededError' ||
    e?.name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
    e?.code === 22 ||
    e?.code === 1014
  );
};

// Grupo de fecha para el sidebar (Hoy / Ayer / Anteriores). La unica fecha real
// es `updated_at` del servidor; un hilo local sin fecha (o con una fecha que no
// parsea) cae en 'Anteriores' en vez de inventarle un dia.
const getDateGroup = (iso?: string | null): 'Hoy' | 'Ayer' | 'Anteriores' => {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return 'Anteriores';
  const dayStart = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((dayStart(new Date()) - dayStart(d)) / 86_400_000);
  if (diffDays <= 0) return 'Hoy';
  if (diffDays === 1) return 'Ayer';
  return 'Anteriores';
};

interface FullThread {
  id: string;
  title: string;
  timestamp: string;
  dateGroup?: 'Hoy' | 'Ayer' | 'Anteriores';
  connection_id?: number;
  results: QueryResult[];
}

function useChatEngineInternal() {
  const { user, settings } = useAuth();
  const { notify } = useNotifications();
  const [promptInput, setPromptInput] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [activeTraceability, setActiveTraceability] = useState<QueryResult['traceability'] | null>(null);
  const [isMobileHistoryOpen, setIsMobileHistoryOpen] = useState(false);
  const [activeConnectionId, setActiveConnectionId] = useState<number | null>(null);
  const [activeDatabaseName, setActiveDatabaseName] = useState('BD Corporativa Local');
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const promptTextareaRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  // Hilo del intento en curso o del ultimo intento fallido (timeout/error). Un
  // hilo nace sin resultados y sin copia en el servidor: el POST recien sale
  // cuando llega la respuesta. Sirve para saber a que conversacion pertenece la
  // tarjeta de "procesando" (y no mostrarla en cualquier otra) y para no dejar
  // un hilo vacio si su intento fallido se descarta.
  const attemptThreadIdRef = useRef<string | null>(null);
  // Espejo en estado del ref: la pantalla necesita el id para decidir donde va
  // la tarjeta. Se escriben siempre juntos con `setAttemptThread`.
  const [attemptThreadId, setAttemptThreadId] = useState<string | null>(null);
  const setAttemptThread = useCallback((id: string | null) => {
    attemptThreadIdRef.current = id;
    setAttemptThreadId(id);
  }, []);
  // Cuando arranco el intento: el mensaje de Cancelar dice a los cuantos
  // segundos se corto.
  const attemptStartedAtRef = useRef<number | null>(null);

  const userRole = resolveRoleLabel(user);
  const [promptSuggestions, setPromptSuggestions] = useState<string[]>([]);
  const [connectors, setConnectors] = useState<CorporateConnection[]>([]);

  // Local storage storage key
  const storageKey = `datia_threads_${user?.id || 'guest'}`;

  // Secuencia de sugerencias: la ultima peticion gana. Sin esto, la respuesta
  // del conector anterior (o del montaje) puede llegar tarde y pisar la lista
  // que corresponde al conector que el usuario tiene en pantalla.
  const suggestionsSeqRef = useRef(0);

  const loadSuggestions = useCallback((connectionId?: number) => {
    const seq = ++suggestionsSeqRef.current;
    queryService.getSuggestions(connectionId).then((suggs) => {
      if (seq === suggestionsSeqRef.current) {
        setPromptSuggestions(suggs);
      }
    }).catch(() => {
      // La lista vacia es el estado por defecto (no hay chips), no una
      // afirmacion sobre el servidor: `ChatEmptyState` solo pinta el bloque si
      // `length > 0`.
      if (seq === suggestionsSeqRef.current) {
        setPromptSuggestions([]);
      }
    });
  }, []);

  // 1. Load initial connectors & suggestions. El provider vive en ProtectedLayout
  // y NO se remonta al navegar, asi que esta no puede ser la unica carga: cada
  // mutacion de conectores (crear, subir, editar, activar o borrar) avisa por
  // CONNECTORS_CHANGED_EVENT y se reaplica la misma regla que al inicio. Sin eso
  // el selector del chat seguia mostrando (y consultando) la fuente anterior
  // hasta recargar la pagina.
  useEffect(() => {
    let isMounted = true;

    const applyConnectors = (conns: CorporateConnection[]) => {
      setConnectors(conns);
      const active = conns.find((c) => c.is_active) || conns[0] || null;
      setActiveConnectionId(active?.id ?? null);
      setActiveDatabaseName(
        active ? `${active.name} (${active.db_type.toUpperCase()})` : 'BD Corporativa Local'
      );
      loadSuggestions(active?.id);
    };

    const load = () => {
      connectorService.getConnectors().then((conns) => {
        if (isMounted && Array.isArray(conns)) {
          applyConnectors(conns);
        }
      });
    };

    loadSuggestions();
    load();
    window.addEventListener(CONNECTORS_CHANGED_EVENT, load);
    return () => {
      isMounted = false;
      window.removeEventListener(CONNECTORS_CHANGED_EVENT, load);
    };
  }, [loadSuggestions]);

  const handleSelectConnection = useCallback((id: number) => {
    const target = connectors.find((c) => c.id === id);
    if (target) {
      setActiveConnectionId(target.id);
      setActiveDatabaseName(`${target.name} (${target.db_type.toUpperCase()})`);
      notify('info', `Fuente de datos activa: ${target.name} (${target.db_type.toUpperCase()})`);
      loadSuggestions(target.id);
    }
  }, [connectors, loadSuggestions, notify]);

  // 2. Persistent Threads State (Cache-first with Backend Sync)
  const [threads, setThreads] = useState<FullThread[]>(() => {
    try {
      const cached = localStorage.getItem(storageKey);
      return cached ? JSON.parse(cached) : [];
    } catch {
      return [];
    }
  });

  // Espejo sincronico de `threads` para los callbacks async del hook: la
  // respuesta de la consulta llega despues de un `await` y su closure quedo con
  // el `threads` de otro render. Se actualiza por commit (effect), asi que
  // leerlo al llegar la respuesta da el estado vigente. No reemplaza al
  // `setThreads` funcional: ese sigue siendo la unica via de escritura.
  const threadsRef = useRef<FullThread[]>(threads);
  useEffect(() => {
    threadsRef.current = threads;
  }, [threads]);

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  // `activeThreadId` vigente cuando el intento TERMINE (el de la clausura es el
  // del arranque): con el intento siguiendo en segundo plano, decide si hay que
  // avisar que la respuesta quedo en otra conversacion.
  const activeThreadIdRef = useRef<string | null>(activeThreadId);
  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
  }, [activeThreadId]);

  // Mismo par que usa useAdminUsers: `threadsLoaded` separa "todavia cargando"
  // de "la API devolvio []" y `threadsError` de "no se pudo preguntar". Antes
  // un fallo de red dejaba el cache local en pantalla indistinguible de un
  // historial confirmado por el servidor.
  const [threadsLoaded, setThreadsLoaded] = useState(false);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  // La pregunta que quedo SIN respuesta (timeout del navegador o fallo de la
  // consulta) se conserva en pantalla junto a este motivo: el hilo mantiene la
  // pregunta visible con un boton para reintentar, en vez de vaciarse y
  // devolver la pantalla a la bienvenida como si la pregunta no hubiera
  // existido. `null` = no hay fallo que mostrar.
  const [pendingError, setPendingError] = useState<string | null>(null);
  // Origen de ese fallo: el titulo de la tarjeta cambia ("Cancelada" no es lo
  // mismo que "Sin respuesta"). Se guarda el origen; no se adivina del texto.
  const [pendingErrorKind, setPendingErrorKind] = useState<'cancel' | 'fail' | null>(null);

  // Proyeccion pedida desde el chat: la pregunta queda aca (fuera del hilo,
  // como la vieja tarjeta de prediccion) para ofrecer abrirla en /projections
  // con el input pre-cargado. `null` = no hay oferta en pantalla. No hay
  // request detras: nada se esta calculando mientras esto se ve.
  const [projectionSuggestion, setProjectionSuggestion] = useState<string | null>(null);

  // Segundos que lleva la consulta en curso. Es lo unico que el frontend puede
  // afirmar con certeza sin SSE: que sigue corriendo y cuanto lleva. Antes se
  // mostraba una "fase" que avanzaba por tiempos fijos (1400/3200/5500 ms) y
  // decia "Ejecutando consulta en base de datos" a los 3,2 s sin saber si el
  // motor estaba classifying, generando SQL o esperando al LLM: una barra de
  // progreso que no mide nada. Ahora no hay fases, hay reloj.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  // Narrativa que llega por el stream antes de que exista la respuesta final.
  // NO se commitea al hilo: se muestra en la burbuja de "procesando" y se
  // descarta en cuanto llega el `result` (que la reemplaza) o el intento
  // termina. Es lo que evita que quede media respuesta pegada en el chat si el
  // stream se corta: lo parcial nunca entra en `threads`.
  const [streamingNarrative, setStreamingNarrative] = useState('');

  // Tokens recibidos todavia no volcados al estado. Son refs y no mas estado a
  // proposito: entre un volcado y el siguiente no hay nada que renderizar, y
  // un `setState` por token es justo lo que se esta evitando.
  const streamBufferRef = useRef(createStreamBuffer(STREAM_FLUSH_MS, (text) => {
    setStreamingNarrative((prev) => prev + text);
  }));

  // Unico punto que escribe texto del stream en el estado: el sink del buffer, y
  // solo cuando su contenido ya esta agrupado. Por construccion sigue sin tocar
  // `threads`: la garantia de que lo parcial nunca se commitea no se relajo para
  // ganar rendimiento, solo se agrupo.

  // Descartar lo parcial, con el mismo criterio de siempre (nunca entra al
  // hilo: lo reemplaza el resultado o se cae el intento), pero ahora hay tambien
  // un buffer que puede quedar pendiente en vuelo y hay que cancelar.
  const discardStreamingNarrative = useCallback(() => {
    streamBufferRef.current.discard();
    setStreamingNarrative('');
  }, []);

  // Si el dashboard se desmonta con un volcado pendiente, el timer sigue vivo
  // hasta 80 ms y escribe contra un estado que ya no muestra nadie.
  useEffect(() => () => streamBufferRef.current.discard(), []);

  // El endpoint de stream es una mejora de latencia, no una capacidad. Si el
  // navegador o la red no lo bancan, la consulta va por `sendQuery` y nadie
  // nota la diferencia. Se desactiva sola tras un fallo para no reintentar el
  // stream en cada consulta si el problema es persistente.
  const [streamingEnabled, setStreamingEnabled] = useState(true);

  // El unico setInterval del hook, y vive atado a `isGenerating`: React lo
  // limpia al cambiar el flag o al desmontar, asi que no puede quedar tickeando
  // despues de que la request termino.
  useEffect(() => {
    if (!isGenerating) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    setElapsedSeconds(0);
    const ticker = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(ticker);
  }, [isGenerating]);

  // Sync threads from backend on login
  useEffect(() => {
    if (!user) return;
    let isMounted = true;

    const loadBackendThreads = async () => {
      try {
        const remoteSummaries = await queryService.getThreads();
        if (!isMounted) return;
        setThreadsError(null);
        setThreadsLoaded(true);
        if (remoteSummaries.length > 0) {
          setThreads((prev) => {
            const prevMap = new Map(prev.map((t) => [t.id, t]));
            return remoteSummaries.map((s) => ({
              id: s.id,
              title: s.title,
              timestamp: s.updated_at ? new Date(s.updated_at).toLocaleDateString() : 'Reciente',
              dateGroup: getDateGroup(s.updated_at),
              connection_id: s.connection_id,
              results: prevMap.get(s.id)?.results || [],
            }));
          });
        }
      } catch (err: any) {
        if (!isMounted) return;
        // El cache local sigue en pantalla (es historial de este navegador, no
        // del servidor), pero la UI lo dice: no se presento como confirmado.
        setThreadsError(err.message || 'No se pudo consultar el historial en el servidor.');
      }
    };

    loadBackendThreads();
    return () => {
      isMounted = false;
    };
  }, [user, storageKey]);

  // Check for shared thread URL parameter ?thread=<id>
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const sharedId = params.get('thread');
    if (sharedId) {
      queryService.getSharedThread(sharedId).then((sharedThread) => {
        if (sharedThread) {
          const formatted: FullThread = {
            id: sharedThread.id,
            title: sharedThread.title,
            timestamp: sharedThread.updated_at ? new Date(sharedThread.updated_at).toLocaleDateString() : 'Compartido',
            dateGroup: getDateGroup(sharedThread.updated_at),
            connection_id: sharedThread.connection_id,
            results: sharedThread.results || [],
          };
          setThreads((prev) => {
            if (prev.some((t) => t.id === sharedThread.id)) {
              return prev.map((t) => (t.id === sharedThread.id ? formatted : t));
            }
            return [formatted, ...prev];
          });
          setActiveThreadId(sharedThread.id);
          notify('info', `Consulta compartida cargada: "${sharedThread.title}"`);
        } else {
          notify('warning', 'El enlace compartido no trae historial: esa conversación no existe o fue eliminada.');
        }
      }).catch(() => {
        notify('error', 'No se pudo consultar la conversación compartida (sin permiso o error de red).');
      });
    }
  }, []);

  // Persist threads to localStorage on change
  // Lo que se escribe es el snapshot recortado (ver PERSISTED_THREADS_LIMIT), no
  // `threads`. Lo que se muestra sigue siendo `threads` completo: el recorte es
  // del disco, no de la pantalla.
  const lastPersistedRef = useRef<{ key: string; payload: string } | null>(null);
  // Un aviso por clave, no uno por cambio de `threads`: sin esto, mientras la
  // cuota siga llena cada consulta nueva vuelve a disparar la misma advertencia.
  const quotaWarnedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    const payload = JSON.stringify(threads.slice(0, PERSISTED_THREADS_LIMIT));
    // Si el serializado no cambio, no se reescribe: el montaje relee lo que
    // acaba de leer del mismo lugar y el sync del backend reordena el mismo
    // historial. El string sirve de comparador porque dos arrays con el mismo
    // contenido dan el mismo JSON.
    if (lastPersistedRef.current?.key === storageKey && lastPersistedRef.current.payload === payload) {
      return;
    }
    try {
      localStorage.setItem(storageKey, payload);
      lastPersistedRef.current = { key: storageKey, payload };
      quotaWarnedKeyRef.current = null;
    } catch (err) {
      // Cualquier otro fallo (navegacion privada que tira SecurityError al
      // escribir) se sigue ignorando como antes; lo unico que se avisa es la
      // cuota agotada, que es el caso donde el historial se pierde en silencio.
      if (!isQuotaError(err) || quotaWarnedKeyRef.current === storageKey) return;
      quotaWarnedKeyRef.current = storageKey;
      notify(
        'warning',
        'Este navegador dejó de guardar el historial: se superó el límite de almacenamiento local (~5 MB). Lo que ves en pantalla y lo que el servidor ya confirmó siguen ahí, pero si recargás esta página se pierde lo que no esté guardado en el servidor.'
      );
    }
  }, [threads, storageKey, notify]);

  // Auto-scroll on new message. La oferta de proyeccion entra aca: es lo
  // unico que cambia en pantalla cuando esa pregunta se envia (no hay
  // `pendingPrompt` ni streaming detras).
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [threads, isGenerating, activeThreadId, pendingPrompt, projectionSuggestion]);

  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId),
    [threads, activeThreadId]
  );

  // `threads` se muta creando arrays y objetos nuevos (nunca in situ), asi que la
  // referencia es un comparador legitimo: si no cambio `threads`, estos objetos
  // son los mismos de ayer y el memo es correcto.
  const sidebarThreads: ChatThread[] = useMemo(
    () =>
      threads.map((t) => ({
        id: t.id,
        title: t.title,
        timestamp: t.timestamp,
        dateGroup: t.dateGroup,
      })),
    [threads]
  );

  const handleSelectThread = useCallback(async (id: string) => {
    setActiveThreadId(id);
    const target = threads.find((t) => t.id === id);
    if (target && target.results.length === 0) {
      try {
        const detail = await queryService.getThread(id);
        if (detail?.results?.length) {
          setThreads((prev) =>
            prev.map((t) => (t.id === id ? { ...t, results: detail.results } : t))
          );
        }
      } catch {
        // Los datos locales siguen en pantalla (son de este navegador), pero el
        // usuario se entera de que el servidor no respondio.
        notify('error', 'No se pudo cargar la conversación desde el servidor; se muestran los datos locales.');
      }
    }
  }, [threads, notify]);

  // El estado del motor guarda UN intento (una pregunta pendiente, una narrativa,
  // un reloj). Cuando el usuario arranca otro intento en otra conversacion, la
  // pregunta fallida anterior ya no se puede volver a mostrar: su hilo, nacido
  // sin resultados y sin copia en el servidor, quedaba en el historial como
  // conversacion vacia que al abrirla mostraba la bienvenida con el hilo
  // resaltado. Ese es el unico caso en que se quita. Un hilo con resultados
  // (multi-turno) no se toca: devuelve el id solo cuando de verdad quito la
  // conversacion.
  const discardAbandonedAttemptThread = useCallback((): string | null => {
    const id = attemptThreadIdRef.current;
    if (!id) return null;
    setAttemptThread(null);
    const target = threadsRef.current.find((t) => t.id === id);
    if (!target || target.results.length > 0) return null;
    setThreads((prev) => prev.filter((t) => t.id !== id));
    return id;
  }, [setAttemptThread]);

  const handleNewThread = useCallback(() => {
    // "En vuelo" es el controller Y el estado generando: el controller sigue
    // seteado despues de un exito, y solo lo limpia Cancelar.
    const inFlight = abortControllerRef.current !== null && isGenerating;
    // Una consulta en curso NO se corta: sigue y su respuesta queda en SU
    // conversacion; el usuario ve la bienvenida y pregunta cuando termine (el
    // input se lo dice).
    if (!inFlight) {
      // Nada corriendo: lo parcial de un intento ya terminado no puede quedar
      // pegado al proximo. La tarjeta de un intento fallido (si la hay) se
      // conserva en su hilo, con su pregunta y su Reintentar.
      discardStreamingNarrative();
    } else {
      notify(
        'info',
        'La consulta anterior sigue en curso: su respuesta va a quedar en esa conversación. Vas a poder preguntar algo nuevo cuando termine.'
      );
    }

    // La oferta de proyeccion no vive en ningun hilo: "Nueva consulta" la
    // descarta, igual que la vieja tarjeta de prediccion.
    setProjectionSuggestion(null);

    setPromptInput('');
    setActiveTraceability(null);
    setActiveThreadId(null);
    setTimeout(() => {
      promptTextareaRef.current?.focus();
    }, 50);
  }, [discardStreamingNarrative, isGenerating, notify, setAttemptThread]);

  const handleDeleteThread = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const previous = threads;
    setThreads((prev) => prev.filter((t) => t.id !== id));
    if (activeThreadId === id) {
      setActiveThreadId(null);
    }
    try {
      const outcome = await queryService.deleteThread(id);
      notify(
        outcome === 'already_absent' ? 'warning' : 'info',
        outcome === 'already_absent'
          ? 'Esa conversación no estaba en el servidor; solo se quitó de este navegador.'
          : 'Conversación eliminada del historial.'
      );
    } catch (err: any) {
      // El servidor no confirmó nada: se revierte el borrado optimista en vez
      // de mostrar un éxito que un reload deshace.
      setThreads(previous);
      if (activeThreadId === id) {
        setActiveThreadId(id);
      }
      notify('error', err.message || 'No se pudo eliminar la conversación en el servidor; sigue en el historial.');
    }
  }, [threads, activeThreadId, notify]);

  // Keyboard shortcuts (Ctrl+N, Ctrl+K)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // El provider vive toda la sesion, tambien fuera del chat: sin este guard
      // Ctrl+N/Ctrl+K actuarian en /admin y /settings.
      if (!window.location.hash.startsWith('#/chat')) return;
      if ((e.ctrlKey || e.metaKey) && (e.key === 'n' || e.key === 'N')) {
        e.preventDefault();
        handleNewThread();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setIsMobileHistoryOpen(true);
        setTimeout(() => {
          searchInputRef.current?.focus();
        }, 100);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleNewThread]);

  const handleEditPrompt = useCallback((question: string) => {
    setPromptInput(question);
    promptTextareaRef.current?.focus();
    notify('info', 'Pregunta cargada en el editor para reintentar.');
  }, [notify]);

  // Deps estables durante todo el stream salvo en los dos flancos (arranque y
  // `finally`): por eso el token no cambia la identidad de este callback ni la
  // del resto de lo que el hook expone.
  const handleSendPrompt = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isGenerating) return;

    // Ruta de PROYECCION: la pregunta no viaja a ningun endpoint.
    //
    // El motor del chat no puede proyectar: el LLM solo tiene filas historicas
    // y cualquier cifra futura seria inventada. La proyeccion real corre en
    // /projections (serie real, banda de error medida y backtests), asi que el
    // chat deja la pregunta como oferta para abrirla ahi con el input ya
    // cargado — sin request, sin reloj y sin fingir que algo se calcula.
    //
    // La oferta NO entra al hilo de chat: no es un `QueryResult` y forzarla
    // dentro de la lista de mensajes haria que el panel de trazabilidad y los
    // exportadores leyeran campos que no existen.
    if (isPredictionQuestion(trimmed)) {
      setProjectionSuggestion(trimmed);
      setPromptInput('');
      return;
    }

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Cualquier otra pregunta sigue por el camino del LLM.
    setProjectionSuggestion(null);

    // ID impredecible, pero la seguridad NO depende de que sea secreto: el
    // endpoint compartido revalida el SQL contra el rol del lector. El UUID evita
    // que dos usuarios generen el mismo `thread-<ms>` y que un ID sea adivinable.
    // `randomUUID` exige contexto seguro (HTTPS o localhost); el fallback cubre
    // el despliegue por HTTP en LAN, donde el navegador no lo expone.
    const currentThreadId = activeThreadId
      || globalThis.crypto?.randomUUID?.()
      || `thread-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    // El estado del motor guarda UN intento: si el anterior quedo fallido en
    // otra conversacion y el usuario arranca otro, esa pregunta ya no se puede
    // volver a mostrar, asi que su hilo vacio no se queda en el historial.
    if (attemptThreadIdRef.current && attemptThreadIdRef.current !== currentThreadId) {
      discardAbandonedAttemptThread();
    }
    // Desde aca este intento es el dueño de la tarjeta de "procesando" y de sus
    // estados: la tarjeta se muestra solo en SU conversacion.
    setAttemptThread(currentThreadId);
    attemptStartedAtRef.current = Date.now();
    const threadTitle = trimmed.length > 32 ? `${trimmed.substring(0, 30)}...` : trimmed;

    if (!activeThreadId) {
      const newTh: FullThread = {
        id: currentThreadId,
        title: threadTitle,
        timestamp: 'Ahora',
        dateGroup: 'Hoy',
        connection_id: activeConnectionId || 1,
        results: [],
      };
      setThreads((prev) => [newTh, ...prev]);
      setActiveThreadId(currentThreadId);
    }

    // Extract multi-turn conversation history from active thread (last 2 turns)
    const conversationHistory: Array<{ question: string; sql?: string }> = [];
    if (activeThread && activeThread.results.length > 0) {
      for (const res of activeThread.results.slice(-2)) {
        conversationHistory.push({
          question: res.question,
          sql: res.traceability?.sql_executed,
        });
      }
    }

    setPendingPrompt(trimmed);
    setPendingError(null);
    setPendingErrorKind(null);
    setPromptInput('');
    setIsGenerating(true);
    setElapsedSeconds(0);
    discardStreamingNarrative();

    const startedAt = Date.now();
    let timedOut = false;
    // Titulo del hilo que recibe el resultado, para avisar donde quedo cuando el
    // usuario no esta mirando esa conversacion.
    let completedThreadTitle: string | null = null;
    // Motivo del fallo del intento, si lo hubo. Se decide en el `catch` y lo
    // aplica el `finally` (que corre siempre), para conservar la pregunta en
    // pantalla en vez de vaciarla.
    let attemptError: string | null = null;

    const timeoutId = setTimeout(() => {
      if (abortControllerRef.current === controller) {
        timedOut = true;
        controller.abort();
        // Que paso y que no se resuelve solo: el motor local sigue vivo, la
        // peticion ya habia salido del navegador y este corte solo abandona la
        // espera. Decir "cancelada" a secas invita a pensar que no se ejecuto
        // nada, y el backend pudo haber generado y ejecutado la consulta igual.
        notify(
          'warning',
          `Se cortó la espera a los ${REQUEST_TIMEOUT_MS / 1000} s. El modelo local no llegó a responder a tiempo: la pregunta ya se había enviado y el servidor pudo seguir trabajándola hasta terminar, pero su resultado se descartó. Podés reintentarla; para consultas más pesadas conviene una pregunta más acotada.`
        );
      }
    }, REQUEST_TIMEOUT_MS);

    try {
      // Primero el stream (lee la narrativa antes de tiempo), con caida al
      // camino que ya funcionaba. Los dos caminos comparten el mismo
      // AbortController y el mismo timeout de arriba, asi que cancelar y el
      // corte por tiempo cortan los dos igual.
      //
      // El fallback reintenta la consulta desde cero: es una segunda llamada al
      // LLM, no una continuacion. Solo ocurre si el stream no produjo un
      // `result` completo.
      let newResult: QueryResult | null = null;

      if (streamingEnabled) {
        try {
          newResult = await queryService.sendQueryStreaming(
            trimmed,
            activeConnectionId || undefined,
            conversationHistory.length > 0 ? conversationHistory : undefined,
            controller.signal,
            (chunk) => {
              // Solo se acumula si este controller sigue siendo el vigente: si
              // el usuario ya cancelo y mando otra consulta, el texto viejo no
              // se escribe en la burbuja de la nueva.
              if (abortControllerRef.current === controller) {
                // El buffer agrupa y programa su propio volcado; el guard de
                // arriba es lo que impide que un intento ya cancelado escriba.
                streamBufferRef.current.push(chunk);
              }
            },
            { baseUrl: settings?.ollama_url, model: settings?.ollama_model }
          );
        } catch (streamErr: any) {
          // Cancelar y el timeout NO son "el stream no funciono": son decisiones
          // del usuario o el reloj, y ya tienen su propio mensaje. Reintentar
          // aca lanzaria una segunda consulta que el usuario no pidio.
          if (streamErr?.name === 'AbortError' || timedOut) {
            throw streamErr;
          }
          // Cualquier otro fallo del stream es recuperable: se apaga el stream
          // para no insistir y se va por el camino de siempre.
          setStreamingEnabled(false);
        }
      }

      if (!newResult) {
        newResult = await queryService.sendQuery(
          trimmed,
          activeConnectionId || undefined,
          conversationHistory.length > 0 ? conversationHistory : undefined,
          controller.signal,
          { baseUrl: settings?.ollama_url, model: settings?.ollama_model }
        );
      }

      // Llego la verdad: la narrativa parcial se descarta y la muestra el
      // resultado completo, que es el unico que se commitea al hilo.
      discardStreamingNarrative();

      const vStatus = newResult.traceability?.validation_status;
      // El backend emite 'APROBADO', 'RECHAZADO_*', 'ERROR_*' o null cuando
      // no hay un estado que afirmar. Null no es una aprobacion.
      if (vStatus && !vStatus.startsWith('APROBADO')) {
        if (vStatus.includes('RECHAZADO')) {
          if (vStatus.toUpperCase().includes('IA LOCAL NO DISPONIBLE')) {
            notify('warning', 'Motor IA local no disponible: la consulta no se pudo procesar.');
          } else {
            notify('warning', `Consulta bloqueada por AST Guardrail (${vStatus}) según perfil ${userRole}.`);
          }
        } else if (vStatus.includes('ERROR')) {
          notify('error', `Error al procesar consulta SQL (${vStatus}).`);
        }
      }

      // El merge y el POST van fuera del updater y leen `threadsRef`: el updater
      // de React no corre necesariamente en el punto de la llamada (su "eager
      // state" se saltea cuando el fiber ya tiene lanes pendientes — p. ej. la
      // narrativa del stream recien limpiada mas arriba), y cuando corre recien
      // en el render, `newResults` quedaba vacio y el POST no salia nunca: el
      // hilo vivia solo en este navegador y su DELETE daba 404. DENTRO del
      // updater el POST tampoco puede ir: StrictMode lo invoca dos veces en dev
      // -> dos POST.
      const mergeTarget = threadsRef.current.find((t) => t.id === currentThreadId);
      if (mergeTarget) {
        completedThreadTitle = mergeTarget.title;
        const newResults = [...mergeTarget.results, newResult];
        setThreads((prev) =>
          prev.map((t) => (t.id === currentThreadId ? { ...t, results: newResults } : t))
        );
        queryService.saveThread({
          id: currentThreadId,
          title: mergeTarget.title,
          connection_id: activeConnectionId || 1,
          results: newResults,
        }).then((saved) => {
          if (!saved) {
            notify('warning', 'La conversación no se pudo guardar en el servidor; queda solo en este navegador.');
          }
        });
      }
    } catch (err: any) {
      // Si este controller ya no es el vigente, hay otro intento en marcha: este
      // ya lo cancelo el usuario (Ctrl+N o el boton Cancelar) y su error no es lo
      // que esta mirando. Antes el `catch` decia "Cancelaste la consulta" por un
      // Ctrl+N que no fue cancelar, y el fallo del intento viejo se reportaba
      // encima de la consulta nueva.
      if (abortControllerRef.current !== controller) return;

      // AbortError tiene dos causas y dos mensajes distintos. El timeout ya
      // notificó con su explicación, asi que no se duplica. Si fue el botón de
      // cancelar, se dice qué se canceló y qué no.
      if (err.name === 'AbortError') {
        if (!timedOut) {
          notify(
            'info',
            `Cancelaste la consulta a los ${Math.round((Date.now() - startedAt) / 1000)} s. La espera en este navegador se cortó, pero la petición ya había llegado al servidor y el motor local puede seguir trabajándola hasta terminar. La conversación no cambió.`
          );
          return;
        }
        // Timeout: el aviso ya lo dio el temporizador; la pregunta NO se tira.
        // `attemptError` hace que el `finally` la conserve en pantalla con el
        // motivo y el boton de reintentar.
        attemptError = `El motor local no respondió en ${REQUEST_TIMEOUT_MS / 1000} s. Podés reintentar la misma pregunta o reformularla.`;
        return;
      }
      const errorMessage = err.message || 'Error al conectar con la base de datos o el motor LLM local.';
      attemptError = errorMessage;
      notify('error', errorMessage);
    } finally {
      clearTimeout(timeoutId);
      // Mismo guard que arriba: sin esto el `finally` de un intento ya
      // terminado apagaba `isGenerating` y vaciaba `pendingPrompt` del intento
      // SIGUIENTE, que queda en pantalla "idle" mientras corre y con su
      // narrativa borrada.
      //
      // El guard NO cubre `discardStreamingNarrative`, y es deliberado: el buffer
      // y el temporizador de volcado son COMPARTIDOS entre intentos. Cuando este
      // `finally` corre, ese buffer ya contiene (o va a contener) texto de la
      // consulta vigente, no del intento que termina. Limpiarlo aqui seria el
      // bug original al reves. Lo limpia quien PARA el intento, antes de que
      // empiece el siguiente: `handleCancelPrompt` y `handleNewThread` (que ya
      // no corta consultas: solo predicciones o intentos ya terminados).
      if (abortControllerRef.current !== controller) return;
      setIsGenerating(false);
      if (attemptError) {
        // La pregunta se conserva: el hilo queda con la pregunta visible, el
        // motivo del fallo y el boton para reintentar. Antes `pendingPrompt` se
        // vaciaba siempre y la pantalla volvia a la bienvenida como si la
        // pregunta nunca hubiera existido.
        setPendingError(attemptError);
        setPendingErrorKind('fail');
      } else {
        setPendingPrompt(null);
        setPendingError(null);
        setPendingErrorKind(null);
        // El intento llego a resultado: su hilo ya tiene contenido. Si el
        // usuario no esta mirando ESA conversacion (le dio a Nueva consulta
        // mientras corria), se le avisa donde quedo la respuesta; si esta
        // mirandola, el resultado aparece ahi mismo.
        if (completedThreadTitle && activeThreadIdRef.current !== currentThreadId) {
          notify(
            'success',
            `Terminó la consulta anterior: la respuesta quedó en "${completedThreadTitle}".`
          );
        }
        setAttemptThread(null);
        attemptStartedAtRef.current = null;
      }
      // La narrativa parcial nunca sobrevive al intento: o la reemplazo el
      // resultado completo, o se cae el stream y se va por `sendQuery`. Lo que
      // se descarto no se guarda en ningun lado (ni el estado ni el buffer).
      discardStreamingNarrative();
    }
  }, [
    activeConnectionId,
    activeThread,
    activeThreadId,
    discardAbandonedAttemptThread,
    discardStreamingNarrative,
    isGenerating,
    notify,
    setAttemptThread,
    settings,
    streamingEnabled,
    userRole,
  ]);

  // Cancelar la consulta en curso. Aborta de verdad (el signal llega al fetch,
  // ver query_service.executeQuery): el `finally` de handleSendPrompt no corre,
  // asi que esta funcion deja la UI usable y CONSERVA la pregunta en su hilo con
  // el motivo y el boton de reintentar (mismo trato que el timeout). No se
  // afirma que el trabajo del servidor se deshizo, porque no se deshizo.
  const handleCancelPrompt = useCallback(() => {
    const controller = abortControllerRef.current;
    if (!controller) return;
    abortControllerRef.current = null;
    controller.abort();
    const seconds = Math.max(
      1,
      Math.round((Date.now() - (attemptStartedAtRef.current ?? Date.now())) / 1000)
    );
    setPendingError(
      `Cancelaste la consulta a los ${seconds} s. La espera en este navegador se cortó, pero la petición ya había llegado al servidor y el motor local puede seguir trabajándola hasta terminar. La pregunta quedó acá: podés reintentarla.`
    );
    setPendingErrorKind('cancel');
    setIsGenerating(false);
    // Lo que se habia leido del stream no es una respuesta: es texto sin
    // confirmar. Cancelar no lo deja pegado en el chat.
    discardStreamingNarrative();
  }, [discardStreamingNarrative]);

  const handleFeedback = useCallback(async (
    result: QueryResult,
    rating: 'positive' | 'negative',
    comment?: string
  ) => {
    const res = await queryService.sendFeedback({
      audit_log_id: result.traceability?.audit_log_id || result.audit_log_id,
      question: result.question,
      sql: result.traceability?.sql_executed,
      connection_id: activeConnectionId || 1,
      rating,
      comment,
    });
    if (res.success) {
      notify('success', res.message);
    } else {
      notify('warning', res.message);
    }
    return res;
  }, [activeConnectionId, notify]);

  // Descartar la oferta de proyeccion sin abrir la seccion (la X de la tarjeta).
  const dismissProjectionSuggestion = useCallback(() => setProjectionSuggestion(null), []);

  // El objeto que devuelve el hook es una referencia nueva en cada render, y eso
  // invalida cualquier `React.memo` o comparacion por referencia aguas abajo
  // aunque no haya cambiado nada. Se congela mientras ninguna entrada cambie.
  // El hook ya expone todo lo que la pagina necesita, solo falta que las
  // referencias sean estables. `longWaitNotice` se calcula aca para que sea un
  // booleano y no una
  // expresion suelta imposible de comparar.
  const longWaitNotice = elapsedSeconds >= LONG_WAIT_NOTICE_SECONDS;

  return useMemo(() => ({
    user,
    userRole,
    settings,
    promptInput,
    setPromptInput,
    isGenerating,
    elapsedSeconds,
    longWaitNotice,
    streamingNarrative,
    activeTraceability,
    setActiveTraceability,
    isMobileHistoryOpen,
    setIsMobileHistoryOpen,
    activeDatabaseName,
    activeConnectionId,
    connectors,
    promptSuggestions,
    threads,
    threadsLoaded,
    threadsError,
    activeThreadId,
    activeThread,
    sidebarThreads,
    pendingPrompt,
    pendingError,
    pendingErrorKind,
    attemptThreadId,
    projectionSuggestion,
    dismissProjectionSuggestion,
    chatBottomRef,
    searchInputRef,
    promptTextareaRef,
    handleSelectThread,
    handleNewThread,
    handleDeleteThread,
    handleSendPrompt,
    handleCancelPrompt,
    handleSelectConnection,
    handleEditPrompt,
    handleFeedback,
  }), [
    user,
    userRole,
    settings,
    promptInput,
    isGenerating,
    elapsedSeconds,
    longWaitNotice,
    streamingNarrative,
    activeTraceability,
    isMobileHistoryOpen,
    activeDatabaseName,
    activeConnectionId,
    connectors,
    promptSuggestions,
    threads,
    threadsLoaded,
    threadsError,
    activeThreadId,
    activeThread,
    sidebarThreads,
    pendingPrompt,
    pendingError,
    pendingErrorKind,
    attemptThreadId,
    projectionSuggestion,
    dismissProjectionSuggestion,
    handleSelectThread,
    handleNewThread,
    handleDeleteThread,
    handleSendPrompt,
    handleCancelPrompt,
    handleSelectConnection,
    handleEditPrompt,
    handleFeedback,
  ]);
}

// El motor vive en un provider montado en ProtectedLayout (AppRouter), no en
// ChatDashboardPage: asi navegar a otra ruta no destruye el estado (hilos, hilo
// activo, pregunta pendiente, stream, reloj) y al volver la conversacion sigue
// ahi. Antes el estado moria con la pagina y desaparecia hasta que la respuesta
// en vuelo terminaba de persistirse en el servidor.
const ChatEngineContext = createContext<ReturnType<typeof useChatEngineInternal> | null>(null);

export function ChatEngineProvider({ children }: { children: React.ReactNode }) {
  const engine = useChatEngineInternal();
  return <ChatEngineContext.Provider value={engine}>{children}</ChatEngineContext.Provider>;
}

export function useChatEngine() {
  const engine = useContext(ChatEngineContext);
  if (!engine) throw new Error('useChatEngine requiere ChatEngineProvider (montado en ProtectedLayout).');
  return engine;
}

