import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mismo patron que query_service.test.ts: el apiClient se sustituye entero y los
// tests EJECUTAN el metodo del servicio. Lo que se protege aca:
// - el contrato HTTP exacto de /ml/projections (endpoints, payload, omision de
//   connection_id sin conexion elegida);
// - que ningun fallo se convierta en una respuesta fabricada: un 500 o un 403
//   de gobernanza tienen que llegar como error, y un available=false real del
//   backend tiene que pasar tal cual (sin rellenar ceros).
const apiClient = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  rawStream: vi.fn(),
};

vi.mock('../shared/api/api_client', () => ({
  apiClient,
  parseSseEvent: () => null,
}));

const httpError = (status: number, detail = 'fallo') => {
  const err: any = new Error(detail);
  err.response = { status, data: { detail } };
  return err;
};

let projectionService: typeof import('../features/projections/services/projection_service').projectionService;

beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  const mod = await import('../features/projections/services/projection_service');
  projectionService = mod.projectionService;
});

describe('estimate: la estimacion es barata y su fallo no se disimula', () => {
  it('pide /ml/projections/estimate con pregunta y horizonte', async () => {
    apiClient.post.mockResolvedValue({ data: { estimated_seconds: 25, source: 'medido' } });

    const est = await projectionService.estimate('¿cuánto vendo en 6 meses?', 6);

    expect(apiClient.post).toHaveBeenCalledWith('/ml/projections/estimate', {
      question: '¿cuánto vendo en 6 meses?',
      horizon: 6,
    });
    expect(est).toEqual({ estimated_seconds: 25, source: 'medido' });
  });

  it('manda horizon null para Auto y conserva el horizonte asumido', async () => {
    apiClient.post.mockResolvedValue({
      data: { estimated_seconds: 60, source: 'fallback', horizon_used: 12 },
    });

    const est = await projectionService.estimate('p', null);

    expect(apiClient.post).toHaveBeenCalledWith('/ml/projections/estimate', {
      question: 'p',
      horizon: null,
    });
    expect(est.horizon_used).toBe(12);
  });

  it('propaga el fallo en vez de devolver una estimacion inventada', async () => {
    apiClient.post.mockRejectedValue(httpError(500, 'Fallo interno'));

    await expect(projectionService.estimate('p', 6)).rejects.toThrow('Fallo interno');
  });
});

describe('run: contrato del pedido', () => {
  it('manda connection_id cuando hay conexion elegida', async () => {
    apiClient.post.mockResolvedValue({ data: { available: true } });

    await projectionService.run('proyectá ventas', 9, 6);

    expect(apiClient.post).toHaveBeenCalledWith('/ml/projections/run', {
      question: 'proyectá ventas',
      horizon: 6,
      connection_id: 9,
    });
  });

  it('omite connection_id cuando no hay conexion: el backend usa su default', async () => {
    apiClient.post.mockResolvedValue({ data: { available: true } });

    await projectionService.run('proyectá ventas', null, 12);

    const [url, payload] = apiClient.post.mock.calls[0];
    expect(url).toBe('/ml/projections/run');
    expect(payload).toEqual({ question: 'proyectá ventas', horizon: 12 });
    expect('connection_id' in payload).toBe(false);
  });

  it('manda horizon null explicito cuando el usuario elige Auto', async () => {
    apiClient.post.mockResolvedValue({ data: { available: true } });

    await projectionService.run('proyectá ventas', 9, null);

    expect(apiClient.post).toHaveBeenCalledWith('/ml/projections/run', {
      question: 'proyectá ventas',
      horizon: null,
      connection_id: 9,
    });
  });
});

describe('run: errores y honestidad', () => {
  it('propaga el detail del backend (403 de gobernanza) accesible como lo leen las páginas', async () => {
    apiClient.post.mockRejectedValue(
      httpError(403, 'Tu rol no puede proyectar sobre esta tabla.')
    );

    await expect(projectionService.run('p', null, 6)).rejects.toThrow(
      'Tu rol no puede proyectar sobre esta tabla.'
    );

    try {
      await projectionService.run('p', null, 6);
    } catch (err: any) {
      expect(err.response.data.detail).toBe('Tu rol no puede proyectar sobre esta tabla.');
    }
  });

  it('un available=false del backend pasa tal cual, con su motivo y su serie', async () => {
    const payload = {
      available: false,
      reason: 'Se necesitan al menos 6 periodos para proyectar y hay 3.',
      series: [{ periodo: '2026-01', valor: 5 }],
      steps: [],
    };
    apiClient.post.mockResolvedValue({ data: payload });

    const res = await projectionService.run('p', 9, 6);

    expect(res).toBe(payload);
    expect(res.available).toBe(false);
    expect(res.reason).toContain('Se necesitan al menos 6 periodos');
  });
});
