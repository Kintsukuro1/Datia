import React, { useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  ShieldCheck,
} from 'lucide-react';
import { authService } from '../../features/auth/services/auth_service';
import { getApiErrorMessage } from '../../shared/api/api_client';
import { useModalA11y } from '../../hooks/useModalA11y';

interface MandatoryPasswordChangeModalProps {
  isOpen: boolean;
  onSuccess: () => void;
  onCancel?: () => void;
}

// Regla dura única del backend: `backend/app/modules/auth/schemas.py`
// (`MIN_PASSWORD_LENGTH = 6`). No exige composición: por eso mayúsculas,
// números y símbolos alimentan el medidor como recomendación, no bloquean.
const MIN_PASSWORD_LENGTH = 6;

export interface PasswordStrength {
  score: number;
  label: 'Débil' | 'Media' | 'Buena' | 'Segura';
  checks: {
    length: boolean;
    mixedCase: boolean;
    number: boolean;
    symbol: boolean;
  };
}

/**
 * Evalúa una clave para el medidor de fortaleza. `length` es la única regla
 * que el servidor rechaza si no se cumple; el resto es aditivo.
 */
export const evaluatePassword = (raw: string): PasswordStrength => {
  const pw = raw ?? '';
  const checks = {
    length: pw.length >= MIN_PASSWORD_LENGTH,
    mixedCase: /[a-z]/.test(pw) && /[A-Z]/.test(pw),
    number: /\d/.test(pw),
    symbol: /[^A-Za-z0-9]/.test(pw),
  };
  const score = Object.values(checks).filter(Boolean).length;
  const label = score >= 4 ? 'Segura' : score === 3 ? 'Buena' : score === 2 ? 'Media' : 'Débil';
  return { score, label, checks };
};

const STRENGTH_BARS = ['bg-rose-500', 'bg-amber-500', 'bg-brand-500', 'bg-emerald-500'];
const STRENGTH_LABELS = ['text-rose-400', 'text-amber-400', 'text-brand-400', 'text-emerald-400'];

const CHECKLIST = [
  { key: 'length', text: `Mínimo ${MIN_PASSWORD_LENGTH} caracteres` },
  { key: 'mixedCase', text: 'Mayúsculas y minúsculas' },
  { key: 'number', text: 'Números' },
  { key: 'symbol', text: 'Símbolo (!@#$)' },
] as const;

