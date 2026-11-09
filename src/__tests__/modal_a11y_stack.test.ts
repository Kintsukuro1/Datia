// @vitest-environment jsdom
import React, { useState, act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import { useModalA11y } from '../hooks/useModalA11y';

const Modal = ({
  open,
  onClose,
  label,
}: {
  open: boolean;
  onClose: () => void;
  label: string;
}) => {
  const ref = useModalA11y<HTMLDivElement>(open, onClose);
  if (!open) return null;
  return React.createElement(
    'div',
    { ref, 'data-testid': label, tabIndex: -1 },
    React.createElement('button', { type: 'button' }, 'ok'),
  );
};

describe('useModalA11y: stack de modales', () => {
  it('Escape cierra solo el tope y restaura el scroll al cerrar el último', async () => {
    document.body.style.overflow = 'auto';
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    const Harness = () => {
      const [firstOpen, setFirstOpen] = useState(true);
      const [secondOpen, setSecondOpen] = useState(true);
      return React.createElement(
        React.Fragment,
        null,
        React.createElement(Modal, {
          open: firstOpen,
          onClose: () => setFirstOpen(false),
          label: 'first',
        }),
        React.createElement(Modal, {
          open: secondOpen,
          onClose: () => setSecondOpen(false),
          label: 'second',
        }),
      );
    };

    await act(async () => {
      root.render(React.createElement(Harness));
    });

    expect(document.querySelector('[data-testid="first"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="second"]')).not.toBeNull();
    expect(document.body.style.overflow).toBe('hidden');

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });

    expect(document.querySelector('[data-testid="second"]')).toBeNull();
    expect(document.querySelector('[data-testid="first"]')).not.toBeNull();
    expect(document.body.style.overflow).toBe('hidden');

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });

    expect(document.querySelector('[data-testid="first"]')).toBeNull();
    expect(document.body.style.overflow).toBe('auto');

    await act(async () => {
      root.unmount();
    });
    container.remove();
    document.body.style.overflow = '';
  });
});
