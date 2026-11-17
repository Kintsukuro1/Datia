import { beforeEach, describe, expect, it, vi } from 'vitest';

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

let biService: typeof import('../features/bi/services/bi_service').biService;

beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  const module = await import('../features/bi/services/bi_service');
  biService = module.biService;
});

describe('contrato HTTP del módulo BI', () => {
  it('lista las bases y lee el esquema bajo /bi', async () => {
    apiClient.get.mockResolvedValueOnce({ data: [{ id: 1 }] });
    await biService.listConnections();
    expect(apiClient.get).toHaveBeenLastCalledWith('/bi/connections');

    apiClient.get.mockResolvedValueOnce({ data: { tables: [] } });
    await biService.getSchema(7);
    expect(apiClient.get).toHaveBeenLastCalledWith(
      '/bi/schema',
      expect.objectContaining({ params: { connection_id: 7 } }),
    );
  });

  it('pide el informe con timeout ampliado y parámetros traducidos', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { kpis: {} } });
    await biService.report({
      connection_id: 7,
      table: 'fact_ventas',
      group_column: 'ejecutivo',
      year: 2026,
      month: '2',
    });
    expect(apiClient.get).toHaveBeenLastCalledWith(
      '/bi/report',
      expect.objectContaining({
        timeoutMs: 60_000,
        params: expect.objectContaining({
          connection_id: 7,
          table: 'fact_ventas',
          group_column: 'ejecutivo',
          year: 2026,
          month: '2',
        }),
      }),
    );
  });

  it('usa los endpoints de visuales sin inventar rutas', async () => {
    apiClient.post.mockResolvedValueOnce({ data: { rows: [] } });
    await biService.preview({ connection_id: 1, table: 't' } as never);
    expect(apiClient.post).toHaveBeenLastCalledWith('/bi/query', {
      connection_id: 1,
      table: 't',
    });

    apiClient.post.mockResolvedValueOnce({ data: { id: 3 } });
    await biService.saveVisual({
      title: 'Ventas',
      panel_name: 'Mi panel',
      spec: { connection_id: 1, table: 't' } as never,
    });
    expect(apiClient.post).toHaveBeenLastCalledWith('/bi/visuals', {
      title: 'Ventas',
      panel_name: 'Mi panel',
      spec: { connection_id: 1, table: 't' },
    });

    apiClient.put.mockResolvedValueOnce({ data: { id: 3 } });
    await biService.updateVisual(3, {
      title: 'Ventas editadas',
      panel_name: 'Mi panel',
      spec: { connection_id: 1, table: 't' } as never,
    });
    expect(apiClient.put).toHaveBeenLastCalledWith('/bi/visuals/3', {
      title: 'Ventas editadas',
      panel_name: 'Mi panel',
      spec: { connection_id: 1, table: 't' },
    });

    apiClient.put.mockResolvedValueOnce({ data: [] });
    await biService.reorderVisuals('Mi panel', [2, 1]);
    expect(apiClient.put).toHaveBeenLastCalledWith('/bi/visuals/positions', {
      panel_name: 'Mi panel',
      ids: [2, 1],
    });

    apiClient.post.mockResolvedValueOnce({ data: { rows: [] } });
    await biService.visualData(3);
    expect(apiClient.post).toHaveBeenLastCalledWith('/bi/visuals/3/data');

    apiClient.delete.mockResolvedValueOnce({ data: { success: true } });
    await biService.deleteVisual(3);
    expect(apiClient.delete).toHaveBeenLastCalledWith('/bi/visuals/3');
  });

  it('filtra los visuales por panel cuando se pide', async () => {
    apiClient.get.mockResolvedValueOnce({ data: [] });
    await biService.listVisuals('Ventas');
    expect(apiClient.get).toHaveBeenLastCalledWith('/bi/visuals', {
      params: { panel_name: 'Ventas' },
    });
  });
});
