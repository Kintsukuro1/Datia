// @vitest-environment jsdom
// Invariante de la Fase 5: una pregunta de proyeccion NO viaja a ningun
// endpoint del chat. El motor la deja como oferta (`projectionSuggestion`) para
// abrir /projections con la pregunta cargada, y no toca la red.
import { describe, it, expect, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

const spies = vi.hoisted(() => ({
  notify: vi.fn(),
  getThreads: vi.fn(async () => []),
  getSuggestions: vi.fn(async () => []),
  sendQueryStreaming: vi.fn(),
  sendQuery: vi.fn(),
  getConnectors: vi.fn(async () => []),
}));

vi.mock('../features/auth/context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'tester', role_name: 'Administrador de Plataforma', is_admin: true },
    settings: {},
  }),
}));

vi.mock('../context/NotificationContext', () => ({
  useNotifications: () => ({ notify: spies.notify }),
}));

vi.mock('../features/admin/services/connector_service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../features/admin/services/connector_service')>();
  return {
    ...actual,
    connectorService: { getConnectors: spies.getConnectors },
  };
});

vi.mock('../features/chat/services/query_service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../features/chat/services/query_service')>();
  return {
    ...actual,
    queryService: {
      getThreads: spies.getThreads,
      getSuggestions: spies.getSuggestions,
      sendQueryStreaming: spies.sendQueryStreaming,
      sendQuery: spies.sendQuery,
    },
  };
});

import { ChatEngineProvider, useChatEngine } from '../features/chat/hooks/useChatEngine';

const QUESTION = 'predice el mes que viene';

describe('pregunta de proyeccion en el chat: oferta, no consulta', () => {
  it('deja la pregunta como oferta sin llamar a ningun endpoint, y "+ Nueva consulta" la descarta', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    let engine: ReturnType<typeof useChatEngine> | null = null;
    const Probe = () => {
      engine = useChatEngine();
      return null;
    };

    await act(async () => {
      root.render(React.createElement(ChatEngineProvider, null, React.createElement(Probe)));
    });

    await act(async () => {
      await engine!.handleSendPrompt(QUESTION);
    });

    // La oferta queda en pantalla, el input se vacia y nada simula estar en curso.
    expect(engine!.projectionSuggestion).toBe(QUESTION);
    expect(engine!.promptInput).toBe('');
    expect(engine!.isGenerating).toBe(false);
    // Ningun camino de red del chat se toco: ni streaming, ni el camino normal.
    expect(spies.sendQueryStreaming).not.toHaveBeenCalled();
    expect(spies.sendQuery).not.toHaveBeenCalled();

    // "Nueva consulta" descarta la oferta (no vive en ningun hilo).
    await act(async () => {
      engine!.handleNewThread();
    });
    expect(engine!.projectionSuggestion).toBeNull();

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
