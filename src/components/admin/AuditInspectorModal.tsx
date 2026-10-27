import React from 'react';
import { ShieldCheck, CheckCircle2, XCircle, AlertTriangle, X, Copy, Check, KeyRound, FileDown, TrendingUp, Wrench } from 'lucide-react';
import { AuditLog } from '../../types';
import { useModalA11y } from '../../hooks/useModalA11y';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';

interface AuditInspectorModalProps {
  log: AuditLog | null;
  onClose: () => void;
}

/**
 * El snapshot se guarda como JSON compacto. Se muestra legible; si algun dia
 * llega algo que no es JSON (texto libre), se muestra tal cual en vez de romper
 * el modal de auditoria.
 */
const prettySnapshot = (raw: string): string => {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
};

export const AuditStatusBadge: React.FC<{ status?: string | null }> = ({ status }) => {
  const s = (status || '').toUpperCase();
  if (s.includes('APROBADO')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20">
        <CheckCircle2 className="w-3.5 h-3.5" />
        <span>Aprobado</span>
      </span>
    );
  }
  if (s.includes('RECHAZADO')) {
    // "RECHAZADO (IA Local No Disponible)" no es un bloqueo de gobernanza: el
    // motor LLM estaba caido antes de validar nada. Compartir el badge rojo de
    // RBAC mandaba a revisar permisos por un problema de infraestructura.
    const engineOffline = s.includes('IA LOCAL NO DISPONIBLE');
    return (
      <span className={`inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold ${
        engineOffline
          ? 'bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/20'
          : 'bg-rose-50 text-rose-700 border border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20'
      }`}>
        {engineOffline ? <AlertTriangle className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
        <span>{engineOffline ? 'IA local no disponible' : 'Bloqueado RBAC'}</span>
      </span>
    );
  }
  if (s.includes('FUERA_DE_ALCANCE')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/20">
        <AlertTriangle className="w-3.5 h-3.5" />
        <span>Fuera de Alcance</span>
      </span>
    );
  }
  // Mutaciones de permisos: no son un estado de validacion de SQL, pero son el
  // rastro que exige el pilar de trazabilidad. Sin esta rama caian en el badge
  // ambar de "sin registro" con el nombre tecnico crudo.
  if (s.includes('PERMISOS_TABLA') || s.includes('PERMISOS_COLUMNA')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-400 dark:border-indigo-500/20">
        <ShieldCheck className="w-3.5 h-3.5" />
        <span>{s.includes('COLUMNA') ? 'Permisos de columna' : 'Permisos de tabla'}</span>
      </span>
    );
  }
  // Familias del vocabulario real de `audit_logs`. Sin estas ramas, un alta de
  // usuario, una exportacion o una proyeccion caian en el badge ambar de "sin
  // registro" con la constante tecnica cruda: se veian como eventos
  // desconocidos, no como lo que son.
  if (s.includes('AUTH_')) {
    if (s.includes('FALLIDO')) {
      return (
        <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20">
          <XCircle className="w-3.5 h-3.5" />
          <span>Login fallido</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-500/10 dark:text-sky-400 dark:border-sky-500/20">
        <KeyRound className="w-3.5 h-3.5" />
        <span>Evento de acceso</span>
      </span>
    );
  }
  if (s.includes('EXPORTADO_')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-500/10 dark:text-violet-400 dark:border-violet-500/20">
        <FileDown className="w-3.5 h-3.5" />
        <span>{s.includes('EXCEL') ? 'Exportado Excel' : 'Exportado PDF'}</span>
      </span>
    );
  }
  if (s.includes('PROYECCION') || s.includes('PREDICCION')) {
    const label = s.includes('VERIFICACION')
      ? 'Verificación de proyección'
      : s.includes('PREDICCION')
        ? 'Predicción determinista'
        : 'Proyección ML';
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-purple-50 text-purple-700 border border-purple-200 dark:bg-purple-500/10 dark:text-purple-400 dark:border-purple-500/20">
        <TrendingUp className="w-3.5 h-3.5" />
        <span>{label}</span>
      </span>
    );
  }
  if (s.includes('NULL_POLICY')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-50 text-teal-700 border border-teal-200 dark:bg-teal-500/10 dark:text-teal-400 dark:border-teal-500/20">
        <Wrench className="w-3.5 h-3.5" />
        <span>{s.includes('SIN_CAMBIOS') ? 'Nulos sin cambios' : 'Política de nulos'}</span>
      </span>
    );
  }
  // Estados de trazabilidad del chat que tambien se persisten en audit_logs.
  // Sin rama propia caian en el badge ambar con la constante tecnica cruda.
  if (s.includes('REVALIDADO')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-500/10 dark:text-sky-400 dark:border-sky-500/20">
        <CheckCircle2 className="w-3.5 h-3.5" />
        <span>Hilo compartido revalidado</span>
      </span>
    );
  }
  if (s.includes('SIN_CONEXION')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/20">
        <AlertTriangle className="w-3.5 h-3.5" />
        <span>{s.includes('REGISTRADA') ? 'Sin conexión registrada' : 'Sin conexión activa'}</span>
      </span>
    );
  }
  if (s.includes('DIAGNOSTICO')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 dark:bg-slate-500/10 dark:text-slate-300 dark:border-slate-500/20">
        <ShieldCheck className="w-3.5 h-3.5" />
        <span>Diagnóstico desconocido</span>
      </span>
    );
  }
  if (s.includes('ERROR_')) {
    return (
      <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-orange-50 text-orange-700 border border-orange-200 dark:bg-orange-500/10 dark:text-orange-400 dark:border-orange-500/20">
        <AlertTriangle className="w-3.5 h-3.5" />
        <span>Error de ejecución</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/20">
      <AlertTriangle className="w-3.5 h-3.5" />
      {/* Un log sin estado no es un bloqueo: es "sin registro". */}
      <span>{status || 'Sin registro'}</span>
    </span>
  );
};

