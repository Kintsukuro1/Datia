import React, { useState, useRef } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';
import { connectorService, ConnectionTestResult, CorporateConnection } from '../../features/admin/services/connector_service';
import {
  catalogService,
  AutoEnrichResponse,
  DataDictionaryResponse,
  EnrichEstimateResponse,
  NullsAuditResponse,
} from '../../features/admin/services/catalog_service';
import { useAuth } from '../../features/auth/context/AuthContext';
import { apiClient, getApiErrorMessage } from '../../shared/api/api_client';
import { SystemHealthResponse } from '../../types';
import { useModalA11y } from '../../hooks/useModalA11y';
import { ChoiceStep } from './database-wizard/ChoiceStep';
import { CompleteStep } from './database-wizard/CompleteStep';
import { GeneratingStep } from './database-wizard/GeneratingStep';
import { InputStep } from './database-wizard/InputStep';
import { NullsStep } from './database-wizard/NullsStep';
import { ReviewStep } from './database-wizard/ReviewStep';
import {
  UI_STEP_INDEX,
  WIZARD_STEPS,
  type ColumnEdit,
  type CreationMode,
  type IngestionMode,
  type LlmStatus,
  type NullPolicy,
  type RemoteFormState,
  type WizardStep,
} from './database-wizard/wizardConfig';

interface DatabaseWizardModalProps {
  isOpen: boolean;
  initialMode?: IngestionMode;
  onClose: () => void;
  onSuccess: () => void;
  onNavigateToCatalog?: () => void;
}

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
  const [remoteForm, setRemoteForm] = useState<RemoteFormState>({
    name: '',
    dbType: 'postgresql',
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
  const [editedColumns, setEditedColumns] = useState<Record<string, ColumnEdit>>({});
  const [isSavingReview, setIsSavingReview] = useState(false);

  // Null Remediation State
  const [nullsData, setNullsData] = useState<NullsAuditResponse | null>(null);
  const [selectedNullPolicy, setSelectedNullPolicy] = useState<NullPolicy>('open');
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
    } catch (err) {
      const msg = getApiErrorMessage(err, 'Error al conectar/ingresar la fuente de datos.', true);
      setErrorMessage(msg);
      setIsProcessing(false);
    }
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
      } catch {
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
      } catch {
        setIsProcessing(false);
        setErrorMessage('No se pudo introspeccionar la base de datos para edición manual.');
      }
    }
  };

  const initializeEditedColumns = (dict: DataDictionaryResponse) => {
    const edits: Record<string, ColumnEdit> = {};
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
    field: keyof ColumnEdit,
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
    } catch {
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
    } catch (err) {
      setIsApplyingNullPolicy(false);
      setErrorMessage(getApiErrorMessage(err, 'Error al aplicar el tratamiento de valores nulos.', true));
    }
  };

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
            <InputStep
              ingestionMode={ingestionMode}
              onIngestionModeChange={setIngestionMode}
              selectedFile={selectedFile}
              onRemoveFile={() => setSelectedFile(null)}
              customName={customName}
              onCustomNameChange={setCustomName}
              isDragging={isDragging}
              onDraggingChange={setIsDragging}
              fileInputRef={fileInputRef}
              onFileChange={handleFileChange}
              onDrop={handleDrop}
              remoteForm={remoteForm}
              setRemoteForm={setRemoteForm}
              testResult={testResult}
              setTestResult={setTestResult}
              onSubmit={handleIngestDatabase}
            />
          )}

          {/* ================= STEP 2: CHOICE & EVALUATION ================= */}
          {currentStep === 'choice' && registeredConnection && (
            <ChoiceStep
              connection={registeredConnection}
              creationMode={creationMode}
              onCreationModeChange={setCreationMode}
              llmStatus={llmStatus}
              llmMessage={llmMessage}
              isEstimating={isEstimating}
              estimate={estimate}
              onRetryLlm={checkLlmAndEstimate}
            />
          )}

          {/* ================= STEP 3: GENERATING (AI Progress) ================= */}
          {currentStep === 'generating' && (
            <GeneratingStep
              estimate={estimate}
              generationPhaseIndex={generationPhaseIndex}
              generationElapsed={generationElapsed}
              generationPct={generationPct}
            />
          )}

          {/* ================= STEP 4: REVIEW & EDIT ================= */}
          {currentStep === 'review' && dictionaryData && (
            <ReviewStep
              dictionaryData={dictionaryData}
              creationMode={creationMode}
              selectedTableIndex={selectedTableIndex}
              onSelectTable={setSelectedTableIndex}
              editedColumns={editedColumns}
              onColumnEditChange={handleColumnEditChange}
            />
          )}

          {/* ================= STEP 4.5: NULL REMEDIATION ================= */}
          {currentStep === 'nulls_check' && nullsData && (
            <NullsStep
              nullsData={nullsData}
              selectedNullPolicy={selectedNullPolicy}
              onPolicyChange={setSelectedNullPolicy}
            />
          )}

          {/* ================= STEP 5: COMPLETE ================= */}
          {currentStep === 'complete' && registeredConnection && (
            <CompleteStep
              connection={registeredConnection}
              creationMode={creationMode}
              dictionaryData={dictionaryData}
            />
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
