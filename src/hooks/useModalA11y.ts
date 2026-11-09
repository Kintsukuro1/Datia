import { useEffect, useRef } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

type ModalEntry = {
  node: HTMLElement;
  close: () => void;
};

// Un solo stack para todos los modales montados. Antes cada hook guardaba su
// propio `previousOverflow` y su propio listener de Escape: con dos modales
// abiertos, cerrar el de abajo restauraba el scroll con el de arriba vivo, y
// cerrar ambos con Escape dejaba `body.overflow = hidden` para siempre.
const modalStack: ModalEntry[] = [];
let savedBodyOverflow: string | null = null;
let globalKeyDown: ((event: KeyboardEvent) => void) | null = null;

function installGlobalModalHandling(): void {
  if (globalKeyDown) return;
  savedBodyOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  globalKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    const top = modalStack[modalStack.length - 1];
    if (!top) return;
    // `stopImmediatePropagation` y no `stopPropagation`: otros listeners del
    // mismo nodo (por ejemplo, el handler de presentación del dashboard) no
    // deben ejecutarse detrás del modal.
    event.preventDefault();
    event.stopImmediatePropagation();
    top.close();
  };
  document.addEventListener('keydown', globalKeyDown, true);
}

function uninstallGlobalModalHandling(): void {
  if (modalStack.length > 0) return;
  if (globalKeyDown) {
    document.removeEventListener('keydown', globalKeyDown, true);
    globalKeyDown = null;
  }
  if (savedBodyOverflow !== null) {
    document.body.style.overflow = savedBodyOverflow;
    savedBodyOverflow = null;
  }
}

/**
 * Dialog semantics + keyboard behaviour for an overlay that already exists.
 *
 * Fifteen overlays shipped with no role="dialog", no aria-modal, no Escape
 * handler and no focus containment: a keyboard user could not dismiss any of
 * them, focus walked the page behind the scrim, and a screen reader announced
 * nothing. Two of them also closed on a click that a touch-only user can never
 * perform.
 *
 * A <Modal> shell would have meant rewriting 15 headers, 15 footers and their
 * sticky/scroll behaviour. This fixes the actual defects in three lines per
 * call site and changes no layout.
 *
 * Usage:
 *   const ref = useModalA11y<HTMLDivElement>(isOpen, onClose);
 *   <div ref={ref} role="dialog" aria-modal="true" aria-label="..." tabIndex={-1}>
 */
export function useModalA11y<T extends HTMLElement = HTMLDivElement>(
  isOpen: boolean,
  onClose?: () => void,
) {
  const ref = useRef<T>(null);

  // Held in a ref so the effect does not depend on the callback identity.
  // Most call sites pass an inline arrow, which changes on every parent
  // render; with onClose in the deps the effect would re-run and yank focus
  // back to the first field mid-interaction.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!isOpen) return;
    const node = ref.current;
    if (!node) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const entry: ModalEntry = {
      node,
      close: () => closeRef.current?.(),
    };
    modalStack.push(entry);
    installGlobalModalHandling();

    const focusables = () =>
      Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement
      );

    // Land focus inside the dialog. Without this, Tab continues from wherever
    // the opener was, which is behind the scrim.
    const initial = focusables()[0];
    (initial ?? node).focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      // Solo el modal del tope atrapa el foco. Si dos están abiertos, el de
      // abajo no debe competir por el Tab.
      if (modalStack[modalStack.length - 1] !== entry) return;

      const list = focusables();
      if (list.length === 0) {
        e.preventDefault();
        node.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;

      if (e.shiftKey && (active === first || active === node)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || active === node)) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      const index = modalStack.indexOf(entry);
      if (index !== -1) modalStack.splice(index, 1);
      uninstallGlobalModalHandling();

      const nextTop = modalStack[modalStack.length - 1];
      if (nextTop) {
        nextTop.node.focus?.();
      } else {
        previouslyFocused?.focus?.();
      }
    };
  }, [isOpen]);

  return ref;
}
