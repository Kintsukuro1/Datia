import { apiClient } from '../../../shared/api/api_client';

/**
 * Politica LLM que aplica la empresa a esta instalacion. `allow_user_llm` es
 * false en central/external: los campos de Ajustes se deshabilitan y el backend
 * ignora la URL/modelo que mande el navegador. En `user` se habilitan y el
 * backend valida la URL antes de usarla.
 */
export interface LLMPolicy {
  mode: 'central' | 'external' | 'user';
  allow_user_llm: boolean;
  default_model: string;
  provider: string;
}

export interface LLMConnectionTestResult {
  success: boolean;
  message: string;
  available_models: string[];
  latency_ms: number;
  /** URL que el backend uso de verdad. En central/external es la de la empresa. */
  active_url?: string;
}

export interface LLMCompletionTestResult {
  success: boolean;
  completion_text: string;
  latency_ms: number;
  message: string;
  /** URL que el backend uso de verdad. */
  active_url?: string;
}

/**
 * El diagnostico va SIEMPRE por el backend.
 *
 * Antes, cuando el POST a `/llm/test-connection` o `/llm/test-completion`
 * fallaba, este archivo reintentaba con `fetch` DIRECTO del navegador a
 * http://127.0.0.1:8080, http://localhost:8080, http://localhost:11434 y
 * http://localhost:1234, probando /api/tags, /v1/models, /props, /health y
 * /slots, y mandando el prompt de la inferencia a /v1/chat/completions o
 * /api/generate. Eso rompia la premisa de la app: el backend es el punto de
 * control (RBAC, auditoria, allowlist anti-SSRF en
 * `llm_diagnostic_router.py`), y esas peticiones no pasaban por el. No
 * aparecian en sus logs, no cumplian la allowlist, y quedaban sujetas a CORS/CSP
 * del navegador.
 *
 * El backend cubre lo mismo: `HealthService.check_llm_connectivity` prueba el
 * `base_url` recibido y los puertos 8080/11434/1234 con esos mismos endpoints, y
 * devuelve la misma forma (`success`, `message`, `available_models`,
 * `latency_ms`). Regresiones conocidas al migrar: el backend NO recorta tags
 * `<think>` ni usa el timeout largo de 5 min del navegador. Cuando no puede
 * contactar al servidor devuelve `available_models: []`: no inventa el modelo
 * configurado como si el servidor lo hubiera reportado.
 *
 * Este modulo ahora no captura errores: un fallo del backend sube al llamador,
 * que decide si reintenta otro endpoint o muestra el error.
 */
export const llmClientService = {
  async getPolicy(): Promise<LLMPolicy> {
    const res = await apiClient.get<LLMPolicy>('/system/llm-policy');
    return res.data;
  },

  /**
   * `baseUrl`/`modelName` son opcionales a proposito. En central/external el
   * llamador los omite: el backend usa el motor de la empresa y devuelve su
   * `active_url`. Mandar los valores viejos de localStorage no cambiaria nada
   * (se ignoran), pero la respuesta seria mas dificil de leer.
   */
  async testConnection(
    provider: string,
    baseUrl?: string,
    modelName?: string
  ): Promise<LLMConnectionTestResult> {
    const payload: Record<string, string> = { provider: provider.trim() || 'ollama' };
    if (baseUrl?.trim()) payload.base_url = baseUrl.trim();
    if (modelName?.trim()) payload.model_name = modelName.trim();
    const res = await apiClient.post<LLMConnectionTestResult>('/llm/test-connection', payload);
    return res.data;
  },

  async testCompletion(
    prompt: string,
    provider: string,
    baseUrl?: string,
    modelName?: string
  ): Promise<LLMCompletionTestResult> {
    const payload: Record<string, string> = {
      prompt,
      provider: provider.trim() || 'ollama',
    };
    if (baseUrl?.trim()) payload.base_url = baseUrl.trim();
    if (modelName?.trim()) payload.model_name = modelName.trim();
    const res = await apiClient.post<LLMCompletionTestResult>('/llm/test-completion', payload);
    return res.data;
  }
};
