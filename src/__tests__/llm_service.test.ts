import { describe, it, expect, beforeEach, vi } from 'vitest';

// Contrato del payload LLM: en central/external el llamador NO manda
// base_url/model_name y el backend usa el motor de la empresa. El bug que estos
// tests evitan es volver a enviar siempre la config vieja de localStorage y
// creer que el backend la esta usando. Tambien cubren `active_url`, que es lo
// que permite mostrar el endpoint real en Ajustes.
const apiClient = {
  get: vi.fn(),
  post: vi.fn(),
};

vi.mock('../shared/api/api_client', () => ({
  apiClient,
}));

let llmClientService: typeof import('../features/chat/services/llm_service').llmClientService;

const connectionResult = {
  success: true,
  message: 'ok',
  available_models: ['qwen2.5-coder:7b'],
  latency_ms: 12,
  active_url: 'http://ollama:11434',
};

beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  ({ llmClientService } = await import('../features/chat/services/llm_service'));
});

describe('llmClientService policy', () => {
  it('lee la politica de la empresa', async () => {
    const policy = {
      mode: 'central' as const,
      allow_user_llm: false,
      default_model: 'qwen2.5-coder:7b',
      provider: 'ollama',
    };
    apiClient.get.mockResolvedValue({ data: policy });

    await expect(llmClientService.getPolicy()).resolves.toEqual(policy);
    expect(apiClient.get).toHaveBeenCalledWith('/system/llm-policy');
  });
});

describe('llmClientService.testConnection', () => {
  it('en central omite base_url y model_name', async () => {
    apiClient.post.mockResolvedValue({ data: connectionResult });

    await expect(llmClientService.testConnection('ollama')).resolves.toEqual(connectionResult);
    expect(apiClient.post).toHaveBeenCalledWith('/llm/test-connection', { provider: 'ollama' });
  });

  it('en user manda la URL y el modelo normalizados', async () => {
    apiClient.post.mockResolvedValue({ data: connectionResult });

    await llmClientService.testConnection('ollama', '  http://192.168.1.5:11434  ', '  qwen2.5-coder:7b  ');

    expect(apiClient.post).toHaveBeenCalledWith('/llm/test-connection', {
      provider: 'ollama',
      base_url: 'http://192.168.1.5:11434',
      model_name: 'qwen2.5-coder:7b',
    });
  });

  it('no manda campos vacios como si fueran configuracion', async () => {
    apiClient.post.mockResolvedValue({ data: connectionResult });

    await llmClientService.testConnection(' ', '', '   ');

    expect(apiClient.post).toHaveBeenCalledWith('/llm/test-connection', { provider: 'ollama' });
  });
});

describe('llmClientService.testCompletion', () => {
  it('en central manda solo prompt y provider', async () => {
    const result = { ...connectionResult, completion_text: 'SELECT 1' };
    apiClient.post.mockResolvedValue({ data: result });

    await expect(llmClientService.testCompletion('SELECT 1', 'ollama')).resolves.toEqual(result);
    expect(apiClient.post).toHaveBeenCalledWith('/llm/test-completion', {
      prompt: 'SELECT 1',
      provider: 'ollama',
    });
  });

  it('en user agrega la config del endpoint', async () => {
    const result = { ...connectionResult, completion_text: 'SELECT 1' };
    apiClient.post.mockResolvedValue({ data: result });

    await llmClientService.testCompletion('SELECT 1', 'ollama', 'http://10.0.0.5:11434', 'qwen2.5-coder:7b');

    expect(apiClient.post).toHaveBeenCalledWith('/llm/test-completion', {
      prompt: 'SELECT 1',
      provider: 'ollama',
      base_url: 'http://10.0.0.5:11434',
      model_name: 'qwen2.5-coder:7b',
    });
  });
});
