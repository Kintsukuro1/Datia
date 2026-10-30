// @vitest-environment jsdom
// El selector de fuente del chat vive en ChatEngineProvider, montado una sola vez
// en ProtectedLayout. Si una mutacion de conectores no invalida esa lista, el
// selector sigue mostrando (y consultando) la fuente anterior hasta recargar.
import { describe, it, expect, vi } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

const spies = vi.hoisted(() => ({
  notify: vi.fn(),
  getThreads: vi.fn(async () => []),
  getSuggestions: vi.fn(async () => []),
  getConnectors: vi.fn(async (): Promise<any[]> => []),
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
    connectorService: {
      ...actual.connectorService,
      getConnectors: spies.getConnectors,
    },
  };
});

vi.mock('../features/chat/services/query_service', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../features/chat/services/query_service')>();
  return {
    ...actual,
    queryService: {
      ...actual.queryService,
      getThreads: spies.getThreads,
      getSuggestions: spies.getSuggestions,
    },
  };
});

import { CONNECTORS_CHANGED_EVENT } from '../features/admin/services/connector_service';
import type { CorporateConnection } from '../features/admin/services/connector_service';
import { ChatEngineProvider, useChatEngine } from '../features/chat/hooks/useChatEngine';

const conn = (id: number, name: string, isActive: boolean): CorporateConnection => ({
  id,
  name,
  db_type: 'postgresql',
  host: 'localhost',
  port: 5432,
  database_name: name.toLowerCase(),
  username: 'admin',
  is_active: isActive,
  created_at: '',
});

describe('selector de fuente del chat', () => {
  it('se actualiza y apunta a la nueva activa cuando avisa una mutacion de conectores', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    spies.getConnectors.mockResolvedValue([conn(1, 'Vieja', true)]);

    let engine: ReturnType<typeof useChatEngine> | null = null;
    const Probe = () => {
      engine = useChatEngine();
      return null;
    };

    await act(async () => {
      root.render(React.createElement(ChatEngineProvider, null, React.createElement(Probe)));
    });

    expect(engine!.connectors.map((c) => c.id)).toEqual([1]);
    expect(engine!.activeConnectionId).toBe(1);

    // El backend deja a la nueva como unica activa y la lista ya la incluye.
    spies.getConnectors.mockResolvedValue([conn(2, 'Nueva', true), conn(1, 'Vieja', false)]);

    await act(async () => {
      window.dispatchEvent(new CustomEvent(CONNECTORS_CHANGED_EVENT));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(engine!.connectors.map((c) => c.id)).toEqual([2, 1]);
    expect(engine!.activeConnectionId).toBe(2);
    expect(engine!.activeDatabaseName).toBe('Nueva (POSTGRESQL)');

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
