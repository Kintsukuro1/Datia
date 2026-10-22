import React, { useState } from 'react';
import { KeyRound, AlertTriangle } from 'lucide-react';
import { useAuth } from '../../features/auth/context/AuthContext';
import {
  daysUntilTempPasswordExpiry,
  isTempPasswordExpired,
} from '../../features/auth/temp_password';
import { MandatoryPasswordChangeModal } from './MandatoryPasswordChangeModal';

/**
 * Aviso persistente mientras la cuenta usa la clave temporal del alta.
 *
 * No bloquea: la cuenta nace operativa durante la ventana de gracia. Al vencer,
 * el backend devuelve 403 en cualquier ruta y el guard de `AppRouter` manda a
 * `/change-password`. El botón abre el mismo formulario sin esperar al bloqueo.
 */
export const TemporaryPasswordBanner: React.FC = () => {
  const { user, completePasswordChange } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  if (!user?.temp_password_expires_at || isTempPasswordExpired(user)) {
    return null;
  }

  const days = daysUntilTempPasswordExpiry(user);
  if (days === null) return null;
  const daysLabel = days === 1 ? '1 día' : `${days} días`;

  return (
    <>
      <div
        role="status"
        className="flex shrink-0 flex-wrap items-center justify-center gap-x-2 gap-y-1 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-700 dark:text-amber-300"
      >
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span>
          Estás usando una <strong>contraseña temporal</strong>. Te quedan {daysLabel} para cambiarla.
        </span>
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 px-2 py-0.5 font-semibold hover:bg-amber-500/20 transition-colors"
        >
          <KeyRound className="h-3 w-3" />
          Cambiar ahora
        </button>
      </div>

      <MandatoryPasswordChangeModal
        isOpen={isOpen}
        voluntary
        onSuccess={() => {
          completePasswordChange();
          setIsOpen(false);
        }}
        onCancel={() => setIsOpen(false)}
      />
    </>
  );
};