export const AuditInspectorModal: React.FC<AuditInspectorModalProps> = ({ log, onClose }) => {
  const { copied: sqlCopied, copy: copySql } = useCopyFeedback();

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(Boolean(log), onClose);

  if (!log) return null;

  const handleCopySql = (sql: string) => {
    void copySql(sql);
  };

  // En eventos que no son consultas (accesos, permisos, politicas de nulos)
  // `question_prompt` guarda una descripcion, no una pregunta del usuario.
  const isQueryEvent = !/^(AUTH_|PERMISOS_|NULL_POLICY|HILO_COMPARTIDO)/.test((log.validation_status || '').toUpperCase());

  return (
    <div ref={modalRef} role="dialog" aria-modal="true" aria-label="Inspector de registro de auditoría" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div className="glass-panel w-full max-w-2xl rounded-2xl border border-slate-200 dark:border-white/10 p-6 space-y-4 shadow-2xl bg-white dark:bg-zinc-900">
        <div className="flex items-center justify-between border-b border-slate-200 dark:border-dark-border pb-3">
          <div className="flex items-center space-x-2">
            <ShieldCheck className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
            <div>
              <h4 className="text-sm font-bold text-slate-900 dark:text-white">
                Inspección de Auditoría - Evento #{log.id}
              </h4>
              <p className="text-xs text-slate-500 dark:text-gray-400">
                {new Date(log.timestamp).toLocaleString()} • {log.username} ({log.user_role})
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar modal de auditoría"
            className="text-slate-400 hover:text-slate-700 dark:text-gray-400 dark:hover:text-white p-1 rounded-lg transition-colors cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-3 text-xs">
          <div>
            <span className="text-slate-600 dark:text-gray-400 font-semibold uppercase text-[10px] tracking-wider">
              {isQueryEvent ? 'Pregunta en Lenguaje Natural:' : 'Descripción del evento:'}
            </span>
            <div className="p-3 bg-slate-50 dark:bg-dark-base rounded-xl border border-slate-200 dark:border-dark-border text-slate-900 dark:text-white mt-1">
              {log.question_prompt}
            </div>
          </div>

          {log.sql_generated ? (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-slate-600 dark:text-gray-400 font-semibold uppercase text-[10px] tracking-wider">
                  SQL Generado & Validado por AST Guardrail:
                </span>
                <button
                  type="button"
                  onClick={() => handleCopySql(log.sql_generated || '')}
                  aria-label="Copiar SQL de auditoría"
                  className="flex items-center space-x-1 text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300 transition-colors cursor-pointer"
                >
                  {sqlCopied ? (
                    <>
                      <Check className="w-3.5 h-3.5" />
                      <span>Copiado</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5" />
                      <span>Copiar SQL</span>
                    </>
                  )}
                </button>
              </div>
              <pre className="p-3 bg-slate-950 rounded-xl border border-slate-800 text-emerald-400 font-mono overflow-x-auto whitespace-pre-wrap">
                {log.sql_generated}
              </pre>
            </div>
          ) : (
            <div className="p-3 bg-slate-50 dark:bg-dark-base rounded-xl border border-slate-200 dark:border-dark-border text-slate-500 dark:text-gray-500 italic">
              No se generó consulta SQL para este evento (por ejemplo, un cambio de permisos o un bloqueo previo).
            </div>
          )}

          {log.result_snapshot && (
            <div>
              <span className="text-slate-600 dark:text-gray-400 font-semibold uppercase text-[10px] tracking-wider">
                Detalle del evento:
              </span>
              <pre className="p-3 mt-1 bg-slate-50 dark:bg-dark-base rounded-xl border border-slate-200 dark:border-dark-border text-slate-800 dark:text-gray-200 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap">
                {prettySnapshot(log.result_snapshot)}
              </pre>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 rounded-xl bg-slate-50 dark:bg-dark-base border border-slate-200 dark:border-dark-border">
              <span className="text-slate-500 dark:text-gray-500 text-[10px] uppercase font-semibold">Estado Validación</span>
              <div className="mt-1">
                <AuditStatusBadge status={log.validation_status} />
              </div>
            </div>
            <div className="p-3 rounded-xl bg-slate-50 dark:bg-dark-base border border-slate-200 dark:border-dark-border">
              <span className="text-slate-500 dark:text-gray-500 text-[10px] uppercase font-semibold">Latencia Ejecución</span>
              <div className="mt-1 text-slate-900 dark:text-white font-mono font-semibold">{log.execution_time_ms} ms</div>
            </div>
            <div className="p-3 rounded-xl bg-slate-50 dark:bg-dark-base border border-slate-200 dark:border-dark-border">
              <span className="text-slate-500 dark:text-gray-500 text-[10px] uppercase font-semibold">Filas Devueltas</span>
              <div className="mt-1 text-slate-900 dark:text-white font-mono font-semibold">{log.rows_returned}</div>
            </div>
          </div>

          {log.error_message && (
            <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/20 text-rose-800 dark:text-rose-300 text-xs">
              <strong className="block font-semibold mb-0.5">Mensaje de Diagnóstico / Error:</strong>
              <span>{log.error_message}</span>
            </div>
          )}
        </div>

        <div className="pt-3 flex justify-end border-t border-slate-200 dark:border-dark-border">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 dark:bg-dark-card dark:text-gray-300 text-xs font-medium dark:hover:bg-dark-border transition-colors cursor-pointer"
          >
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
};
