import { apiClient } from '../../../shared/api/api_client';
import { AuditLogsPage, AuditFilterParams } from '../../../types';
import { downloadBlob } from '../../../shared/download';

export const auditService = {
  async getAuditLogs(params?: AuditFilterParams): Promise<AuditLogsPage> {
    const res = await apiClient.get<AuditLogsPage>('/audit', { params });
    return res.data;
  },

  async exportAuditLogsCsv(params?: AuditFilterParams): Promise<void> {
    const res = await apiClient.get<Blob>('/audit/export', {
      params,
      responseType: 'blob',
    });

    const blob = new Blob([res.data], { type: 'text/csv;charset=utf-8;' });
    const nowStr = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    downloadBlob(blob, `datia_audit_logs_${nowStr}.csv`);
  },
};
