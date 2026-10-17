import React, { useState, useRef } from 'react';
import {
  Sparkles,
  Database,
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  ArrowRight,
  ArrowLeft,
  Check,
  Loader2,
  X,
  Server,
  Table as TableIcon,
  Key,
  ShieldCheck,
  UserCheck,
  Bot,
  Edit3,
  Trash2,
  BarChart3,
  MapPin,
  Clock,
  WifiOff,
  Box,
  Cylinder,
  Zap,
} from 'lucide-react';
import { UploadDropzone } from './upload/UploadDropzone';
import { ConnectorFormFields } from './ConnectorFormFields';
import { connectorService, ConnectionTestResult, CorporateConnection } from '../../features/admin/services/connector_service';
import {
  catalogService,
  AutoEnrichResponse,
  DataDictionaryResponse,
  DataDictionaryTable,
  DataDictionaryColumn,
  EnrichEstimateResponse,
  NullsAuditResponse,
} from '../../features/admin/services/catalog_service';
import { useAuth } from '../../features/auth/context/AuthContext';
import { apiClient } from '../../shared/api/api_client';
import { SystemHealthResponse } from '../../types';
import { useModalA11y } from '../../hooks/useModalA11y';

interface DatabaseWizardModalProps {
  isOpen: boolean;
  initialMode?: IngestionMode;
  onClose: () => void;
  onSuccess: () => void;
  onNavigateToCatalog?: () => void;
}

type WizardStep = 'input' | 'choice' | 'generating' | 'review' | 'nulls_check' | 'complete';
type IngestionMode = 'file' | 'remote';
type CreationMode = 'ai' | 'manual';
type LlmStatus = 'checking' | 'available' | 'unavailable';

// Estados del proceso (solo texto). El avance de la barra NO sale de acá: es
// el tiempo transcurrido contra la estimación medida del modelo (ver
// `generationPct`). Un porcentaje fijo mentía mientras el modelo trabajaba.
const GENERATION_PHASES = [
  'Introspeccionando esquema físico y metadatos relacionales...',
  'Muestreando valores representativos de columnas...',
  'Invocando modelo de IA para descripciones de negocio y fórmulas...',
  'Compilando catálogo semántico y reglas de gobernanza...',
];

// Pasos visibles del tracker. 'generating' y 'review' comparten el paso 3
// (Diccionario): generar y revisar es el mismo tramo del flujo.
const WIZARD_STEPS = [
  'Ingreso BD',
  'Evaluación & Modo',
  'Diccionario & IA',
  'Tratamiento Nulos',
  'Finalizado',
];

const UI_STEP_INDEX: Record<WizardStep, number> = {
  input: 0,
  choice: 1,
  generating: 2,
  review: 2,
  nulls_check: 3,
  complete: 4,
};

type RemoteDbType = 'postgresql' | 'mssql' | 'mysql' | 'oracle';

// Motores soportados por el backend, con el puerto por defecto real que aplica
// el formulario al seleccionarlos.
const ENGINES: {
  id: RemoteDbType;
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { id: 'postgresql', label: 'PostgreSQL', hint: 'Puerto 5432', icon: Database },
  { id: 'mssql', label: 'MS SQL Server', hint: 'Puerto 1433', icon: Server },
  { id: 'mysql', label: 'MySQL / MariaDB', hint: 'Puerto 3306', icon: Box },
  { id: 'oracle', label: 'Oracle Database', hint: 'Puerto 1521', icon: Cylinder },
];

const DEFAULT_PORTS: Record<RemoteDbType, number> = {
  postgresql: 5432,
  mssql: 1433,
  mysql: 3306,
  oracle: 1521,
};

