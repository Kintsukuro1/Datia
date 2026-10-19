import { useCallback, useEffect, useRef, useState } from 'react';
import { copyToClipboard } from '../shared/clipboard';

/**
 * Feedback temporizado de "Copiado", sin repetir el mismo estado a mano.
 *
 * `copyToClipboard` reporta exito real (en LAN por http la API no existe);
 * `copy()` enciende `copied` solo si copio de verdad. `flash()` es para los
 * call sites que escriben el portapapeles por su cuenta (p. ej. una imagen via
 * `ClipboardItem`). En ambos casos el timer viejo se cancela si el usuario
 * vuelve a copiar, y el timeout se limpia al desmontar: el patron original
 * (useState + setTimeout por componente) dejaba timers vivos.
 */
export function useCopyFeedback(timeoutMs = 2000) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const flash = useCallback(() => {
    setCopied(true);
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setCopied(false);
    }, timeoutMs);
  }, [clearTimer, timeoutMs]);

  const copy = useCallback(
    async (text: string) => {
      const ok = await copyToClipboard(text);
      if (ok) flash();
      return ok;
    },
    [flash]
  );

  const reset = useCallback(() => {
    clearTimer();
    setCopied(false);
  }, [clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  return { copied, copy, flash, reset };
}
