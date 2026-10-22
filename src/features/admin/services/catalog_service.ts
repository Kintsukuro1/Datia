import { apiClient } from '../../../shared/api/api_client';

export interface CatalogItem {
  id: number;
  connection_id: number;
  domain_id?: number;
  schema_name: string;
  table_name: string;
  column_name?: string;
  friendly_name?: string;
  description: string;
  synonyms?: string;
  business_formula?: string;
  is_ai_generated: boolean;
  updated_at?: string;
}

export interface DataDictionaryColumn {
  name: string;
  data_type: string;
  is_pk: boolean;
  is_nullable: boolean;
  default_value?: string | null;
  sample_values: string[];
  friendly_name?: string | null;
  description?: string | null;
  business_formula?: string | null;
  is_ai_generated: boolean;
}

export interface DataDictionaryTable {
  table_name: string;
  schema_name: string;
  row_count: number;
  column_count: number;
  description?: string | null;
  columns: DataDictionaryColumn[];
}

export interface DataDictionaryResponse {
  connection_id: number;
  connection_name: string;
  db_type: string;
  tables: DataDictionaryTable[];
  total_tables: number;
  total_columns: number;
}

export interface AutoEnrichResponse {
  success: boolean;
  message: string;
  enriched_count: number;
  /** Cuántos campos describió el modelo local (0 = quedó la heurística). */
  llm_enriched_count: number;
  /** La fase IA se cortó (presupuesto o fallos consecutivos): con
   *  `llm_enriched_count > 0` es una corrida parcial. */
  llm_stopped_early: boolean;
  catalog_items: CatalogItem[];
}

export interface EnrichEstimateResponse {
  available: boolean;
  message: string;
  total_tables: number;
  total_columns: number;
  tokens_per_second?: number | null;
  estimated_seconds?: number | null;
  min_seconds?: number | null;
  max_seconds?: number | null;
  beyond_budget: boolean;
  budget_seconds: number;
}

interface NullColumnInfo {
  column_name: string;
  data_type: string;
  null_count: number;
  null_percentage: number;
}

interface NullTableInfo {
  table_name: string;
  schema_name: string;
  total_rows: number;
  total_null_columns: number;
  columns_with_nulls: NullColumnInfo[];
}

export interface NullsAuditResponse {
  has_nulls: boolean;
  total_tables_with_nulls: number;
  tables: NullTableInfo[];
  current_policy: string;
}

export const catalogService = {
  async getCatalog(connectionId?: number, tableName?: string): Promise<CatalogItem[]> {
    // Sin catch: devolver [] ante un 500 o un error de red es indistinguible de
    // "no hay reglas en el catalogo" y la vista afirmaba un conteo que nadie
    // confirmo. El que llama decide que mostrar.
    const params: Record<string, any> = {};
    if (connectionId) params.connection_id = connectionId;
    if (tableName) params.table_name = tableName;
    const res = await apiClient.get<CatalogItem[]>('/catalog', { params });
    if (!Array.isArray(res.data)) {
      throw new Error('El servidor no devolvió la lista del catálogo.');
    }
    return res.data;
  },

  async createCatalogItem(item: {
    connection_id?: number;
    schema_name?: string;
    table_name: string;
    column_name?: string;
    friendly_name?: string;
    description: string;
    synonyms?: string;
    business_formula?: string;
    is_ai_generated?: boolean;
  }): Promise<CatalogItem> {
    const res = await apiClient.post<CatalogItem>('/catalog', {
      connection_id: item.connection_id || 1,
      schema_name: item.schema_name || 'main',
      table_name: item.table_name,
      column_name: item.column_name || null,
      friendly_name: item.friendly_name || null,
      description: item.description,
      synonyms: item.synonyms || null,
      business_formula: item.business_formula || 'Columna directa',
      is_ai_generated: item.is_ai_generated ?? false,
    });
    return res.data;
  },

  async updateCatalogItem(
    id: number,
    item: {
      friendly_name?: string;
      description?: string;
      synonyms?: string;
      business_formula?: string;
      is_ai_generated?: boolean;
    }
  ): Promise<CatalogItem> {
    const res = await apiClient.put<CatalogItem>(`/catalog/${id}`, item);
    return res.data;
  },

  async deleteCatalogItem(id: number): Promise<void> {
    await apiClient.delete(`/catalog/${id}`);
  },

  async getDataDictionary(connectionId?: number): Promise<DataDictionaryResponse> {
    // Sin catch: fabricar `{tables: [], total_tables: 0, connection_name: 'Base
    // de Datos'}` ante un fallo pintaba "0 Tablas • 0 Columnas" sobre una
    // conexion que existe. Ninguna respuesta inventada.
    const params: Record<string, any> = {};
    if (connectionId) params.connection_id = connectionId;
    const res = await apiClient.get<DataDictionaryResponse>('/catalog/data-dictionary', { params });
    return res.data;
  },

  async autoEnrich(
    connectionId?: number,
    tableName?: string,
    options?: { useLlm?: boolean; baseUrl?: string; modelName?: string }
  ): Promise<AutoEnrichResponse> {
    // La IA es opt-in: cuesta tiempo de modelo. `baseUrl`/`modelName` llevan la
    // configuración del usuario logueado (validada en el backend, loopback only).
    const res = await apiClient.post<AutoEnrichResponse>('/catalog/auto-enrich', {
      connection_id: connectionId || null,
      table_name: tableName || null,
      use_llm: options?.useLlm ?? false,
      base_url: options?.baseUrl || null,
      model_name: options?.modelName || null,
    });
    return res.data;
  },

  async getEnrichEstimate(
    connectionId: number,
    options?: { baseUrl?: string; modelName?: string }
  ): Promise<EnrichEstimateResponse> {
    const params: Record<string, any> = {};
    if (options?.baseUrl) params.base_url = options.baseUrl;
    if (options?.modelName) params.model_name = options.modelName;
    const res = await apiClient.get<EnrichEstimateResponse>(
      `/catalog/connections/${connectionId}/enrich-estimate`,
      { params }
    );
    return res.data;
  },

  async getNullsAudit(connectionId: number): Promise<NullsAuditResponse> {
    const res = await apiClient.get<NullsAuditResponse>(`/catalog/connections/${connectionId}/nulls-audit`);
    return res.data;
  },

  async applyNullPolicy(connectionId: number, policy: 'delete_rows' | 'mode' | 'nearest' | 'open'): Promise<{ status: string; policy: string; message: string }> {
    // El backend exige confirmación server-side para las políticas destructivas;
    // el clic en el paso "nulls_check" del wizard es esa confirmación.
    const res = await apiClient.post<{ status: string; policy: string; message: string }>(`/catalog/connections/${connectionId}/nulls-policy`, { policy, confirm: true });
    return res.data;
  },
};
