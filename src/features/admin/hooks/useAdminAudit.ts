import { useState, useEffect, useCallback, useRef } from 'react';
import { auditService } from '../services/audit_service';
import { AuditLog, AuditFilterParams } from '../../../types';
import { useNotifications } from '../../../context/NotificationContext';

// `input type="date"` da un dia, no un instante. Mandar el ISO de la medianoche
// UTC dejaba fuera todo el dia final (y corria el inicio de dia segun la zona).
// Se usan los bordes del dia LOCAL.
const dayStartIso = (value: string): string | undefined =>
  value ? new Date(`${value}T00:00:00`).toISOString() : undefined;
const dayEndIso = (value: string): string | undefined =>
  value ? new Date(`${value}T23:59:59.999`).toISOString() : undefined;

export function useAdminAudit() {
  const { notify } = useNotifications();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [page, setPage] = useState<number>(1);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [loading, setLoading] = useState<boolean>(false);
  const [exporting, setExporting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Filters state
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [filterUsername, setFilterUsername] = useState<string>('');
  const [filterDatabase, setFilterDatabase] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');

  // Selected Log for Inspector Modal
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  // La ultima consulta gana: tipear en un filtro dispara requests que pueden
  // volver desordenadas y pintar filas de un filtro anterior.
  const logsSeqRef = useRef(0);

  const fetchLogs = useCallback(async (targetPage: number = 1) => {
    const seq = ++logsSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const params: AuditFilterParams = {
        page: targetPage,
        page_size: 20,
        start_date: dayStartIso(startDate),
        end_date: dayEndIso(endDate),
        username: filterUsername.trim() || undefined,
        target_database: filterDatabase.trim() || undefined,
        validation_status: filterStatus || undefined,
      };

      const res = await auditService.getAuditLogs(params);
      if (seq !== logsSeqRef.current) return;
      setLogs(res.items);
      setTotal(res.total);
      setPage(res.page);
      setTotalPages(res.total_pages);
    } catch {
      if (seq !== logsSeqRef.current) return;
      // No se dejan las filas anteriores: serian de otro filtro.
      setLogs([]);
      setTotal(0);
      setError('No se pudieron cargar los registros de auditoría.');
    } finally {
      if (seq === logsSeqRef.current) setLoading(false);
    }
  }, [startDate, endDate, filterUsername, filterDatabase, filterStatus]);

  // `fetchLogs` cambia de identidad en cada tecla del filtro (depende de los
  // estados), asi que este efecto disparaba una request por pulsacion. El
  // debounce deja la ultima tecla ganar.
  useEffect(() => {
    const id = setTimeout(() => fetchLogs(1), 350);
    return () => clearTimeout(id);
  }, [fetchLogs]);

  const handleFilterSubmit = (e: React.FormEvent) => {
    // No fetch here: the debounced effect above already refetches 350ms after
    // the last keystroke, so calling fetchLogs(1) fired a second request per
    // click — and an admin who typed a username then clicked "Filtrar" could get
    // the pre-keystroke page on the compliance screen.
    e.preventDefault();
  };

  const handleClearFilters = () => {
    setStartDate('');
    setEndDate('');
    setFilterUsername('');
    setFilterDatabase('');
    setFilterStatus('');
  };

  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const params: AuditFilterParams = {
        start_date: dayStartIso(startDate),
        end_date: dayEndIso(endDate),
        username: filterUsername.trim() || undefined,
        target_database: filterDatabase.trim() || undefined,
        validation_status: filterStatus || undefined,
      };
      await auditService.exportAuditLogsCsv(params);
    } catch {
      notify('error', 'Error al exportar archivo CSV de auditoría.');
    } finally {
      setExporting(false);
    }
  };

  return {
    logs,
    total,
    page,
    totalPages,
    loading,
    exporting,
    error,
    startDate,
    endDate,
    filterUsername,
    filterDatabase,
    filterStatus,
    selectedLog,
    setSelectedLog,
    setStartDate,
    setEndDate,
    setFilterUsername,
    setFilterDatabase,
    setFilterStatus,
    fetchLogs,
    handleFilterSubmit,
    handleClearFilters,
    handleExportCsv,
  };
}
