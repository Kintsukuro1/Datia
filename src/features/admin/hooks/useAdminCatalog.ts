import { useState, useEffect, useCallback, useRef } from 'react';
import {
  CatalogItem,
  CatalogRuleDraft,
  DataDictionaryResponse,
  catalogService,
} from '../services/catalog_service';
import {
  CorporateConnection,
  connectorService,
} from '../services/connector_service';
import { getApiErrorMessage } from '../../../shared/api/api_client';

export function useAdminCatalog() {
  const [subTab, setSubTab] = useState<'catalog' | 'dictionary'>('catalog');

  // Corporate Connections State
  const [connectors, setConnectors] = useState<CorporateConnection[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState<number | null>(null);
  const [isLoadingConnectors, setIsLoadingConnectors] = useState(false);
  const [connectorsError, setConnectorsError] = useState<string | null>(null);

  // Semantic Catalog State
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(false);
  const [editingItem, setEditingItem] = useState<CatalogItem | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  // AI Auto-enrichment State
  const [isEnriching, setIsEnriching] = useState(false);
  const [enrichSuccessMsg, setEnrichSuccessMsg] = useState<string | null>(null);
  const [enrichErrorMsg, setEnrichErrorMsg] = useState<string | null>(null);
  // Catalog CRUD errors (edit / create)
  const [catalogErrorMsg, setCatalogErrorMsg] = useState<string | null>(null);
  // Errores de carga/refresco de la VISTA (los de guardado van a
  // catalogErrorMsg, que se muestra dentro del modal "Añadir Regla"). Son dos
  // estados separados y no uno: las dos cargas corren en paralelo y el exito de
  // una limpiaria el fallo de la otra, dejando la seccion del catalogo
  // afirmando "no se encontraron entradas" con la API caida.
  const [catalogViewErrorMsg, setCatalogViewErrorMsg] = useState<string | null>(null);
  const [dictionaryViewErrorMsg, setDictionaryViewErrorMsg] = useState<string | null>(null);

  // Technical Data Dictionary State
  const [dataDictionary, setDataDictionary] = useState<DataDictionaryResponse | null>(null);
  const [isLoadingDictionary, setIsLoadingDictionary] = useState(false);
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>({});

  const selectedConnRef = useRef<number | null>(null);
  selectedConnRef.current = selectedConnectionId;

  // Secuencia por consulta: al cambiar de fuente, la respuesta de la anterior
  // puede llegar despues y pintar su catalogo como si fuera el de la actual.
  const catalogSeqRef = useRef(0);
  const dictionarySeqRef = useRef(0);
  const enrichSeqRef = useRef(0);

  // 1. Fetch available connections
  const fetchConnectors = useCallback(async () => {
    setIsLoadingConnectors(true);
    setConnectorsError(null);
    try {
      const data = await connectorService.getConnectors();
      setConnectors(data || []);
      if (data && data.length > 0) {
        setSelectedConnectionId((prev) => {
          if (prev && data.some((c) => c.id === prev)) {
            return prev;
          }
          const active = data.find((c) => c.is_active);
          return active ? active.id : data[0].id;
        });
      }
    } catch (err: any) {
      // Sin fuentes la pantalla no puede decir "no hay reglas": no se pudo
      // preguntar. Se conserva la lista anterior (si la había) y se avisa.
      setConnectorsError(
        getApiErrorMessage(err, 'No se pudo cargar la lista de fuentes de datos.', true)
      );
    } finally {
      setIsLoadingConnectors(false);
    }
  }, []);

  // 2. Fetch Catalog for active/selected connection
  const fetchCatalogData = useCallback(async (connId?: number | null) => {
    const targetId = connId !== undefined ? connId : selectedConnRef.current;
    const seq = ++catalogSeqRef.current;
    setIsLoadingCatalog(true);
    // Lo de la fuente anterior no puede quedar en pantalla mientras carga la
    // nueva: seria dato viejo presentado como actual.
    setItems([]);
    try {
      const data = await catalogService.getCatalog(targetId || undefined);
      if (seq !== catalogSeqRef.current) return;
      setItems(data);
      setCatalogViewErrorMsg(null);
    } catch (err: any) {
      if (seq !== catalogSeqRef.current) return;
      // Se limpian los items: los de la conexion anterior en pantalla serian
      // datos viejos afirmando ser de la conexion actual.
      setCatalogViewErrorMsg(getApiErrorMessage(err, 'No se pudo cargar el catálogo semántico.', true));
      setItems([]);
    } finally {
      if (seq === catalogSeqRef.current) setIsLoadingCatalog(false);
    }
  }, []);

  // 3. Fetch Dictionary for active/selected connection
  const fetchDictionaryData = useCallback(async (connId?: number | null) => {
    const targetId = connId !== undefined ? connId : selectedConnRef.current;
    const seq = ++dictionarySeqRef.current;
    setIsLoadingDictionary(true);
    setDataDictionary(null);
    setExpandedTables({});
    try {
      const data = await catalogService.getDataDictionary(targetId || undefined);
      if (seq !== dictionarySeqRef.current) return;
      setDataDictionary(data);
      setDictionaryViewErrorMsg(null);
      // Auto-expand first 3 tables
      const initialExpanded: Record<string, boolean> = {};
      data.tables.forEach((t, idx) => {
        initialExpanded[t.table_name] = idx < 3;
      });
      setExpandedTables(initialExpanded);
    } catch (err: any) {
      if (seq !== dictionarySeqRef.current) return;
      setDictionaryViewErrorMsg(getApiErrorMessage(err, 'No se pudo cargar el diccionario de datos.', true));
      setDataDictionary(null);
      setExpandedTables({});
    } finally {
      if (seq === dictionarySeqRef.current) setIsLoadingDictionary(false);
    }
  }, []);

  // Initial load: Fetch connectors first
  useEffect(() => {
    fetchConnectors();
  }, [fetchConnectors]);

  // When selectedConnectionId changes, re-fetch catalog and dictionary
  useEffect(() => {
    if (selectedConnectionId !== null) {
      fetchCatalogData(selectedConnectionId);
      fetchDictionaryData(selectedConnectionId);
    }
  }, [selectedConnectionId, fetchCatalogData, fetchDictionaryData]);

  const handleSelectConnection = (connId: number) => {
    // No se espera al efecto para vaciar: la fuente ya cambió en la UI.
    setItems([]);
    setDataDictionary(null);
    setExpandedTables({});
    setSelectedConnectionId(connId);
  };

  const handleRunAiAutoEnrich = async () => {
    const targetId = selectedConnectionId;
    const seq = ++enrichSeqRef.current;
    setIsEnriching(true);
    setEnrichSuccessMsg(null);
    setEnrichErrorMsg(null);
    try {
      // `useLlm: true` porque el boton promete IA: con el modelo caido el
      // backend cae a heuristicas y lo declara en el mensaje.
      const res = await catalogService.autoEnrich(
        targetId || undefined,
        undefined,
        { useLlm: true }
      );
      if (seq !== enrichSeqRef.current) return;
      // `success: false` es un fallo del enriquecimiento aunque el POST haya
      // respondido 200: pintarlo en verde afirmaba que enriquecio. Y sin
      // refresco: nada cambio en el servidor.
      if (!res.success) {
        setEnrichErrorMsg(res.message || 'La IA no pudo enriquecer el catálogo.');
        setTimeout(() => setEnrichErrorMsg(null), 6000);
        return;
      }
      setEnrichSuccessMsg(res.message || 'Catálogo enriquecido con IA.');
      // Solo se refresca la fuente enriquecida; si el usuario cambio de fuente
      // mientras corria, no se pisa la vista de la nueva.
      if (selectedConnRef.current === targetId) {
        await fetchCatalogData(targetId);
        await fetchDictionaryData(targetId);
      }
      setTimeout(() => setEnrichSuccessMsg(null), 5000);
    } catch (err: any) {
      if (seq !== enrichSeqRef.current) return;
      const msg = getApiErrorMessage(err, 'Error al auto-enriquecer el catálogo.', true);
      setEnrichErrorMsg(msg);
      setTimeout(() => setEnrichErrorMsg(null), 6000);
    } finally {
      if (seq === enrichSeqRef.current) setIsEnriching(false);
    }
  };

  const handleSaveEdit = async (desc: string, formula: string) => {
    if (!editingItem) return;
    setCatalogErrorMsg(null);
    try {
      await catalogService.updateCatalogItem(editingItem.id, {
        description: desc,
        business_formula: formula,
      });
      await fetchCatalogData(selectedConnectionId);
      await fetchDictionaryData(selectedConnectionId);
      setEditingItem(null);
    } catch (err: any) {
      // No se parchea el estado local: el catalogo en la BD no cambio.
      setCatalogErrorMsg(getApiErrorMessage(err, 'No se pudo guardar la edición del catálogo.', true));
    }
  };

  const handleAddItem = async (newItem: CatalogRuleDraft): Promise<boolean> => {
    const connectionId = newItem.connection_id ?? selectedConnectionId;
    if (!connectionId) {
      setCatalogErrorMsg('Seleccioná una fuente de datos antes de crear la regla.');
      return false;
    }
    setCatalogErrorMsg(null);
    try {
      await catalogService.createCatalogItem({
        connection_id: connectionId,
        table_name: newItem.table,
        column_name: newItem.column,
        description: newItem.desc,
        business_formula: newItem.formula,
        is_ai_generated: newItem.is_ai,
      });
      await fetchCatalogData(selectedConnectionId);
      await fetchDictionaryData(selectedConnectionId);
      setIsAddModalOpen(false);
      return true;
    } catch (err: any) {
      // Sin exito visual: la regla no se creo.
      setCatalogErrorMsg(getApiErrorMessage(err, 'No se pudo crear la regla del catálogo.', true));
      return false;
    }
  };

  const handleDeleteItem = async (id: number, table: string, column?: string) => {
    const label = column ? `${table}.${column}` : table;
    if (!window.confirm(`¿Estás seguro de eliminar la regla del catálogo para '${label}'?`)) return;
    try {
      await catalogService.deleteCatalogItem(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
      // Este refresco ya no lanza: captura su propio fallo en dictionaryViewErrorMsg.
      await fetchDictionaryData(selectedConnectionId);
    } catch (err: any) {
      // No va a catalogErrorMsg: ese se muestra dentro del modal "Añadir Regla"
      // y un borrado no tiene nada que ver con ese formulario.
      setCatalogViewErrorMsg(getApiErrorMessage(err, 'No se pudo eliminar la regla del catálogo.', true));
    }
  };

  const toggleTableExpansion = (tableName: string) => {
    setExpandedTables((prev) => ({
      ...prev,
      [tableName]: !prev[tableName],
    }));
  };

  const selectedConnector = connectors.find((c) => c.id === selectedConnectionId) || null;

  return {
    subTab,
    setSubTab,
    connectors,
    selectedConnectionId,
    selectedConnector,
    isLoadingConnectors,
    connectorsError,
    handleSelectConnection,
    items,
    isLoadingCatalog,
    editingItem,
    setEditingItem,
    isAddModalOpen,
    setIsAddModalOpen,
    isEnriching,
    enrichSuccessMsg,
    enrichErrorMsg,
    catalogErrorMsg,
    catalogViewErrorMsg,
    dictionaryViewErrorMsg,
    dataDictionary,
    isLoadingDictionary,
    expandedTables,
    handleRunAiAutoEnrich,
    handleSaveEdit,
    handleAddItem,
    handleDeleteItem,
    toggleTableExpansion,
    refreshAll: () => {
      fetchConnectors();
      fetchCatalogData(selectedConnectionId);
      fetchDictionaryData(selectedConnectionId);
    },
  };
}
