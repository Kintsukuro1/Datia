export type WizardStep = 'input' | 'choice' | 'generating' | 'review' | 'nulls_check' | 'complete';
export type IngestionMode = 'file' | 'remote';
export type CreationMode = 'ai' | 'manual';
export type LlmStatus = 'checking' | 'available' | 'unavailable';
export type RemoteDbType = 'postgresql' | 'mssql' | 'mysql' | 'oracle';
export type NullPolicy = 'delete_rows' | 'mode' | 'nearest' | 'open';

export interface RemoteFormState {
  name: string;
  dbType: RemoteDbType;
  host: string;
  port: number;
  databaseName: string;
  username: string;
  password: string;
  isActive: boolean;
}

export type ColumnEdit = {
  friendly_name: string;
  description: string;
  business_formula: string;
};

// Estados del proceso (solo texto). El avance de la barra NO sale de acá: es
// el tiempo transcurrido contra la estimación medida del modelo (ver
// `generationPct`). Un porcentaje fijo mentía mientras el modelo trabajaba.
export const GENERATION_PHASES = [
  'Introspeccionando esquema físico y metadatos relacionales...',
  'Muestreando valores representativos de columnas...',
  'Invocando modelo de IA para descripciones de negocio y fórmulas...',
  'Compilando catálogo semántico y reglas de gobernanza...',
];

// Pasos visibles del tracker. 'generating' y 'review' comparten el paso 3
// (Diccionario): generar y revisar es el mismo tramo del flujo.
export const WIZARD_STEPS = [
  'Ingreso BD',
  'Evaluación & Modo',
  'Diccionario & IA',
  'Tratamiento Nulos',
  'Finalizado',
];

export const UI_STEP_INDEX: Record<WizardStep, number> = {
  input: 0,
  choice: 1,
  generating: 2,
  review: 2,
  nulls_check: 3,
  complete: 4,
};

// Segundos -> texto legible. Se redondea a pasos de 5s para no fingir una
// precisión que la estimación (una medición + una fórmula) no tiene.
export const formatDuration = (seconds?: number | null): string => {
  if (!seconds || seconds <= 0) return '—';
  if (seconds < 90) return `${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `${Math.round(seconds / 60)} min`;
};
