import { describe, it, expect, beforeEach, vi } from 'vitest';

// Contrato de `catalog_service`: un fallo de API se propaga. El bug que estos
// tests protegen es concreto: `getCatalog` con `catch { return [] }` y
// `getDataDictionary` con un diccionario fabricado
// (`{connection_name: 'Base de Datos', tables: [], total_tables: 0}`) hacian que
// la vista afirmara "0 Tablas • 0 Columnas" sobre una base que existe, cuando en
// realidad no se pudo preguntar.

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

let catalogService: typeof import('../features/admin/services/catalog_service').catalogService;

beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  const mod = await import('../features/admin/services/catalog_service');
  catalogService = mod.catalogService;
});

describe('getCatalog: lista vacia no es lo mismo que API caida', () => {
  it('propaga un 500 en vez de resolver []', async () => {
    apiClient.get.mockRejectedValue(httpError(500, 'Fallo interno'));

    // Con `[]`, la pestañita afirmaba "Catálogo Semántico (0)" y el usuario no
    // tenia forma de saber que la API estaba caida.
    await expect(catalogService.getCatalog()).rejects.toThrow();
  });

  it('rechaza un 200 que no es una lista', async () => {
    // Sin el guard, este objeto llegaba a `setItems` y el render reventaba en
    // `items.map` / `items.length`.
    apiClient.get.mockResolvedValue({ data: { detalle: 'algo raro' } });

    await expect(catalogService.getCatalog()).rejects.toThrow(/catálogo/i);
  });

  it('devuelve el payload cuando el servidor responde 200', async () => {
    const items = [
      {
        id: 1,
        connection_id: 1,
        schema_name: 'main',
        table_name: 'ventas',
        description: 'Ventas por mes',
        is_ai_generated: false,
      },
    ];
    apiClient.get.mockResolvedValue({ data: items });

    await expect(catalogService.getCatalog(2)).resolves.toEqual(items);
    expect(apiClient.get).toHaveBeenCalledWith('/catalog', { params: { connection_id: 2 } });
  });
});

describe('getDataDictionary: ningun diccionario inventado', () => {
  it('propaga un fallo de red y no resuelve {tables: [], total_tables: 0}', async () => {
    apiClient.get.mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(catalogService.getDataDictionary()).rejects.toThrow();
  });

  it('propaga el error del servidor con su detalle', async () => {
    apiClient.get.mockRejectedValue(httpError(500, 'Fallo interno'));
    await expect(catalogService.getDataDictionary()).rejects.toThrow();
  });

  it('devuelve el diccionario cuando el servidor responde 200', async () => {
    const payload = {
      connection_id: 1,
      connection_name: 'Ventas',
      db_type: 'sqlite',
      tables: [],
      total_tables: 0,
      total_columns: 0,
    };
    apiClient.get.mockResolvedValue({ data: payload });

    await expect(catalogService.getDataDictionary(1)).resolves.toEqual(payload);
  });
});
