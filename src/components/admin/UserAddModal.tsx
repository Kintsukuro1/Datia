import React, { useState } from 'react';
import { X, UserPlus, ShieldCheck, KeyRound, Copy, Check, CheckCircle2 } from 'lucide-react';
import { UserItem } from './AdminUsersTab';
import { authService } from '../../features/auth/services/auth_service';
import { parseServerDate } from '../../features/auth/temp_password';
import { UserCreationResult } from '../../types';
import { useModalA11y } from '../../hooks/useModalA11y';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import { resolveRoleLabel } from '../../constants';
import { getApiErrorMessage } from '../../shared/api/api_client';

interface UserAddModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (created: UserItem) => void;
}

export const UserAddModal: React.FC<UserAddModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
}) => {
  const [newUsername, setNewUsername] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [created, setCreated] = useState<UserCreationResult | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { copied, copy, reset: resetCopy } = useCopyFeedback(3000);

  const resetForm = () => {
    setNewUsername('');
    setNewEmail('');
    setCreated(null);
    setCreateError(null);
    resetCopy();
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  // Dialog semantics, Escape, focus containment and focus restore.
  const modalRef = useModalA11y<HTMLDivElement>(isOpen, handleClose);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUsername.trim()) {
      setCreateError('Por favor completa el nombre de usuario.');
      return;
    }

    setIsSubmitting(true);
    setCreateError(null);

    try {
      // El servidor genera la clave y devuelve el usuario real (rol incluido).
      // Construir la fila con datos sintéticos rompería el siguiente Editar /
      // Sesiones / Reset, y afirmar un rol que la cuenta no tiene es peor que
      // decir que no tiene ninguno.
      const data = await authService.createUser({
        username: newUsername.trim(),
        email: newEmail.trim() || undefined,
      });

      const createdItem: UserItem = {
        id: data.user.id,
        name: data.user.username,
        email: data.user.email || `${data.user.username}@empresa.com`,
        role: resolveRoleLabel(data.user),
        is_admin: data.user.is_admin,
        is_active: data.user.is_active,
        must_change_password: data.user.must_change_password,
        temp_password_expires_at: data.user.temp_password_expires_at,
      };

      setNewUsername('');
      setNewEmail('');
      setCreateError(null);
      onSuccess(createdItem);
      setCreated(data);
    } catch (err: any) {
      setCreateError(getApiErrorMessage(err, 'Error al crear el nuevo usuario.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const expiresAt = created?.user.temp_password_expires_at
    ? parseServerDate(created.user.temp_password_expires_at)
    : NaN;
  const expiresLabel = Number.isFinite(expiresAt)
    ? new Date(expiresAt).toLocaleString()
    : null;

  return (
    <div ref={modalRef} role="dialog" aria-modal="true" aria-label="Crear nuevo usuario" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm animate-fadeIn">
      <div className="glass-panel w-full max-w-md rounded-2xl sm:rounded-3xl border border-white/10 shadow-2xl overflow-hidden flex flex-col max-h-[88vh] sm:max-h-[90vh]">
        {/* Header */}
        <div className="shrink-0 px-5 sm:px-6 py-4 border-b border-app-border flex items-center justify-between bg-app-surface/95 backdrop-blur">
          <h4 className="text-sm font-bold text-app-text flex items-center gap-2">
            <UserPlus className="w-4 h-4 text-purple-400" /> Crear Nuevo Usuario
          </h4>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Cerrar modal"
            className="text-gray-400 hover:text-app-text p-1 rounded-lg hover:bg-app-card transition-colors shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0 p-5 sm:p-6 space-y-3.5 text-xs">
          {createError && (
            <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs">
              {createError}
            </div>
          )}

          {created ? (
            <div className="space-y-4">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-app-text">
                    Cuenta «{created.user.username}» creada.
                  </p>
                  <p className="text-gray-400 mt-0.5">
                    Entrega esta contraseña temporal al usuario. Solo se muestra esta vez.
                  </p>
                </div>
              </div>

              <div className="rounded-xl border border-purple-500/30 bg-purple-500/10 p-3 space-y-2">
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-purple-300">
                  <KeyRound className="w-3.5 h-3.5" />
                  Contraseña temporal
                </div>
                <code className="block break-all rounded-lg bg-app-bg px-3 py-2 text-sm font-mono text-app-text select-all">
                  {created.temporary_password}
                </code>
                <button
                  type="button"
                  onClick={() => copy(created.temporary_password)}
                  className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-[11px] text-purple-300 hover:bg-purple-500/20 transition-colors"
                >
                  {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? 'Copiada' : 'Copiar contraseña'}
                </button>
              </div>

              <div className="flex items-start gap-2 rounded-xl border border-app-border/60 bg-app-bg/40 px-3 py-2.5 text-[11px] text-gray-400">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span>
                  La cuenta debe cambiarla en su primer inicio de sesión. La temporal
                  vence {expiresLabel ? <>el <strong className="text-app-text">{expiresLabel}</strong></> : 'a los 7 días'};
                  si no alcanza a cambiarla antes, solo podrá hacerlo desde la pantalla de cambio de clave.
                </span>
              </div>

              <p className="text-[11px] text-gray-400">
                Perfil asignado: <strong className="text-app-text">{resolveRoleLabel(created.user)}</strong>.
                Puedes cambiarlo desde <strong className="text-app-text">Editar</strong>.
              </p>
            </div>
          ) : (
            <form id="user-add-form" onSubmit={handleSubmit} className="space-y-3.5">
              <div>
                <label htmlFor="new-user-username" className="block text-gray-300 font-medium mb-1">
                  Nombre de Usuario
                </label>
                <input
                  id="new-user-username"
                  aria-label="Nombre de Usuario"
                  type="text"
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="ej. felipe_analista"
                  maxLength={50}
                  className="w-full bg-app-bg border border-app-border rounded-xl px-3 py-2 text-app-text focus:outline-none focus:border-purple-500"
                  required
                />
              </div>

              <div>
                <label htmlFor="new-user-email" className="block text-gray-300 font-medium mb-1">
                  Correo Electrónico (opcional)
                </label>
                <input
                  id="new-user-email"
                  aria-label="Correo Electrónico"
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="usuario@empresa.com"
                  className="w-full bg-app-bg border border-app-border rounded-xl px-3 py-2 text-app-text focus:outline-none focus:border-purple-500"
                />
              </div>

              <div className="flex items-start space-x-2 text-[11px] text-gray-400 bg-app-bg/40 rounded-xl border border-app-border/60 px-3 py-2.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span>
                  La contraseña temporal la genera el sistema y se muestra una sola vez.
                  El usuario deberá cambiarla en su primer inicio de sesión. La cuenta
                  nace con el rol por defecto y sin privilegios de administrador.
                  Asigna su rol RBAC desde <strong className="text-app-text">Editar</strong>.
                </span>
              </div>
            </form>
          )}
        </div>

        {/* Fixed Sticky Footer Actions */}
        <div className="shrink-0 px-5 sm:px-6 py-3.5 border-t border-app-border bg-app-surface/95 backdrop-blur flex justify-end space-x-2 z-10">
          {created ? (
            <button
              type="button"
              onClick={handleClose}
              className="bg-purple-600 hover:bg-purple-500 text-white font-semibold px-4 py-2 rounded-xl text-xs transition-colors"
            >
              Entendido
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={handleClose}
                className="px-4 py-2 rounded-xl bg-app-card text-gray-300 text-xs hover:bg-app-border transition-colors"
              >
                Cancelar
              </button>
              <button
                form="user-add-form"
                type="submit"
                disabled={isSubmitting}
                className="bg-purple-600 hover:bg-purple-500 text-white font-semibold px-4 py-2 rounded-xl text-xs transition-colors disabled:opacity-50"
              >
                {isSubmitting ? 'Creando...' : 'Crear cuenta'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
