import { apiClient, getApiErrorMessage, parseSseEvent } from '../../../shared/api/api_client';
import { QueryResult } from '../../../types';

/**
 * Una pregunta es de PROYECCION si pide el futuro de forma explicita.
 *
 * Deliberadamente estrecho. Detecta la intencion solo ante palabras que no
 * admiten otra lectura: el consumidor (el chat) NO manda la pregunta a ningun
 * modelo — ofrece abrir la seccion Proyecciones con la pregunta ya cargada,
 * donde corre el motor determinista. Ampliarlo a "retencion" o "clientes"
 * arrastraria preguntas de personal o de TI a un calculo de churn comercial: el
 * backend tiene aislamiento de dominio por rol y esta regla no debe abrir un
 * hueco al lado de el.
 *
 * Cuando el filtro no matchea, la pregunta sigue al camino normal del LLM, que
 * con la regla anti-proyeccion de `prompts.py` explica que las proyecciones se
 * calculan en la seccion Proyecciones. Perder una frase es degradado; entregar
 * el dato equivocado es el daño que esto previene.
 *
 * `cuanto venderé` no entra a proposito: el grupo terminaria en `é` y el `\b`
 * final de JS es ASCII, asi que no habria frontera y la alternativa no casaria.
 * Las formas no acentuadas ("facturo", "vendero", "venderemos") si.
 */
const PREDICTION_INTENT =
  /\b(predicci[oó]n|predice|prediga|prediga(?:r)?|predici[oó]n|proyecci[oó]n|proyect(?:a|e|es|en)|pron[oó]stico|pronostic(?:a|o|en)|estimar\s+(?:el\s+|la\s+|los\s+)?(?:mes|pr[oó]ximo|proximo|siguiente)|cu[aá]nto\s+(?:voy|vamos)\s+a\s+(?:vender|ganar|facturar|ingresar)|cu[aá]nto\s+(?:facturo|vendero|venderemos)|qu[eé]\s+va\s+a\s+pasar\s+el\s+mes)\b/i;

export function isPredictionQuestion(text: string): boolean {
  return PREDICTION_INTENT.test(text || '');
}

