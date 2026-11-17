import { apiClient } from '../../../shared/api/api_client';
import {
  BIConnection,
  BIQueryResult,
  BIReport,
  BISchema,
  BIVisual,
  BIVisualSpec,
} from '../types';

export interface BIReportParams {
  connection_id: number;
  table: string;
  date_column?: string;
  group_column?: string;
  category_column?: string;
  measure?: string;
  year?: number;
  month?: string;
  group_value?: string;
  category_value?: string;
}

export interface BIVisualCreateBody {
  title: string;
  panel_name: string;
  spec: BIVisualSpec;
}

export const biService = {
  listConnections: async (): Promise<BIConnection[]> =>
    (await apiClient.get<BIConnection[]>('/bi/connections')).data,

  getSchema: async (connectionId: number): Promise<BISchema> =>
    (
      await apiClient.get<BISchema>('/bi/schema', {
        params: { connection_id: connectionId },
        timeoutMs: 30_000,
      })
    ).data,

  report: async (params: BIReportParams): Promise<BIReport> =>
    (
      await apiClient.get<BIReport>('/bi/report', {
        params: params as unknown as Record<string, unknown>,
        // El informe ejecuta varias consultas; el tope normal de 30 s es corto.
        timeoutMs: 60_000,
      })
    ).data,

  preview: async (spec: BIVisualSpec): Promise<BIQueryResult> =>
    (await apiClient.post<BIQueryResult>('/bi/query', spec)).data,

  listVisuals: async (panelName?: string): Promise<BIVisual[]> =>
    (
      await apiClient.get<BIVisual[]>('/bi/visuals', {
        params: panelName ? { panel_name: panelName } : undefined,
      })
    ).data,

  saveVisual: async (body: BIVisualCreateBody): Promise<BIVisual> =>
    (await apiClient.post<BIVisual>('/bi/visuals', body)).data,

  updateVisual: async (visualId: number, body: BIVisualCreateBody): Promise<BIVisual> =>
    (await apiClient.put<BIVisual>(`/bi/visuals/${visualId}`, body)).data,

  reorderVisuals: async (panelName: string, ids: number[]): Promise<BIVisual[]> =>
    (
      await apiClient.put<BIVisual[]>('/bi/visuals/positions', {
        panel_name: panelName,
        ids,
      })
    ).data,

  visualData: async (visualId: number): Promise<BIQueryResult> =>
    (await apiClient.post<BIQueryResult>(`/bi/visuals/${visualId}/data`)).data,

  deleteVisual: async (visualId: number): Promise<{ success: boolean }> =>
    (await apiClient.delete<{ success: boolean }>(`/bi/visuals/${visualId}`)).data,
};