// Segundos -> texto legible. Se redondea a pasos de 5s para no fingir una
// precisión que la estimación (una medición + una fórmula) no tiene.
const formatDuration = (seconds?: number | null): string => {
  if (!seconds || seconds <= 0) return '—';
  if (seconds < 90) return `${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `${Math.round(seconds / 60)} min`;
};

export const DatabaseWizardModal: React.FC<DatabaseWizardModalProps> = ({
  isOpen,
  initialMode = 'file',
  onClose,
  onSuccess,
  onNavigateToCatalog,
}) => {
  // Step state
  const [currentStep, setCurrentStep] = useState<WizardStep>('input');
  const [ingestionMode, setIngestionMode] = useState<IngestionMode>(initialMode);
  const [creationMode, setCreationMode] = useState<CreationMode>('ai');

  React.useEffect(() => {
    if (isOpen) {
      setIngestionMode(initialMode);
    }
  }, [isOpen, initialMode]);

  // File Upload State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [customName, setCustomName] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Remote DB State
  const [remoteForm, setRemoteForm] = useState({
    name: '',
    dbType: 'postgresql' as RemoteDbType,
    host: 'localhost',
    port: 5432,
    databaseName: '',
    username: 'postgres',
    password: '',
    isActive: true,
  });

  // Prueba de conectividad previa (POST /connectors/test). El resultado se
  // limpia al cambiar cualquier parámetro probado para no mostrar un OK viejo.
  const [isTestingConnection, setIsTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);

  // Common Submission / Connection State
  const [isProcessing, setIsProcessing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [registeredConnection, setRegisteredConnection] = useState<CorporateConnection | null>(null);

  // Generation progress state
  const [generationPhaseIndex, setGenerationPhaseIndex] = useState(0);
  // Reloj real de la fase de generación: la barra avanza con lo transcurrido
  // contra la estimación medida del modelo, no con una coreografía fija que se
  // queda clavada en 75% mientras el modelo trabaja.
  const [generationElapsed, setGenerationElapsed] = useState(0);
  const generationStartRef = useRef<number | null>(null);

  // Dictionary Review State
  const [dictionaryData, setDictionaryData] = useState<DataDictionaryResponse | null>(null);
  const [selectedTableIndex, setSelectedTableIndex] = useState(0);
  const [editedColumns, setEditedColumns] = useState<Record<string, { friendly_name: string; description: string; business_formula: string }>>({});
  const [isSavingReview, setIsSavingReview] = useState(false);

  // Null Remediation State
  const [nullsData, setNullsData] = useState<NullsAuditResponse | null>(null);
  const [selectedNullPolicy, setSelectedNullPolicy] = useState<'delete_rows' | 'mode' | 'nearest' | 'open'>('open');
  const [isApplyingNullPolicy, setIsApplyingNullPolicy] = useState(false);

  // LLM availability for step 2. Same source as the header badge
  // (/system/health): one source of truth, no second opinion.
  const { settings } = useAuth();
  const [llmStatus, setLlmStatus] = useState<LlmStatus>('checking');
  const [llmMessage, setLlmMessage] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<EnrichEstimateResponse | null>(null);
  const [isEstimating, setIsEstimating] = useState(false);
  // Descarta respuestas viejas del health check si el paso se re-entra o el
  // usuario cambia de conexión mientras una corrida anterior está en vuelo.
  const llmCheckIdRef = useRef(0);

  const llmConfig = React.useMemo(
    () => ({
      provider: settings?.llm_provider,
      baseUrl: settings?.ollama_url,
      modelName: settings?.ollama_model,
    }),
    [settings?.llm_provider, settings?.ollama_url, settings?.ollama_model]
  );

  const checkLlmAndEstimate = React.useCallback(async () => {
    if (!registeredConnection) return;
    const checkId = ++llmCheckIdRef.current;
    setLlmStatus('checking');
    setLlmMessage(null);
    setEstimate(null);

    let engineStatus: string | undefined;
    let engineMessage: string | null = null;
    try {
      const res = await apiClient.get<SystemHealthResponse>('/system/health', {
        params: {
          base_url: llmConfig.baseUrl,
          provider: llmConfig.provider,
          model_name: llmConfig.modelName,
        },
      });
      if (llmCheckIdRef.current !== checkId) return;
      const engine = res.data.llm_engine;
      engineStatus = engine?.status;
      engineMessage = engine?.message || null;
    } catch {
      if (llmCheckIdRef.current !== checkId) return;
      // Sin dato del backend no se sabe nada del LLM: se bloquea la opción y el
      // mensaje dice exactamente eso.
      setLlmStatus('unavailable');
      setLlmMessage('No se pudo verificar la disponibilidad del LLM local.');
      return;
    }

    const available = engineStatus === 'OPERATIVO';
    setLlmStatus(available ? 'available' : 'unavailable');
    setLlmMessage(engineMessage);
    if (!available) return;

    // La estimación corre después de la verificación: su probe de velocidad es
    // una inferencia corta real, no tiene sentido pagarla sin LLM.
    setIsEstimating(true);
    try {
      const est = await catalogService.getEnrichEstimate(registeredConnection.id, {
        baseUrl: llmConfig.baseUrl,
        modelName: llmConfig.modelName,
      });
      if (llmCheckIdRef.current !== checkId) return;
      setEstimate(est.available ? est : null);
    } catch {
      if (llmCheckIdRef.current === checkId) setEstimate(null);
    } finally {
      if (llmCheckIdRef.current === checkId) setIsEstimating(false);
    }
  }, [registeredConnection?.id, llmConfig.baseUrl, llmConfig.provider, llmConfig.modelName]);

  // Al entrar al paso de decisión se verifica el LLM una vez (y también al
  // reabrir el wizard): el estado se decide acá, no en un valor cacheado.
  React.useEffect(() => {
    if (isOpen && currentStep === 'choice' && registeredConnection) {
      checkLlmAndEstimate();
    }
  }, [isOpen, currentStep, registeredConnection?.id, checkLlmAndEstimate]);

  // Sin LLM verificado el modo IA no es una opción: tarjeta deshabilitada y
  // selección a Manual. El usuario puede reconectar el modelo y "Verificar de
  // nuevo" sin cerrar el wizard.
  React.useEffect(() => {
    if (llmStatus === 'unavailable') {
      setCreationMode((mode) => (mode === 'ai' ? 'manual' : mode));
    }
  }, [llmStatus]);

  // Ticker de 1s mientras se genera: alimenta el contador de tiempo
  // transcurrido y el avance real de la barra. Se apaga al salir del paso.
  React.useEffect(() => {
    if (currentStep !== 'generating') return;
    const id = setInterval(() => {
      const started = generationStartRef.current;
      if (started) setGenerationElapsed(Math.max(0, Math.round((Date.now() - started) / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [currentStep]);

  // Avance real: fracción transcurrida de la estimación medida. Sin estimación
  // (probe caído) se usa una referencia nominal de 60s. El tope de 96% no se
  // alcanza hasta que el backend responde: la barra nunca miente "100%".
  const generationExpectedSeconds =
    estimate?.estimated_seconds && estimate.estimated_seconds > 0 ? estimate.estimated_seconds : 60;
  const generationPct = Math.min(96, Math.max(3, Math.round((generationElapsed / generationExpectedSeconds) * 100)));

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, onClose);

  if (!isOpen) return null;

  const resetState = () => {
    setCurrentStep('input');
    setIngestionMode(initialMode);
    setCreationMode('ai');
    setSelectedFile(null);
    setCustomName('');
    setErrorMessage(null);
    setIsProcessing(false);
    setRegisteredConnection(null);
    setGenerationPhaseIndex(0);
    setGenerationElapsed(0);
    generationStartRef.current = null;
    setDictionaryData(null);
    setSelectedTableIndex(0);
    setEditedColumns({});
    setIsSavingReview(false);
    setNullsData(null);
    setSelectedNullPolicy('open');
    setIsApplyingNullPolicy(false);
    setLlmStatus('checking');
    setLlmMessage(null);
    setEstimate(null);
    setIsEstimating(false);
    setIsTestingConnection(false);
    setTestResult(null);
  };

  const handleClose = () => {
    resetState();
    onClose();
  };

  // --- Step 1: Submit DB (File or Remote) ---
  const handleFileChange = (file: File) => {
    setErrorMessage(null);
    setSelectedFile(file);
    if (!customName) {
      const baseName = file.name.substring(0, file.name.lastIndexOf('.')) || file.name;
      setCustomName(baseName.replace(/[_-]/g, ' ').toUpperCase());
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileChange(e.dataTransfer.files[0]);
    }
  };

  const handleIngestDatabase = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setIsProcessing(true);

    try {
      let createdConn: CorporateConnection;

      if (ingestionMode === 'file') {
        if (!selectedFile) {
          setErrorMessage('Selecciona un archivo válido (SQLite, Excel o CSV).');
          setIsProcessing(false);
          return;
        }
        createdConn = await connectorService.uploadDatabase(selectedFile, customName);
      } else {
        if (!remoteForm.name.trim() || !remoteForm.databaseName.trim()) {
          setErrorMessage('Completa el nombre identificador y nombre de la base de datos.');
          setIsProcessing(false);
          return;
        }
        createdConn = await connectorService.createConnector({
          name: remoteForm.name.trim(),
          db_type: remoteForm.dbType,
          host: remoteForm.host.trim(),
          port: remoteForm.port,
          database_name: remoteForm.databaseName.trim(),
          username: remoteForm.username.trim(),
          password: remoteForm.password,
          is_active: remoteForm.isActive,
          is_uploaded: false,
        });
      }

      setRegisteredConnection(createdConn);
      setIsProcessing(false);
      setCurrentStep('choice');
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Error al conectar/ingresar la fuente de datos.';
      setErrorMessage(msg);
      setIsProcessing(false);
    }
  };

  const handleRemoteDbTypeChange = (dbType: RemoteDbType) => {
    setRemoteForm((prev) => ({ ...prev, dbType, port: DEFAULT_PORTS[dbType] ?? prev.port }));
    setTestResult(null);
  };

  // Prueba de conectividad real sin crear el conector: socket TCP al puerto.
  // No valida usuario ni contraseña, y el mensaje del backend dice qué se
  // verificó.
  const handleTestRemoteConnection = async () => {
    setIsTestingConnection(true);
    setTestResult(null);
    const result = await connectorService.testConnection({
      name: remoteForm.name.trim() || 'prueba_conexion',
      db_type: remoteForm.dbType,
      host: remoteForm.host.trim(),
      port: remoteForm.port,
      database_name: remoteForm.databaseName.trim(),
      username: remoteForm.username.trim(),
      password: remoteForm.password,
      is_active: remoteForm.isActive,
      is_uploaded: false,
    });
    setTestResult(result);
    setIsTestingConnection(false);
  };

  // --- Step 2 to Step 3/4: Proceed with AI or Manual ---
  const handleStartDictionaryCreation = async () => {
    if (!registeredConnection) return;
    setErrorMessage(null);

    if (creationMode === 'ai') {
      // Guard duro: sin LLM verificado no se entra a "generando". La tarjeta ya
      // está deshabilitada, pero el estado puede haber quedado desincronizado
      // (p. ej. el LLM se cayó después de la verificación o el usuario hizo clic
      // antes de que terminara): acá se corta, no en el backend.
      if (llmStatus !== 'available') {
        setErrorMessage(
          llmStatus === 'checking'
            ? 'Verificando la disponibilidad del LLM; espera un instante e intenta de nuevo.'
            : 'No hay una LLM conectada para generar el diccionario. Conecta el modelo en Opciones o usa la Creación Manual.'
        );
        return;
      }

      setCurrentStep('generating');
      setGenerationPhaseIndex(0);
      generationStartRef.current = Date.now();
      setGenerationElapsed(0);

      // Fases 1, 2 y 4 son ritmo visual; la espera real es la fase 3 (la
      // llamada al modelo). El error de esa llamada NO se traga: un rechazo se
      // avisa en la revisión en vez de pintar un diccionario heurístico como
      // "generado por IA".
      try {
        const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

        // Phase 1: Introspect
        setGenerationPhaseIndex(0);
        await sleep(1100);

        // Phase 2: Samples
        setGenerationPhaseIndex(1);
        await sleep(1200);

        // Phase 3: enriquecimiento real con el modelo local (acotado en el
        // backend: una llamada por lote de columnas, techo por llamada y
        // presupuesto total — ver `CatalogEnricher`).
        setGenerationPhaseIndex(2);
        let enrich: AutoEnrichResponse | null = null;
        try {
          enrich = await catalogService.autoEnrich(registeredConnection.id, undefined, {
            useLlm: true,
            baseUrl: llmConfig.baseUrl,
            modelName: llmConfig.modelName,
          });
        } catch {
          enrich = null;
        }

        // Phase 4: Compile
        setGenerationPhaseIndex(3);
        await sleep(900);

        // Load resulting data dictionary
        const dict = await catalogService.getDataDictionary(registeredConnection.id);
        setDictionaryData(dict);
        initializeEditedColumns(dict);

        if (!enrich || !enrich.success) {
          // El mensaje del backend distingue el motivo real (LLM apagado, base
          // inaccesible, corte por tiempo). El genérico de acá los igualaba y
          // dejaba al usuario sin diagnóstico.
          setErrorMessage(
            enrich?.message ||
              'El enriquecimiento automático no se completó; se cargó el diccionario introspeccionado para revisión.'
          );
        } else if ((enrich.llm_enriched_count ?? 0) === 0) {
          setErrorMessage(
            enrich.message ||
              'El modelo de IA no devolvió descripciones usables; el diccionario quedó con reglas automáticas para revisión.'
          );
        } else if (enrich.llm_stopped_early) {
          // Corrida parcial: lo que llegó quedó aplicado y el resto es
          // heurístico. Se avisa para que la revisión sepa qué mirar.
          setErrorMessage(enrich.message);
        }
        setCurrentStep('review');
      } catch (err: any) {
        // Acá solo puede caer `getDataDictionary`: `autoEnrich` ya se manejó
        // arriba. Antes esta rama reintentaba la carga; ahora el único fallo
        // posible es la introspección, así que se reporta de una.
        setErrorMessage('No se pudo introspeccionar la base de datos. Revisá la conexión y volvé a intentar.');
        setCurrentStep('choice');
      }
    } else {
      // Manual creation mode
      setIsProcessing(true);
      try {
        const dict = await catalogService.getDataDictionary(registeredConnection.id);
        setDictionaryData(dict);
        initializeEditedColumns(dict);
        setIsProcessing(false);
        setCurrentStep('review');
      } catch (err: any) {
        setIsProcessing(false);
        setErrorMessage('No se pudo introspeccionar la base de datos para edición manual.');
      }
    }
  };

  const initializeEditedColumns = (dict: DataDictionaryResponse) => {
    const edits: Record<string, { friendly_name: string; description: string; business_formula: string }> = {};
    for (const table of dict.tables || []) {
      for (const col of table.columns || []) {
        const key = `${table.table_name}.${col.name}`;
        edits[key] = {
          friendly_name: col.friendly_name || col.name.replace(/_/g, ' ').toUpperCase(),
          description: col.description || `Campo '${col.name}' de la tabla ${table.table_name}`,
          business_formula: col.business_formula || 'Columna directa',
        };
      }
    }
    setEditedColumns(edits);
  };

  const handleColumnEditChange = (
    tableName: string,
    colName: string,
    field: 'friendly_name' | 'description' | 'business_formula',
    val: string
  ) => {
    const key = `${tableName}.${colName}`;
    setEditedColumns((prev) => ({
      ...prev,
      [key]: {
        ...prev[key],
        [field]: val,
      },
    }));
  };

  // --- Step 4: Save reviewed dictionary ---
  const handleSaveDictionaryReview = async () => {
    if (!registeredConnection || !dictionaryData) return;
    setIsSavingReview(true);
    setErrorMessage(null);

    try {
      // Save all reviewed items. Sequential, and failures are NO LONGER
      // swallowed: the previous `.catch(() => null)` let the wizard land on
      // "Diccionario Instalado Exitosamente" with a silently partial catalog,
      // so the AI-reviewed descriptions the admin just read did not exist and
      // the text-to-SQL engine had nothing to read.
      // ponytail: one request per column, serialized. 300 round-trips behind a
      // single button; batch endpoint only if this gets slow in practice.
      const tables = dictionaryData.tables || [];
      const pending = tables.flatMap((table) =>
        (table.columns || [])
          .filter((col) => editedColumns[`${table.table_name}.${col.name}`])
          .map((col) => ({ table, col }))
      );

      const failed: string[] = [];
      for (const { table, col } of pending) {
        const current = editedColumns[`${table.table_name}.${col.name}`];
        try {
          await catalogService.createCatalogItem({
            connection_id: registeredConnection.id,
            schema_name: table.schema_name || 'main',
            table_name: table.table_name,
            column_name: col.name,
            friendly_name: current.friendly_name,
            description: current.description,
            business_formula: current.business_formula,
            is_ai_generated: creationMode === 'ai',
          });
        } catch {
          failed.push(`${table.table_name}.${col.name}`);
        }
      }

      if (failed.length > 0) {
        setIsSavingReview(false);
        setErrorMessage(
          `No se pudieron guardar ${failed.length} de ${pending.length} columnas (por ejemplo ${failed
            .slice(0, 3)
            .join(', ')}). El diccionario quedó incompleto; revisa la conexión y reintenta.`
        );
        return;
      }

      setIsSavingReview(false);

      // Audit for NULL values across the connection
      try {
        const audit = await catalogService.getNullsAudit(registeredConnection.id);
        if (audit && audit.has_nulls && audit.tables && audit.tables.length > 0) {
          setNullsData(audit);
          setCurrentStep('nulls_check');
          return;
        }
      } catch {
        // If audit encounters an issue, proceed to complete directly
      }

      setCurrentStep('complete');
      onSuccess();
    } catch (err: any) {
      setErrorMessage('Error al persistir algunas modificaciones del diccionario.');
      setIsSavingReview(false);
    }
  };

  // --- Step 4.5: Apply chosen NULL remediation policy ---
  const handleApplyNullPolicy = async () => {
    if (!registeredConnection) return;
    setIsApplyingNullPolicy(true);
    setErrorMessage(null);

    try {
      await catalogService.applyNullPolicy(registeredConnection.id, selectedNullPolicy);
      setIsApplyingNullPolicy(false);
      setCurrentStep('complete');
      onSuccess();
    } catch (err: any) {
      setIsApplyingNullPolicy(false);
      setErrorMessage(err.response?.data?.detail || err.message || 'Error al aplicar el tratamiento de valores nulos.');
    }
  };

  const activeTable: DataDictionaryTable | undefined = dictionaryData?.tables?.[selectedTableIndex];

  // Sin LLM verificado (chequeando o caído) el modo IA no es cliqueable.
  const aiDisabled = llmStatus !== 'available';
  const uiStepIndex = UI_STEP_INDEX[currentStep];

  return (
    <div ref={modalRef} role="dialog" aria-modal="true" aria-label="Asistente de instalación de base de datos" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-md animate-fadeIn">
      <div className="glass-panel w-full max-w-4xl rounded-2xl sm:rounded-3xl border border-app-border shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
        {/* Top Header */}
        <div className="shrink-0 px-6 py-4 border-b border-app-border flex items-center justify-between bg-app-surface/95 backdrop-blur">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-xl bg-brand-600 text-white shadow-lg shadow-brand-600/25 shrink-0">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-app-text tracking-tight leading-tight">
                  Asistente de Instalación y Onboarding de Base de Datos
                </h3>
                <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                  Wizard
                </span>
              </div>
              <p className="text-xs text-app-muted">
                Paso a paso con evaluación, gobernanza e inteligencia artificial
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Cerrar asistente"
            className="text-app-muted hover:text-app-text p-1.5 rounded-lg hover:bg-app-hover transition-colors shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Step Progression Tracker */}
        <div className="shrink-0 px-6 py-3 bg-app-bg/70 border-b border-app-border flex items-center overflow-x-auto">
          {WIZARD_STEPS.map((label, idx) => {
            const isActive = idx === uiStepIndex;
            const isDone = idx < uiStepIndex;
            const stepLabel = idx === 2 && creationMode === 'manual' ? 'Diccionario Manual' : label;
            return (
              <React.Fragment key={label}>
                {idx > 0 && (
                  <div className={`h-px flex-1 min-w-3 mx-2.5 ${isDone || isActive ? 'bg-brand-500/50' : 'bg-app-border'}`} />
                )}
                <div className={`flex items-center gap-2 min-w-0 shrink-0 ${!isDone && !isActive ? 'opacity-50' : ''}`}>
                  <span
                    aria-hidden="true"
                    className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 border ${
                      isDone
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/40'
                        : isActive
                          ? 'bg-brand-600 text-white border-brand-500 shadow-sm shadow-brand-600/30'
                          : 'bg-app-card text-app-muted border-app-border'
                    }`}
                  >
                    {isDone ? <Check className="w-3.5 h-3.5" /> : idx + 1}
                  </span>
                  <div className="flex flex-col min-w-0">
                    <span className={`text-[9px] uppercase tracking-wider leading-none ${
                      isActive ? 'text-brand-600 dark:text-brand-400 font-bold' : 'text-app-muted'
                    }`}>
                      {isActive ? `Paso ${idx + 1} · Activo` : `Paso ${idx + 1}`}
                    </span>
                    <span className={`text-[11px] leading-tight truncate ${
                      isActive ? 'text-app-text font-bold' : isDone ? 'text-app-text font-medium' : 'text-app-muted'
                    }`}>
                      {stepLabel}
                    </span>
                  </div>
                </div>
              </React.Fragment>
            );
          })}
        </div>

        {/* Error notification banner */}
        {errorMessage && (
          <div className="shrink-0 mx-6 mt-4 p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300 text-xs flex items-center space-x-2 animate-fadeIn">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Scrollable Content Body */}
        <div className="flex-1 overflow-y-auto min-h-0 p-5 sm:p-6">
          {/* ================= STEP 1: INPUT ================= */}
          {currentStep === 'input' && (
            <form id="wizard-step-1" onSubmit={handleIngestDatabase} className="space-y-5">
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 pb-3 border-b border-app-border">
                <div className="flex flex-col gap-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
                      Paso 1 de 5
                    </span>
                    <span className="text-[11px] text-app-muted">Configuración de la fuente</span>
                  </div>
                  <h4 className="text-sm font-bold text-app-text">Selecciona el método de ingreso de la Base de Datos</h4>
                  <p className="text-xs text-app-muted leading-relaxed max-w-lg">
                    Puedes importar un archivo o conectar un servidor corporativo en red.
                  </p>
                </div>
                {/* Tabs */}
                <div className="flex items-center bg-app-bg rounded-xl p-1 border border-app-border shrink-0 self-start">
                  <button
                    type="button"
                    onClick={() => setIngestionMode('file')}
                    className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors flex items-center space-x-1.5 ${
                      ingestionMode === 'file' ? 'bg-brand-600 text-white' : 'text-app-muted hover:text-app-text'
                    }`}
                  >
                    <UploadCloud className="w-3.5 h-3.5" />
                    <span>Archivo</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setIngestionMode('remote')}
                    className={`px-3 py-1 text-xs font-semibold rounded-lg transition-colors flex items-center space-x-1.5 ${
                      ingestionMode === 'remote' ? 'bg-brand-600 text-white' : 'text-app-muted hover:text-app-text'
                    }`}
                  >
                    <Server className="w-3.5 h-3.5" />
                    <span>Conexión Remota</span>
                  </button>
                </div>
              </div>

              {ingestionMode === 'file' ? (
                <div className="space-y-4">
                  <UploadDropzone
                    selectedFile={selectedFile}
                    fileInputRef={fileInputRef}
                    isDragging={isDragging}
                    setIsDragging={setIsDragging}
                    handleFileChange={handleFileChange}
                    handleDrop={handleDrop}
                    onRemoveFile={() => setSelectedFile(null)}
                  />
                  <div className="space-y-1">
                    <label htmlFor="wizard-custom-name" className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider">
                      Nombre identificador de la fuente <span className="text-brand-500">*</span>
                    </label>
                    <input
                      type="text"
                      id="wizard-custom-name"
                      value={customName}
                      onChange={(e) => setCustomName(e.target.value)}
                      placeholder="Ej: FINANZAS_2026, INVENTARIO_GENERAL..."
                      required
                      className="w-full bg-app-bg border border-app-border rounded-xl px-3.5 py-2.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500"
                    />
                  </div>
                </div>
              ) : (
                <div className="space-y-5">
                  {/* Motor de datos */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider">
                        1. Motor de datos <span className="text-rose-500">*</span>
                      </label>
                      <span className="text-[10px] text-app-muted">{ENGINES.length} motores soportados</span>
                    </div>
                    <div role="radiogroup" aria-label="Motor de base de datos" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
                      {ENGINES.map((engine) => {
                        const selected = remoteForm.dbType === engine.id;
                        const Icon = engine.icon;
                        return (
                          <button
                            key={engine.id}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            onClick={() => handleRemoteDbTypeChange(engine.id)}
                            className={`relative flex flex-col items-start gap-2 p-3 rounded-xl border text-left transition-colors cursor-pointer ${
                              selected
                                ? 'bg-brand-500/10 border-brand-500/60 ring-1 ring-brand-500/40'
                                : 'bg-app-card border-app-border hover:bg-app-hover'
                            }`}
                          >
                            {selected && (
                              <span className="absolute top-2 right-2 w-4 h-4 rounded-full bg-brand-600 text-white flex items-center justify-center">
                                <Check className="w-2.5 h-2.5" />
                              </span>
                            )}
                            <span className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                              selected ? 'bg-brand-600 text-white' : 'bg-app-bg text-app-muted border border-app-border'
                            }`}>
                              <Icon className="w-4 h-4" />
                            </span>
                            <span className="flex flex-col min-w-0">
                              <span className="text-[11px] font-bold text-app-text leading-tight">{engine.label}</span>
                              <span className="text-[10px] text-app-muted">{engine.hint}</span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Parámetros de endpoint y autenticación */}
                  <div className="rounded-xl bg-app-card border border-app-border p-4 space-y-4">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div>
                        <h5 className="text-xs font-bold text-app-text">2. Parámetros de endpoint y autenticación</h5>
                        <p className="text-[11px] text-app-muted">Las credenciales se cifran antes de guardarse y no se devuelven por la API.</p>
                      </div>
                      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-app-bg border border-app-border text-[10px] font-semibold text-app-muted shrink-0 self-start">
                        <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        <span>Cifrado de credenciales activo</span>
                      </span>
                    </div>
                    <ConnectorFormFields
                      name={remoteForm.name}
                      dbType={remoteForm.dbType}
                      host={remoteForm.host}
                      port={remoteForm.port}
                      databaseName={remoteForm.databaseName}
                      username={remoteForm.username}
                      password={remoteForm.password}
                      isActive={remoteForm.isActive}
                      editingConnector={null}
                      hideDbTypeSelector
                      onFieldChange={(field, val) => {
                        setTestResult(null);
                        setRemoteForm((prev) => {
                          const updated = { ...prev, [field]: val };
                          if (field === 'name') {
                            const oldSlug = prev.name.toLowerCase().replace(/[^a-z0-9_]/g, '_');
                            if (!prev.databaseName || prev.databaseName === oldSlug) {
                              updated.databaseName = String(val).toLowerCase().replace(/[^a-z0-9_]/g, '_');
                            }
                          }
                          return updated;
                        });
                      }}
                      onDbTypeChange={handleRemoteDbTypeChange}
                    />
                  </div>

                  {/* Resultado de la prueba de conectividad */}
                  {testResult && (
                    <div
                      role="status"
                      className={`p-3 rounded-xl border flex items-start gap-2.5 text-xs animate-fadeIn ${
                        testResult.success
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-700 dark:text-emerald-300'
                          : 'bg-rose-500/10 border-rose-500/30 text-rose-700 dark:text-rose-300'
                      }`}
                    >
                      {testResult.success ? (
                        <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                      ) : (
                        <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                      )}
                      <div className="min-w-0 space-y-0.5">
                        <span className="font-semibold block">{testResult.message}</span>
                        {testResult.success && testResult.latency_ms > 0 && (
                          <span className="text-[11px] opacity-90">Latencia medida: {testResult.latency_ms} ms</span>
                        )}
                      </div>
                    </div>
                  )}

                  <p className="text-[10px] text-app-muted">
                    Cada base de datos se crea de forma individual e independiente en el sistema.
                  </p>
                </div>
              )}
            </form>
          )}

          {/* ================= STEP 2: CHOICE & EVALUATION ================= */}
          {currentStep === 'choice' && registeredConnection && (
            <div className="space-y-5 animate-fadeIn">
              {/* Step header */}
              <div className="flex flex-col gap-1 pb-3 border-b border-app-border">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
                    Paso 2 de 5
                  </span>
                  <span className="text-[11px] text-app-muted">Fuente registrada</span>
                </div>
                <h4 className="text-sm font-bold text-app-text">Evaluación de la fuente y modo de creación</h4>
                <p className="text-xs text-app-muted leading-relaxed max-w-lg">
                  Verificá el resumen de la conexión y elegí cómo construir el diccionario y catálogo de datos.
                </p>
              </div>

              {/* Evaluated summary card */}
              <div className="p-4 rounded-2xl bg-app-card border border-app-border flex items-center justify-between gap-3">
                <div className="flex items-center space-x-3 min-w-0">
                  <div className="p-2.5 rounded-xl bg-brand-500/10 text-brand-600 dark:text-brand-400 border border-brand-500/30 shrink-0">
                    <Database className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <h4 className="text-sm font-bold text-app-text truncate">{registeredConnection.name}</h4>
                    <p className="text-xs text-app-muted">
                      Motor: <span className="font-mono text-brand-600 dark:text-brand-400 uppercase">{registeredConnection.db_type}</span> • Estado: <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Evaluada y Conectada</span>
                    </p>
                  </div>
                </div>
                <div className="text-right text-xs shrink-0">
                  <span className="px-2.5 py-1 rounded-lg bg-app-bg border border-app-border text-app-muted font-mono">
                    ID #{registeredConnection.id}
                  </span>
                </div>
              </div>

              <div>
                <h4 className="text-sm font-bold text-app-text">¿Cómo deseas estructurar el Diccionario y Catálogo de Datos?</h4>
                <p className="text-xs text-app-muted mt-0.5">
                  Elige si deseas que la Inteligencia Artificial analice y describa las columnas, o hacerlo manualmente:
                </p>
              </div>

              {/* Mode Selection Cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Option 1: AI */}
                <div
                  role="button"
                  tabIndex={aiDisabled ? -1 : 0}
                  aria-disabled={aiDisabled}
                  onClick={() => { if (!aiDisabled) setCreationMode('ai'); }}
                  onKeyDown={(e) => { if (!aiDisabled && (e.key === 'Enter' || e.key === ' ')) setCreationMode('ai'); }}
                  className={`p-4 rounded-2xl border transition-all text-left space-y-3 relative ${
                    aiDisabled
                      ? 'border-app-border bg-app-card/40 opacity-60 cursor-not-allowed'
                      : creationMode === 'ai'
                        ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500 cursor-pointer'
                        : 'border-app-border bg-app-card hover:border-brand-500/40 cursor-pointer'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                      <Bot className="w-5 h-5" />
                    </div>
                    {llmStatus === 'available' ? (
                      <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                        Recomendado
                      </span>
                    ) : llmStatus === 'checking' ? (
                      <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-app-bg text-app-muted border border-app-border">
                        Verificando…
                      </span>
                    ) : (
                      <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-700 dark:text-rose-300 border border-rose-500/30">
                        No Disponible
                      </span>
                    )}
                  </div>
                  <div>
                    <h5 className="text-sm font-bold text-app-text flex items-center gap-1.5">
                      <span>Generar con Inteligencia Artificial</span>
                    </h5>
                    <p className="text-xs text-app-muted mt-1">
                      La IA examina la estructura, tipos y valores muestrales para formular descripciones semánticas, nombres de negocio y fórmulas automáticamente.
                    </p>
                    {llmStatus === 'available' && isEstimating && (
                      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-app-muted">
                        <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                        <span>Midiendo velocidad del modelo para estimar la duración…</span>
                      </div>
                    )}
                    {llmStatus === 'available' && !isEstimating && estimate && (
                      <div className="mt-2 space-y-1 text-[10px]">
                        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-brand-600 dark:text-brand-400">
                          <Clock className="w-3.5 h-3.5 shrink-0" />
                          <span>
                            Duración estimada: ~{formatDuration(estimate.min_seconds)} a {formatDuration(estimate.max_seconds)} (
                            {estimate.total_tables} tabla{estimate.total_tables === 1 ? '' : 's'} ·{' '}
                            {estimate.total_columns} columna{estimate.total_columns === 1 ? '' : 's'} ·{' '}
                            {estimate.tokens_per_second} tok/s medidos)
                          </span>
                        </div>
                        {estimate.beyond_budget && (
                          <div className="text-amber-700 dark:text-amber-300">
                            Supera el límite de {Math.round(estimate.budget_seconds / 60)} min por corrida: las últimas
                            tablas quedarán con reglas automáticas.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  {llmStatus === 'unavailable' ? (
                    /* Sin LLM: la razón del bloqueo, el mensaje del backend y un
                       reintento en caliente (encender el modelo sin cerrar). */
                    <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300 text-[11px] space-y-1">
                      <div className="flex items-center space-x-1.5 font-bold">
                        <WifiOff className="w-3.5 h-3.5 shrink-0" />
                        <span>No hay LLM conectada</span>
                      </div>
                      <p className="text-[10px] leading-relaxed opacity-90">
                        {llmMessage || 'No se pudo contactar al servidor LLM local. Verifica que llama.exe serve u Ollama esté activo.'}{' '}
                        Puedes reconectar el modelo en Opciones o continuar con la Creación Manual.
                      </p>
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); checkLlmAndEstimate(); }}
                        className="text-[10px] font-semibold underline underline-offset-2 hover:opacity-80"
                      >
                        Verificar de nuevo
                      </button>
                    </div>
                  ) : (
                    /* Warning disclaimer inside AI card */
                    <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-300 text-[11px] space-y-1">
                      <div className="flex items-center space-x-1.5 font-bold">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                        <span>Aviso de Precaución</span>
                      </div>
                      <p className="text-[10px] leading-relaxed opacity-90">
                        La IA puede cometer imprecisiones en terminología o lógica de negocio. Se te permitirá auditar y corregir cada definición en el siguiente paso.
                      </p>
                    </div>
                  )}
                </div>

                {/* Option 2: Manual */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setCreationMode('manual')}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setCreationMode('manual'); }}
                  className={`p-4 rounded-2xl border transition-all cursor-pointer text-left space-y-3 relative ${
                    creationMode === 'manual'
                      ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500'
                      : 'border-app-border bg-app-card hover:border-brand-500/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-app-bg text-app-muted border border-app-border">
                      <UserCheck className="w-5 h-5" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-app-bg text-app-muted border border-app-border">
                      Manual
                    </span>
                  </div>
                  <div>
                    <h5 className="text-sm font-bold text-app-text">Creación Manual por el Usuario</h5>
                    <p className="text-xs text-app-muted mt-1">
                      Construye las descripciones y definiciones tú mismo sin inferencias de IA, manteniendo el control absoluto de cada término.
                    </p>
                  </div>
                  <div className="p-2.5 rounded-xl bg-app-bg/80 border border-app-border text-app-muted text-[10px] space-y-1">
                    <div className="flex items-center space-x-1.5 font-semibold text-app-text">
                      <ShieldCheck className="w-3.5 h-3.5 text-brand-500 shrink-0" />
                      <span>Control Total</span>
                    </div>
                    <p className="leading-relaxed">
                      El asistente introspeccionará el esquema físico y te llevará directamente a la mesa de edición para documentar las tablas.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ================= STEP 3: GENERATING (AI Progress) ================= */}
          {currentStep === 'generating' && (
            <div className="py-10 px-4 text-center space-y-6 animate-fadeIn max-w-lg mx-auto">
              <div className="relative mx-auto w-16 h-16">
                <div className="absolute inset-0 rounded-2xl bg-brand-600/20 animate-ping"></div>
                <div className="relative w-16 h-16 rounded-2xl bg-brand-600 flex items-center justify-center text-white shadow-xl shadow-brand-600/30">
                  <Bot className="w-8 h-8 animate-pulse" />
                </div>
              </div>

              <div className="space-y-1.5">
                <h4 className="text-base font-bold text-app-text">
                  Generando Diccionario de Datos con IA
                </h4>
                <p className="text-xs text-app-muted">
                  {estimate
                    ? `El modelo local está redactando descripciones de negocio para ${estimate.total_columns} columna${estimate.total_columns === 1 ? '' : 's'} de ${estimate.total_tables} tabla${estimate.total_tables === 1 ? '' : 's'}.`
                    : 'El modelo local está redactando descripciones de negocio y fórmulas para cada campo.'}
                </p>
              </div>

              {/* Progress Bar: avance real = tiempo transcurrido contra la
                  estimación medida del modelo. El 100% solo llega con la
                  respuesta del backend; por eso el tope visual es 96%. */}
              <div className="space-y-2">
                <div className="w-full bg-app-bg rounded-full h-2.5 overflow-hidden border border-app-border">
                  <div
                    className="bg-brand-600 h-2.5 rounded-full transition-all duration-700 ease-out"
                    style={{ width: `${generationPct}%` }}
                  ></div>
                </div>
                <div className="flex justify-between text-[11px] text-app-muted font-mono">
                  <span>Fase {generationPhaseIndex + 1} de 4 · {generationElapsed} s</span>
                  <span className="text-brand-600 dark:text-brand-400 font-semibold">{generationPct}%</span>
                </div>
                <p className="text-[11px] text-app-muted leading-relaxed">
                  {estimate
                    ? `Estimado: ${formatDuration(estimate.min_seconds)} a ${formatDuration(estimate.max_seconds)} (${estimate.tokens_per_second} tok/s medidos del modelo). Podés revisar y ajustar todo al terminar.`
                    : 'Con modelos locales esto puede tardar de uno a tres minutos. Podés revisar y ajustar todo al terminar.'}
                </p>
                {estimate?.beyond_budget && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400 leading-relaxed">
                    Supera el límite de {Math.round(estimate.budget_seconds / 60)} min por corrida:
                    se describirá lo que entre en ese tiempo y el resto queda heurístico para revisión.
                  </p>
                )}
              </div>

              {/* Active Step Indicator */}
              <div className="p-3.5 rounded-xl bg-app-card border border-brand-500/30 text-xs text-app-text flex items-center justify-center space-x-2.5">
                <Loader2 className="w-4 h-4 animate-spin text-brand-600 dark:text-brand-400 shrink-0" />
                <span>{GENERATION_PHASES[generationPhaseIndex]}</span>
              </div>
            </div>
          )}

          {/* ================= STEP 4: REVIEW & EDIT ================= */}
          {currentStep === 'review' && dictionaryData && (
            <div className="space-y-4 animate-fadeIn">
              {/* Step header */}
              <div className="flex flex-col gap-1 pb-3 border-b border-app-border">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
                    Paso 3 de 5
                  </span>
                  <span className="text-[11px] text-app-muted">
                    {creationMode === 'ai' ? 'Perfilado semántico con IA' : 'Edición manual del diccionario'}
                  </span>
                </div>
                <h4 className="text-sm font-bold text-app-text">Revisión y validación del diccionario de datos</h4>
                <p className="text-xs text-app-muted leading-relaxed max-w-2xl">
                  {creationMode === 'ai'
                    ? 'Verificá que la IA haya generado descripciones correctas. Podés ajustar cualquier campo antes de autorizar.'
                    : 'Definí los nombres de negocio, fórmulas y descripciones para cada columna detectada.'}
                </p>
              </div>

              {/* Resumen de lo procesado (conteos reales del diccionario) */}
              <div className="p-3 rounded-xl bg-app-card border border-app-border text-xs flex items-center justify-between gap-3">
                <div className="flex items-center space-x-2.5 text-app-muted min-w-0">
                  <Edit3 className="w-4 h-4 text-brand-500 shrink-0" />
                  <span className="truncate">
                    {dictionaryData.connection_name} · <span className="font-mono uppercase">{dictionaryData.db_type}</span>
                  </span>
                </div>
                <div className="text-right text-[11px] text-app-muted shrink-0">
                  <span className="text-app-text font-bold">{dictionaryData.total_tables}</span> tablas •{' '}
                  <span className="text-brand-600 dark:text-brand-400 font-bold">{dictionaryData.total_columns}</span> columnas
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
                {/* Master: tablas procesadas */}
                <aside className="lg:col-span-4 space-y-2">
                  <div className="flex items-center justify-between px-1">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-app-muted">Tablas procesadas</span>
                    <span className="text-[10px] text-app-muted">{dictionaryData.tables.length}</span>
                  </div>
                  <div className="flex flex-col gap-1.5 max-h-[46vh] overflow-y-auto custom-scrollbar pr-0.5">
                    {dictionaryData.tables.map((t, idx) => {
                      const isSelected = selectedTableIndex === idx;
                      return (
                        <button
                          key={t.table_name}
                          type="button"
                          onClick={() => setSelectedTableIndex(idx)}
                          aria-pressed={isSelected}
                          className={`w-full text-left p-2.5 rounded-lg border transition-colors cursor-pointer ${
                            isSelected
                              ? 'bg-brand-500/10 border-brand-500/50'
                              : 'bg-app-card border-app-border hover:bg-app-hover'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-1.5 min-w-0">
                              <TableIcon className={`w-3.5 h-3.5 shrink-0 ${isSelected ? 'text-brand-500' : 'text-app-muted'}`} />
                              <span className="font-mono text-[11px] font-semibold text-app-text truncate">{t.table_name}</span>
                            </span>
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted shrink-0">
                              {t.columns.length} col
                            </span>
                          </div>
                          <div className="text-[10px] text-app-muted pl-5 mt-0.5 truncate">
                            {t.row_count.toLocaleString()} filas · Esquema: {t.schema_name}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </aside>

                {/* Detail: columnas de la tabla activa */}
                <section className="lg:col-span-8 space-y-3">
                  {activeTable ? (
                    <>
                      <div className="flex items-center justify-between gap-3 pb-1">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <span className="w-9 h-9 rounded-lg bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center shrink-0">
                            <TableIcon className="w-4 h-4" />
                          </span>
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-sm font-bold text-app-text font-mono truncate">{activeTable.table_name}</span>
                              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted uppercase">
                                {dictionaryData.db_type}
                              </span>
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted">
                                Esquema: {activeTable.schema_name}
                              </span>
                            </div>
                            <span className="text-[10px] text-app-muted">Fuente: {dictionaryData.connection_name}</span>
                          </div>
                        </div>
                        {creationMode === 'ai' && (
                          <span className="text-[10px] text-amber-700 dark:text-amber-300 bg-amber-500/10 border border-amber-500/30 px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1">
                            <AlertTriangle className="w-3 h-3" />
                            <span>Revisá los campos de la IA</span>
                          </span>
                        )}
                      </div>

                      <div className="space-y-2.5 max-h-[46vh] overflow-y-auto pr-1 custom-scrollbar">
                        {activeTable.columns.map((col: DataDictionaryColumn) => {
                          const editKey = `${activeTable.table_name}.${col.name}`;
                          const values = editedColumns[editKey] || {
                            friendly_name: col.friendly_name || col.name,
                            description: col.description || '',
                            business_formula: col.business_formula || '',
                          };

                          return (
                            <div
                              key={col.name}
                              className="p-3 rounded-xl bg-app-card border border-app-border hover:border-brand-500/40 transition-colors space-y-2.5"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                {col.is_pk && (
                                  <span title="Clave Primaria" className="text-amber-500">
                                    <Key className="w-3.5 h-3.5" />
                                  </span>
                                )}
                                <span className="font-mono text-xs font-bold text-app-text">{col.name}</span>
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-brand-600 dark:text-brand-400">
                                  {col.data_type}
                                </span>
                                {!col.is_nullable && (
                                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-app-bg border border-app-border text-app-muted">
                                    No nulo
                                  </span>
                                )}

                                {/* Samples Preview */}
                                {col.sample_values && col.sample_values.length > 0 && (
                                  <div className="flex items-center gap-1 text-[10px] text-app-muted ml-auto min-w-0">
                                    <span className="shrink-0">Ejemplos:</span>
                                    <div className="flex gap-1 overflow-hidden">
                                      {col.sample_values.slice(0, 2).map((val, vIdx) => (
                                        <span
                                          key={`${col.name}-sample-${val}-${vIdx}`}
                                          className="font-mono bg-app-bg px-1.5 py-0.5 rounded border border-app-border text-app-muted truncate max-w-[100px]"
                                        >
                                          {val}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>

                              {/* Editable Fields */}
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                                <div>
                                  <label
                                    htmlFor={`wizard-fn-${activeTable.table_name}-${col.name}`}
                                    className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                                  >
                                    Nombre Amigable
                                  </label>
                                  <input
                                    id={`wizard-fn-${activeTable.table_name}-${col.name}`}
                                    type="text"
                                    value={values.friendly_name}
                                    onChange={(e) =>
                                      handleColumnEditChange(activeTable.table_name, col.name, 'friendly_name', e.target.value)
                                    }
                                    placeholder="Ej: Monto Facturado"
                                    className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500"
                                  />
                                </div>

                                <div>
                                  <label
                                    htmlFor={`wizard-formula-${activeTable.table_name}-${col.name}`}
                                    className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                                  >
                                    Fórmula o Regla
                                  </label>
                                  <input
                                    id={`wizard-formula-${activeTable.table_name}-${col.name}`}
                                    type="text"
                                    value={values.business_formula}
                                    onChange={(e) =>
                                      handleColumnEditChange(activeTable.table_name, col.name, 'business_formula', e.target.value)
                                    }
                                    placeholder="Ej: Columna directa, sum(monto_usd)"
                                    className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500 font-mono"
                                  />
                                </div>

                                <div className="sm:col-span-2">
                                  <label
                                    htmlFor={`wizard-desc-${activeTable.table_name}-${col.name}`}
                                    className="block text-[10px] font-semibold text-app-muted uppercase tracking-wider mb-1"
                                  >
                                    Definición / Descripción Funcional
                                  </label>
                                  <textarea
                                    id={`wizard-desc-${activeTable.table_name}-${col.name}`}
                                    rows={2}
                                    value={values.description}
                                    onChange={(e) =>
                                      handleColumnEditChange(activeTable.table_name, col.name, 'description', e.target.value)
                                    }
                                    placeholder="Describe el significado para el negocio..."
                                    className="w-full bg-app-bg border border-app-border rounded-lg px-2.5 py-1.5 text-xs text-app-text placeholder:text-app-muted focus:outline-none focus:border-brand-500 resize-none"
                                  />
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <div className="py-8 text-center text-app-muted text-xs">
                      No se encontraron tablas para revisar en esta base de datos.
                    </div>
                  )}
                </section>
              </div>
            </div>
          )}

          {/* ================= STEP 4.5: NULL REMEDIATION ================= */}
          {currentStep === 'nulls_check' && nullsData && (
            <div className="space-y-5 animate-fadeIn">
              {/* Alert Header */}
              <div className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center space-x-2.5 min-w-0">
                    <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0" />
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0">
                        Paso 4 de 5
                      </span>
                      <h4 className="text-sm font-bold text-app-text truncate">Auditoría de Calidad: Valores Nulos Detectados</h4>
                    </div>
                  </div>
                  <span className="text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0">
                    {nullsData.total_tables_with_nulls} {nullsData.total_tables_with_nulls === 1 ? 'tabla afectada' : 'tablas afectadas'}
                  </span>
                </div>
                <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                  Se detectaron campos con valores <code className="font-mono text-amber-700 dark:text-amber-200">NULL</code> en los datos recién ingresados.
                  Como administrador, define la política corporativa que aplicará a estos registros:
                </p>
              </div>

              {/* Table / Column Breakdown List */}
              <div className="p-3.5 rounded-2xl bg-app-bg border border-app-border space-y-2.5 max-h-36 overflow-y-auto custom-scrollbar">
                <span className="text-[10px] font-bold text-app-muted uppercase tracking-wider block">
                  Columnas afectadas por tabla:
                </span>
                <div className="space-y-2">
                  {nullsData.tables.map((tbl) => (
                    <div key={tbl.table_name} className="flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="font-mono font-bold text-app-text bg-app-card px-2 py-0.5 rounded border border-app-border">
                        {tbl.table_name}
                      </span>
                      <span className="text-app-muted">→</span>
                      {tbl.columns_with_nulls.map((col) => (
                        <span
                          key={col.column_name}
                          className="font-mono bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded text-[11px]"
                        >
                          {col.column_name}: <strong>{col.null_count}</strong> nulos ({col.null_percentage}%)
                        </span>
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              {/* 4 Interactive Choice Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                {/* Option 1: Delete Rows */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedNullPolicy('delete_rows')}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedNullPolicy('delete_rows'); }}
                  className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
                    selectedNullPolicy === 'delete_rows'
                      ? 'border-rose-500 bg-rose-500/10 shadow-lg shadow-rose-500/10 ring-1 ring-rose-500'
                      : 'border-app-border bg-app-card hover:border-rose-500/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30">
                      <Trash2 className="w-4 h-4" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-700 dark:text-rose-300 border border-rose-500/30">
                      Depuración
                    </span>
                  </div>
                  <h5 className="text-xs font-bold text-app-text">1. Eliminar registros con nulos</h5>
                  <p className="text-[11px] text-app-muted leading-relaxed">
                    Elimina físicamente de la base de datos las filas que contienen campos vacíos, garantizando que solo existan filas 100% completas.
                  </p>
                </div>

                {/* Option 2: Mode */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedNullPolicy('mode')}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedNullPolicy('mode'); }}
                  className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
                    selectedNullPolicy === 'mode'
                      ? 'border-brand-500 bg-brand-500/10 shadow-lg shadow-brand-500/10 ring-1 ring-brand-500'
                      : 'border-app-border bg-app-card hover:border-brand-500/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                      <BarChart3 className="w-4 h-4" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-600 dark:text-brand-400 border border-brand-500/30">
                      Estadística
                    </span>
                  </div>
                  <h5 className="text-xs font-bold text-app-text">2. Adaptar a la moda estadística</h5>
                  <p className="text-[11px] text-app-muted leading-relaxed">
                    Imputa cada valor nulo con el valor más frecuente de su columna, preservando la tendencia y distribución mayoritaria.
                  </p>
                </div>

                {/* Option 3: Nearest */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedNullPolicy('nearest')}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedNullPolicy('nearest'); }}
                  className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
                    selectedNullPolicy === 'nearest'
                      ? 'border-cyan-500 bg-cyan-500/10 shadow-lg shadow-cyan-500/10 ring-1 ring-cyan-500'
                      : 'border-app-border bg-app-card hover:border-cyan-500/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30">
                      <MapPin className="w-4 h-4" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30">
                      Proximidad
                    </span>
                  </div>
                  <h5 className="text-xs font-bold text-app-text">3. Adaptar al más cercano</h5>
                  <p className="text-[11px] text-app-muted leading-relaxed">
                    Rellena cada valor nulo con el dato contiguo no-nulo más próximo (interpolación de arrastre / forward-fill).
                  </p>
                </div>

                {/* Option 4: Open to User */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedNullPolicy('open')}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedNullPolicy('open'); }}
                  className={`p-3.5 rounded-2xl border transition-all cursor-pointer text-left space-y-2 relative ${
                    selectedNullPolicy === 'open'
                      ? 'border-emerald-500 bg-emerald-500/10 shadow-lg shadow-emerald-500/10 ring-1 ring-emerald-500'
                      : 'border-app-border bg-app-card hover:border-emerald-500/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="p-2 rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30">
                      <Sparkles className="w-4 h-4" />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
                      Recomendado
                    </span>
                  </div>
                  <h5 className="text-xs font-bold text-app-text">4. Dejar la opción abierta al usuario</h5>
                  <p className="text-[11px] text-app-muted leading-relaxed">
                    Conserva los datos intactos. Cuando una consulta maneje nulos, la IA le advertirá previamente y le dará a elegir qué hacer.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* ================= STEP 5: COMPLETE ================= */}
          {currentStep === 'complete' && registeredConnection && (
            <div className="py-8 px-4 text-center space-y-5 animate-fadeIn max-w-md mx-auto">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 flex items-center justify-center mx-auto shadow-lg shadow-emerald-500/20">
                <CheckCircle2 className="w-8 h-8" />
              </div>
              <div className="space-y-1.5">
                <h4 className="text-base font-bold text-app-text">
                  ¡Base de Datos y Diccionario Instalados Exitosamente!
                </h4>
                <p className="text-xs text-app-muted">
                  La fuente de datos <strong className="text-app-text">{registeredConnection.name}</strong> ha sido registrada, estructurada y catalogada.
                </p>
              </div>

              <div className="p-4 rounded-2xl bg-app-bg border border-app-border text-left text-xs space-y-2">
                <div className="flex justify-between">
                  <span className="text-app-muted">Fuente:</span>
                  <span className="text-app-text font-semibold">{registeredConnection.name}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-app-muted">Modo de Creación:</span>
                  <span className="text-brand-600 dark:text-brand-400 font-semibold">{creationMode === 'ai' ? 'Asistido por IA (Auditado)' : 'Manual por Usuario'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-app-muted">Tablas Catalogadas:</span>
                  <span className="text-emerald-600 dark:text-emerald-400 font-semibold">{dictionaryData?.total_tables || 1} Tablas</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-app-muted">Gobernanza RBAC:</span>
                  <span className="text-cyan-700 dark:text-cyan-300 font-semibold">Reglas Activas</span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Fixed Sticky Footer Actions */}
        <div className="shrink-0 px-6 py-3.5 border-t border-app-border bg-app-surface/95 backdrop-blur flex items-center justify-between gap-2">
          {/* Back Button (Only on Step 2) */}
          {currentStep === 'choice' ? (
            <button
              type="button"
              onClick={() => setCurrentStep('input')}
              className="flex items-center space-x-1.5 px-4 py-2 rounded-xl text-xs font-medium text-app-muted hover:text-app-text hover:bg-app-hover transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>Volver a Fuente</span>
            </button>
          ) : (
            <div></div>
          )}

          {/* Action Buttons depending on Step */}
          <div className="flex items-center space-x-2.5 flex-wrap justify-end">
            {currentStep === 'input' && (
              <>
                <button
                  type="button"
                  onClick={handleClose}
                  disabled={isProcessing}
                  className="px-4 py-2 rounded-xl text-xs font-medium text-app-muted hover:text-app-text hover:bg-app-hover transition-colors"
                >
                  Cancelar
                </button>
                {ingestionMode === 'remote' && (
                  <button
                    type="button"
                    onClick={handleTestRemoteConnection}
                    disabled={isTestingConnection || !remoteForm.host.trim() || !remoteForm.databaseName.trim()}
                    className="flex items-center space-x-1.5 px-4 py-2 rounded-xl text-xs font-semibold bg-app-card hover:bg-app-hover text-app-text border border-app-border transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {isTestingConnection ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Zap className="w-4 h-4 text-amber-500" />
                    )}
                    <span>{isTestingConnection ? 'Probando...' : 'Probar conexión'}</span>
                  </button>
                )}
                <button
                  type="submit"
                  form="wizard-step-1"
                  disabled={isProcessing || (ingestionMode === 'file' && !selectedFile)}
                  className="flex items-center space-x-2 px-5 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {isProcessing ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Ingresando Fuente...</span>
                    </>
                  ) : (
                    <>
                      <span>Ingresar y Evaluar BD</span>
                      <ArrowRight className="w-4 h-4" />
                    </>
                  )}
                </button>
              </>
            )}

            {currentStep === 'choice' && (
              <button
                type="button"
                onClick={handleStartDictionaryCreation}
                disabled={isProcessing}
                className="flex items-center space-x-2 px-5 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-sm transition-all"
              >
                {isProcessing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Cargando esquema...</span>
                  </>
                ) : (
                  <>
                    <span>{creationMode === 'ai' ? 'Iniciar con IA y Revisar' : 'Continuar Manualmente'}</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            )}

            {currentStep === 'generating' && (
              <div className="text-xs text-app-muted italic">
                Procesando diccionario...
              </div>
            )}

            {currentStep === 'review' && (
              <>
                <button
                  type="button"
                  onClick={() => setCurrentStep('choice')}
                  disabled={isSavingReview}
                  className="px-4 py-2 rounded-xl text-xs font-medium text-app-muted hover:text-app-text hover:bg-app-hover transition-colors"
                >
                  Volver a Modo
                </button>
                <button
                  type="button"
                  onClick={handleSaveDictionaryReview}
                  disabled={isSavingReview}
                  className="flex items-center space-x-2 px-5 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm shadow-emerald-600/30 transition-all disabled:opacity-50"
                >
                  {isSavingReview ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Guardando Cambios...</span>
                    </>
                  ) : (
                    <>
                      <Check className="w-4 h-4" />
                      <span>Confirmar y Finalizar Instalación</span>
                    </>
                  )}
                </button>
              </>
            )}

            {currentStep === 'nulls_check' && (
              <>
                <button
                  type="button"
                  onClick={() => setCurrentStep('review')}
                  disabled={isApplyingNullPolicy}
                  className="px-4 py-2 rounded-xl text-xs font-medium text-app-muted hover:text-app-text hover:bg-app-hover transition-colors"
                >
                  Volver a Diccionario
                </button>
                <button
                  type="button"
                  onClick={handleApplyNullPolicy}
                  disabled={isApplyingNullPolicy}
                  className="flex items-center space-x-2 px-5 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm shadow-emerald-600/30 transition-all disabled:opacity-50"
                >
                  {isApplyingNullPolicy ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Aplicando Tratamiento...</span>
                    </>
                  ) : (
                    <>
                      <Check className="w-4 h-4" />
                      <span>Confirmar Tratamiento y Finalizar</span>
                    </>
                  )}
                </button>
              </>
            )}

            {currentStep === 'complete' && (
              <div className="flex items-center space-x-2">
                {onNavigateToCatalog && (
                  <button
                    type="button"
                    onClick={() => {
                      handleClose();
                      onNavigateToCatalog();
                    }}
                    className="px-4 py-2 rounded-xl text-xs font-medium text-brand-600 dark:text-brand-400 hover:text-app-text bg-brand-500/10 hover:bg-brand-500/20 border border-brand-500/30 transition-colors"
                  >
                    Ver en Catálogo
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleClose}
                  className="px-5 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white shadow-sm transition-all"
                >
                  Finalizar
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
