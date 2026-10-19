// @vitest-environment jsdom
// Invariante de la Fase 5: la oferta de proyeccion llevada a UI. El boton
// navega a /projections con la pregunta en el state (el input de alla la
// precarga) y la X descarta sin navegar.
import { describe, it, expect, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { ProjectionSuggestionCard } from '../features/chat/components/ProjectionSuggestionCard';

const QUESTION = 'predice las ventas del próximo mes';

function ProjectionsProbe() {
  const location = useLocation();
  const question = (location.state as { question?: string } | null)?.question ?? 'sin-pregunta';
  return React.createElement('div', { id: 'projections-probe' }, `${location.pathname}::${question}`);
}

async function render(onDismiss: () => void) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      React.createElement(
        MemoryRouter,
        { initialEntries: ['/chat'] },
        React.createElement(
          Routes,
          null,
          React.createElement(Route, {
            path: '/chat',
            element: React.createElement(ProjectionSuggestionCard, { question: QUESTION, onDismiss }),
          }),
          React.createElement(Route, {
            path: '/projections',
            element: React.createElement(ProjectionsProbe),
          })
        )
      )
    );
  });
  return { container, root };
}

async function unmount(container: HTMLElement, root: ReturnType<typeof createRoot>) {
  await act(async () => {
    root.unmount();
  });
  container.remove();
}

describe('ProjectionSuggestionCard', () => {
  it('muestra la pregunta y el botón la abre en /projections con el state cargado', async () => {
    const onDismiss = vi.fn();
    const { container, root } = await render(onDismiss);

    // La pregunta ofrecida queda a la vista: se proyecta exactamente eso.
    expect(container.textContent).toContain(QUESTION);

    const openButton = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Abrir en Proyecciones')
    ) as HTMLButtonElement;
    expect(openButton).toBeTruthy();

    await act(async () => {
      openButton.click();
    });

    expect(container.querySelector('#projections-probe')?.textContent).toBe(`/projections::${QUESTION}`);
    expect(onDismiss).not.toHaveBeenCalled();

    await unmount(container, root);
  });

  it('la X descarta la oferta sin navegar', async () => {
    const onDismiss = vi.fn();
    const { container, root } = await render(onDismiss);

    const dismissButton = container.querySelector(
      'button[aria-label="Descartar la sugerencia"]'
    ) as HTMLButtonElement;
    expect(dismissButton).toBeTruthy();

    await act(async () => {
      dismissButton.click();
    });

    expect(onDismiss).toHaveBeenCalledTimes(1);
    // Sigue en /chat: descartar no navega.
    expect(container.querySelector('#projections-probe')).toBeNull();

    await unmount(container, root);
  });
});
