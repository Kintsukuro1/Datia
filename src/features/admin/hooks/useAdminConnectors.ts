import { useState } from 'react';
import { CorporateConnection, ConnectionTestResult, connectorService } from '../services/connector_service';

// Resultado del test + momento en que el servidor lo respondio. El timestamp es
// solo de esta sesion: no se persiste ni se finge un "ultimo sondeo" que nadie corre.
export type ConnectionTestEntry = ConnectionTestResult & { testedAt: number };

export function useAdminConnectors(connectors: CorporateConnection[]) {
  const [filterDbType, setFilterDbType] = useState<string>('ALL');
  const [filterStatus, setFilterStatus] = useState<'ALL' | 'active' | 'inactive'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResultsMap, setTestResultsMap] = useState<Record<number, ConnectionTestEntry>>({});

  const handleTestCardConnection = async (conn: CorporateConnection) => {
    setTestingId(conn.id);
    const result = await connectorService.testConnection({
      name: conn.name,
      db_type: conn.db_type,
      host: conn.host,
      port: conn.port,
      database_name: conn.database_name,
      username: conn.username,
    });

    // Resultado real del servidor. No se maquilla un fallo como verificado.
    setTestingId(null);
    setTestResultsMap((prev) => ({ ...prev, [conn.id]: { ...result, testedAt: Date.now() } }));
  };

  const query = searchQuery.trim().toLowerCase();
  const filteredConnectors = connectors.filter((c) => {
    if (filterDbType !== 'ALL' && c.db_type !== filterDbType) return false;
    if (filterStatus === 'active' && !c.is_active) return false;
    if (filterStatus === 'inactive' && c.is_active) return false;
    if (query) {
      const haystack = `${c.name} ${c.host} ${c.database_name}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  // Conteos derivados de la lista real de conectores, no de metricas de muestra.
  const counters = {
    total: connectors.length,
    active: connectors.filter((c) => c.is_active).length,
    inactive: connectors.filter((c) => !c.is_active).length,
    permissionReview: connectors.filter((c) => c.requires_permission_review).length,
  };

  return {
    filterDbType,
    setFilterDbType,
    filterStatus,
    setFilterStatus,
    searchQuery,
    setSearchQuery,
    testingId,
    testResultsMap,
    handleTestCardConnection,
    counters,
    filteredConnectors,
  };
}