export const MandatoryPasswordChangeModal: React.FC<MandatoryPasswordChangeModalProps> = ({
  isOpen,
  onSuccess,
  onCancel,
}) => {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One toggle for all three: confirming a new password means comparing the
  // last two, which needs both visible at the same time.
  const [showPasswords, setShowPasswords] = useState(false);

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, onCancel);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!oldPassword.trim()) {
      setError('Debes ingresar tu contraseña actual o temporal.');
      return;
    }

    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`La nueva contraseña debe tener al menos ${MIN_PASSWORD_LENGTH} caracteres.`);
      return;
    }

    if (newPassword !== confirmPassword) {
      setError('Las nuevas contraseñas no coinciden.');
      return;
    }

    if (oldPassword === newPassword) {
      setError('La nueva contraseña debe ser diferente a la actual.');
      return;
    }

    setLoading(true);
    try {
      await authService.changePassword(oldPassword, newPassword);
      onSuccess();
    } catch (err: any) {
      const msg = getApiErrorMessage(err, 'Error al actualizar la contraseña. Verifica tu clave actual.');
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const strength = evaluatePassword(newPassword);
  const strengthColorIdx = Math.max(strength.score, 1) - 1;
  const confirmMismatch = confirmPassword.length > 0 && confirmPassword !== newPassword;

  return (
    <div
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-label="Cambio de contraseña obligatorio"
      tabIndex={-1}
      className="animate-fadeIn fixed inset-0 z-[100] overflow-y-auto bg-app-bg"
    >
      {/* Decoración sutil de página (no de overlay): esta ruta es la pantalla
          completa, no un modal sobre el dashboard. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-60 [background-image:radial-gradient(ellipse_60%_50%_at_50%_35%,rgba(0,110,230,0.12),transparent_75%)]"
      />
      <div className="relative flex min-h-full items-center justify-center p-4">
        <div className="w-full max-w-[480px] space-y-5">
          <div className="glass-panel space-y-5 rounded-2xl border border-app-border p-6 shadow-2xl sm:p-8">
            {/* Encabezado */}
            <div className="flex items-start gap-4">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-600/15 text-brand-400">
                <KeyRound className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <h1 className="text-lg font-bold tracking-tight">Cambio de contraseña obligatorio</h1>
                <p className="text-xs leading-relaxed text-app-muted">
                  Tu clave es temporal o un administrador solicitó el cambio. Definí una nueva para continuar en la plataforma.
                </p>
              </div>
            </div>

            {error && (
              <div className="flex items-start gap-2.5 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-400">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="mandatory-old-pwd" className="block text-xs font-medium text-app-muted">
                  Contraseña actual o temporal
                </label>
                <div className="relative">
                  <input
                    id="mandatory-old-pwd"
                    aria-label="Contraseña Actual o Temporal"
                    name="currentPassword"
                    autoComplete="current-password"
                    type={showPasswords ? 'text' : 'password'}
                    value={oldPassword}
                    onChange={(e) => setOldPassword(e.target.value)}
                    placeholder="Ingresa la clave provisional..."
                    required
                    className="h-11 w-full rounded-xl border border-app-border bg-app-bg pl-10 pr-11 text-sm text-app-text placeholder-gray-500 transition-colors focus:border-brand-500 focus:outline-none"
                  />
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                  <button
                    type="button"
                    onClick={() => setShowPasswords((v) => !v)}
                    aria-label={showPasswords ? 'Ocultar contraseñas' : 'Mostrar contraseñas'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
                  >
                    {showPasswords ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label htmlFor="mandatory-new-pwd" className="block text-xs font-medium text-app-muted">
                    Nueva contraseña
                  </label>
                  <span className="text-[10px] text-app-muted">Mín. {MIN_PASSWORD_LENGTH} caracteres</span>
                </div>
                <div className="relative">
                  <input
                    id="mandatory-new-pwd"
                    aria-label="Nueva Contraseña"
                    name="newPassword"
                    autoComplete="new-password"
                    type={showPasswords ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Crea una contraseña segura..."
                    required
                    minLength={MIN_PASSWORD_LENGTH}
                    className="h-11 w-full rounded-xl border border-app-border bg-app-bg pl-10 pr-11 text-sm text-app-text placeholder-gray-500 transition-colors focus:border-brand-500 focus:outline-none"
                  />
                  <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                  <button
                    type="button"
                    onClick={() => setShowPasswords((v) => !v)}
                    aria-label={showPasswords ? 'Ocultar contraseñas' : 'Mostrar contraseñas'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
                  >
                    {showPasswords ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>

                {/* Medidor de fortaleza: la única regla obligatoria es el mínimo
                    de 6 (la del backend); la composición es recomendación. */}
                {newPassword.length > 0 && (
                  <div className="space-y-2.5 rounded-xl border border-app-border bg-app-card/40 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] text-app-muted">Fortaleza de la contraseña</span>
                      <span className={`text-[11px] font-semibold ${STRENGTH_LABELS[strengthColorIdx]}`}>
                        {strength.label} ({strength.score}/4)
                      </span>
                    </div>
                    <div className="grid grid-cols-4 gap-1.5" aria-hidden="true">
                      {[0, 1, 2, 3].map((i) => (
                        <div
                          key={i}
                          className={`h-1.5 rounded-full ${i < strength.score ? STRENGTH_BARS[strengthColorIdx] : 'bg-app-border'}`}
                        />
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
                      {CHECKLIST.map(({ key, text }) => {
                        const met = strength.checks[key];
                        return (
                          <div key={key} className="flex items-center gap-1.5">
                            {met ? (
                              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                            ) : (
                              <Circle className="h-3.5 w-3.5 shrink-0 text-app-muted" />
                            )}
                            <span className={`text-[11px] ${met ? 'text-app-text' : 'text-app-muted'}`}>{text}</span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-[10px] leading-snug text-app-muted">
                      El mínimo de {MIN_PASSWORD_LENGTH} caracteres es obligatorio; el resto solo mejora la fortaleza.
                    </p>
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <label htmlFor="mandatory-confirm-pwd" className="block text-xs font-medium text-app-muted">
                  Confirmar nueva contraseña
                </label>
                <div className="relative">
                  <input
                    id="mandatory-confirm-pwd"
                    aria-label="Confirmar Nueva Contraseña"
                    name="confirmPassword"
                    autoComplete="new-password"
                    type={showPasswords ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Repite la nueva contraseña..."
                    required
                    minLength={MIN_PASSWORD_LENGTH}
                    className={`h-11 w-full rounded-xl border bg-app-bg pl-10 pr-11 text-sm text-app-text placeholder-gray-500 transition-colors focus:border-brand-500 focus:outline-none ${
                      confirmMismatch ? 'border-rose-500/60' : 'border-app-border'
                    }`}
                  />
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                  <button
                    type="button"
                    onClick={() => setShowPasswords((v) => !v)}
                    aria-label={showPasswords ? 'Ocultar contraseñas' : 'Mostrar contraseñas'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
                  >
                    {showPasswords ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {confirmMismatch && (
                  <p className="flex items-center gap-1.5 text-[11px] text-rose-400">
                    <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                    Las nuevas contraseñas no coinciden.
                  </p>
                )}
              </div>

              <div className="space-y-2.5 pt-1">
                <button
                  type="submit"
                  disabled={loading}
                  className="flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-brand-600 text-sm font-semibold text-white shadow-md shadow-brand-600/25 transition-all hover:bg-brand-500 active:scale-[0.99] disabled:opacity-60"
                >
                  {loading ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>Cambiando contraseña…</span>
                    </>
                  ) : (
                    <>
                      <KeyRound className="h-4 w-4" />
                      <span>Cambiar contraseña</span>
                    </>
                  )}
                </button>

                {/*
                  Sin esto la pantalla es una caja sin salida: el logout del Header
                  no existe en esta ruta (sin navegación a propósito). Cerrar
                  sesión es la única salida y tiene que existir.
                */}
                {onCancel && (
                  <button
                    type="button"
                    onClick={onCancel}
                    className="flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-app-border bg-app-bg text-xs font-medium text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
                  >
                    <LogOut className="h-3.5 w-3.5" />
                    <span>Salir de mi cuenta</span>
                  </button>
                )}
              </div>
            </form>
          </div>

          {/* Auditoría real: cada login crea una UserSession registrada. */}
          <p className="flex items-center justify-center gap-1.5 text-[11px] text-app-muted">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
            <span>Esta sesión queda registrada en la bitácora de auditoría.</span>
          </p>
        </div>
      </div>
    </div>
  );
};