export const queryService = {
  async getSuggestions(connectionId?: number): Promise<string[]> {
    const params: Record<string, any> = {};
    if (connectionId) {
      params.connection_id = connectionId;
    }
    // Sin catch: las sugerencias las decide el backend, que ya tiene las suyas
    // neutras derivadas del rol y del esquema permitido. Devolver [] ante un
    // fallo de red o un 500 es indistinguible de "el servidor no tiene
    // sugerencias" y el consumidor no podia distinguir cargando / vacio /
    // error. Inventar preguntas aca anuncia capacidades que el servidor no
    // ofrece.
    const res = await apiClient.get('/chat/suggestions', { params });
    if (!Array.isArray(res.data?.suggestions)) {
      throw new Error('El servidor no devolvió la lista de sugerencias.');
    }
    return res.data.suggestions;
  },

  /**
   * Consulta simple (sin stream) a `/chat/query`.
   *
   * `userRole` sigue fuera: el rol lo resuelve el backend desde el JWT
   * (`current_user.role.name`, router.py), asi que mandarlo era escribir en el
   * vacio. La config LLM es el caso contrario: `llm_base_url` y `llm_model` SI
   * existen en `QueryRequest` (schemas.py) y viajan en el parametro opcional
   * `llm`; el backend los revalida con la allowlist anti-SSRF antes de tocar el
   * motor y corta con 400 si no pasan. El parametro `settingsOrSignal` era el
   * que hacia la llamada ilegible: cuatro posicionales de dos tipos, y el que
   * nadie leia.
   */
  async sendQuery(
    question: string,
    connectionId?: number,
    conversationHistory?: Array<{ question: string; sql?: string }>,
    signal?: AbortSignal,
    llm?: { baseUrl?: string; model?: string }
  ): Promise<QueryResult> {
    const payload: any = { question };
    if (connectionId) {
      payload.connection_id = connectionId;
    }
    if (conversationHistory && conversationHistory.length > 0) {
      payload.conversation_history = conversationHistory;
    }
    if (llm?.baseUrl) {
      payload.llm_base_url = llm.baseUrl;
    }
    if (llm?.model) {
      payload.llm_model = llm.model;
    }

    try {
      // El signal viaja hasta fetch. Sin esto el AbortController del timeout y el
      // de "nuevo hilo" abortaban una promesa que no tenian nada que abortar: la
      // consulta sigue viva en el servidor y su respuesta tarde o temprano
      // aparecia en un hilo que ya no estaba en pantalla.
      const res = await apiClient.post('/chat/query', payload, signal ? { signal } : undefined);
      return res.data;
    } catch (err: any) {
      // Todo error se propaga. Antes solo se relanzaban 400/401/403 y >=500, y el
      // resto (422, 404, 429, error de red sin `err.response`) caia a un bloque que
      // fabricaba un dashboard ejecutivo completo con KPIs inventados
      // ("$1,029,000", "31.1%") y `validation_status: 'SUCCESS'`. Un fallo de red
      // se leia como un analisis real.
      if (err?.name === 'AbortError') throw err;
      const detail = err?.response?.data?.detail
        || err?.response?.data?.message
        || err?.message;
      throw new Error(detail || 'No se pudo completar la consulta.');
    }
  },

  /**
   * Variante stremeada de la consulta: entrega la narrativa a medida que llega
   * y devuelve la MISMA `QueryResult` que `sendQuery`.
   *
   * Por que `fetch` y no `EventSource`: `EventSource` no soporta POST ni
   * cabeceras, asi que la unica forma de usarlo seria mandar el JWT en la query
   * string, donde queda en los logs del servidor y en el historial del
   * navegador. `fetch` + `response.body.getReader()` si soporta POST, el header
   * `Authorization` (igual que el resto de la API) y `AbortSignal`.
   *
   * Que devuelve y que NO devuelve
   * -----------------------------
   * Devuelve la respuesta COMPLETA, y la unica que se considera verdad es el
   * evento `result`. Los deltas son lectura anticipada.
   *
   * Si el stream se corta, si el servidor manda `error`, o si nunca llega
   * `result`, este metodo LANZA. No devuelve una media respuesta: quien llama
   * reintenta por `sendQuery` y obtiene el resultado entero. Esa es la garantia
   * de que no quede texto a medias pegado en el chat.
   */
  async sendQueryStreaming(
    question: string,
    connectionId?: number,
    conversationHistory?: Array<{ question: string; sql?: string }>,
    signal?: AbortSignal,
    onNarrative?: (chunk: string) => void,
    llm?: { baseUrl?: string; model?: string }
  ): Promise<QueryResult> {
    const payload: any = { question };
    if (connectionId) {
      payload.connection_id = connectionId;
    }
    if (conversationHistory && conversationHistory.length > 0) {
      payload.conversation_history = conversationHistory;
    }
    if (llm?.baseUrl) {
      payload.llm_base_url = llm.baseUrl;
    }
    if (llm?.model) {
      payload.llm_model = llm.model;
    }

    let response: Response;
    try {
      response = await apiClient.rawStream('/chat/query/stream', payload, signal);
    } catch (err: any) {
      // Cortar antes de abrir la conexion (timeout, cancelar, red caida): no
      // hay nada que recuperar de este intento.
      if (err?.name === 'AbortError') throw err;
      throw new Error(
        err?.message || 'No se pudo abrir el stream de la consulta; se usará el camino normal.'
      );
    }

    const body = response.body;
    if (!body) {
      throw new Error('Este navegador no permite leer la respuesta por partes.');
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    let sawResult = false;
    let finalResult: QueryResult | null = null;
    let serverError: string | null = null;

    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // El framing SSE separa eventos con una linea en blanco. Un evento
        // partido entre dos lecturas se queda en `buffer` hasta que llega el
        // resto: no se procesa a medias.
        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
          const rawEvent = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          const event = parseSseEvent(rawEvent);
          if (event) {
            if (event.event === 'delta') {
              // Texto real del modelo. Se entrega para leer antes de tiempo.
              if (typeof event.data?.text === 'string' && event.data.text && onNarrative) {
                onNarrative(event.data.text);
              }
            } else if (event.event === 'result') {
              sawResult = true;
              finalResult = event.data as QueryResult;
            } else if (event.event === 'error') {
              serverError = event.data?.message || 'El servidor no pudo completar la consulta.';
            }
          }
          boundary = buffer.indexOf('\n\n');
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') throw err;
      // Se corto la conexion a mitad. Todo lo leido hasta aqui es parcial y no
      // se commitea: se propaga el error para que el llamador use `/query`.
      throw new Error(
        'La respuesta en vivo se cortó a mitad. Se recuperará la respuesta completa por el camino normal.'
      );
    } finally {
      // Liberar el lock permite que un abort posterior cierre el socket.
      try {
        reader.releaseLock();
      } catch {
        // el reader ya estaba liberado
      }
    }

    if (serverError) {
      throw new Error(serverError);
    }
    if (!sawResult || !finalResult) {
      // El stream termino sin `result`: no hay verdad. Es exactamente el caso en
      // el que el cliente no debe quedarse con la media respuesta que leyo.
      throw new Error(
        'La respuesta en vivo terminó sin resultado completo; se recuperará por el camino normal.'
      );
    }

    return finalResult;
  },

  async getThreads(): Promise<Array<{ id: string; title: string; connection_id: number; message_count: number; updated_at: string }>> {
    // Sin catch: devolver [] ante un 500 o un error de red es indistinguible de
    // "no tenes conversaciones" y el consumidor no podia distinguir cargando /
    // vacio / error. El que llama decide que mostrar.
    const res = await apiClient.get('/chat/threads');
    if (!Array.isArray(res.data)) {
      throw new Error('El servidor no devolvio la lista de conversaciones.');
    }
    return res.data;
  },

  async getThread(id: string): Promise<{ id: string; title: string; connection_id: number; results: QueryResult[]; created_at: string; updated_at: string } | null> {
    try {
      const res = await apiClient.get(`/chat/threads/${id}`);
      return res.data;
    } catch (err: any) {
      // 404 = el hilo no existe (respuesta valida). Cualquier otro status (401,
      // red, 500) es un fallo real y debe propagarse.
      if (err.response?.status === 404) return null;
      throw err;
    }
  },

  async getSharedThread(id: string): Promise<{ id: string; title: string; connection_id: number; results: QueryResult[]; created_at: string; updated_at: string } | null> {
    try {
      const res = await apiClient.get(`/chat/threads/shared/${id}`);
      return res.data;
    } catch (err: any) {
      // 404 = el hilo compartido no existe (respuesta valida). Cualquier otro
      // status (401, red, 500) es un fallo real y debe propagarse.
      if (err.response?.status === 404) return null;
      throw err;
    }
  },

  async toggleGoldenQuery(payload: { question: string; sql: string; connection_id?: number; is_golden: boolean }): Promise<{ success: boolean; message: string; is_golden: boolean }> {
    try {
      const res = await apiClient.post('/chat/golden-query', payload);
      return res.data;
    } catch (err: any) {
      return { success: false, message: err.message || 'Error al actualizar consulta maestra', is_golden: false };
    }
  },

  async saveThread(thread: { id: string; title: string; connection_id?: number; results: QueryResult[] }): Promise<boolean> {
    try {
      await apiClient.post('/chat/threads', thread);
      return true;
    } catch {
      return false;
    }
  },

  /**
   * 'deleted': el servidor confirmó el DELETE.
   * 'already_absent' (404): no estaba en el servidor. No es lo mismo que un
   * borrado confirmado y la UI lo dice distinto.
   * Cualquier otro status (401, red, 500) lanza: el hilo sigue existiendo.
   */
  async deleteThread(id: string): Promise<'deleted' | 'already_absent'> {
    try {
      await apiClient.delete(`/chat/threads/${id}`);
      return 'deleted';
    } catch (err: any) {
      if (err.response?.status === 404) return 'already_absent';
      throw err;
    }
  },

  async sendFeedback(payload: {
    audit_log_id?: number;
    question: string;
    sql?: string;
    connection_id?: number;
    rating: 'positive' | 'negative';
    comment?: string;
  }): Promise<{ success: boolean; message: string; learning_saved: boolean }> {
    try {
      const res = await apiClient.post('/chat/feedback', payload);
      return res.data;
    } catch (err: any) {
      return {
        success: false,
        message: getApiErrorMessage(err, 'Error al registrar calificación.'),
        learning_saved: false,
      };
    }
  },

  async getWidgets(): Promise<Array<{
    id: number;
    title: string;
    connection_id?: number;
    chart_type?: string;
    chart_option_json?: string;
    kpis_json?: string;
    query_text?: string;
    created_at: string;
  }>> {
    // Mismo criterio que getAnomalies: `Tablero (0)` con la API caida es un
    // conteo inventado. Propaga y el consumidor muestra un estado de error.
    const res = await apiClient.get('/chat/widgets');
    if (!Array.isArray(res.data)) {
      throw new Error('El servidor no devolvio los tableros fijados.');
    }
    return res.data;
  },

  async pinWidget(widget: {
    title: string;
    connection_id?: number;
    chart_type?: string;
    chart_option_json?: string;
    kpis_json?: string;
    query_text?: string;
  }): Promise<boolean> {
    try {
      await apiClient.post('/chat/widgets', widget);
      return true;
    } catch {
      return false;
    }
  },

  async unpinWidget(widgetId: number): Promise<boolean> {
    try {
      await apiClient.delete(`/chat/widgets/${widgetId}`);
      return true;
    } catch {
      return false;
    }
  },

  async getAnomalies(connectionId?: number): Promise<{
    count: number;
    anomalies: Array<{
      id: string;
      type: string;
      severity: 'critical' | 'warning' | 'info';
      title: string;
      description: string;
      action_label: string;
      action_route?: string;
      query_prompt?: string;
    }>;
    has_critical: boolean;
  }> {
    const params: Record<string, any> = {};
    if (connectionId) {
      params.connection_id = connectionId;
    }
    // Sin catch y sin `|| {count: 0}`: un fallo de API caia en un objeto vacio y
    // el panel pintaba "0 anomalias" en verde, que es afirmar que no hay
    // anomalias cuando en realidad no se pudo preguntar. Si el 200 no trae el
    // conteo numerico tampoco hay nada que mostrar: se propaga.
    const res = await apiClient.get('/system/anomalies', { params });
    if (typeof res.data?.count !== 'number') {
      throw new Error('El servidor no devolvio el conteo de anomalias.');
    }
    return res.data;
  }
};

