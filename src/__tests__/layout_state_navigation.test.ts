// @vitest-environment jsdom
// Invariante de la que depende ChatEngineProvider montado en ProtectedLayout:
// reproduce la estructura de rutas de AppRouter (cada ruta envuelve su propio
// layout) y verifica que el estado del layout sobrevive a la navegacion.
import { describe, it, expect } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';

const h = React.createElement;

let layoutMounts = 0;

const Layout = ({ children }: { children: React.ReactNode }) => {
  const [count, setCount] = React.useState(0);
  React.useEffect(() => {
    layoutMounts += 1;
  }, []);
  return h(
    'div',
    null,
    h('button', { id: 'inc', onClick: () => setCount((c: number) => c + 1) }, '+'),
    h('span', { id: 'count' }, String(count)),
    children
  );
};

const PageA = () => h('div', { id: 'page-a' }, 'A');
const PageB = () => h('div', { id: 'page-b' }, 'B');

const App = () =>
  h(
    HashRouter,
    null,
    h(
      Routes,
      null,
      h(Route, { path: '/a', element: h(Layout, null, h(PageA)) }),
      h(Route, { path: '/b', element: h(Layout, null, h(PageB)) }),
      h(Route, { path: '*', element: h(Navigate, { to: '/a', replace: true }) })
    )
  );

describe('estado del layout a traves de la navegacion', () => {
  it('no remonta el layout al cambiar de ruta', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    window.location.hash = '#/a';
    await act(async () => {
      root.render(h(App));
    });

    const inc = document.getElementById('inc') as HTMLButtonElement;
    await act(async () => {
      inc.click();
      inc.click();
    });
    expect(document.getElementById('count')!.textContent).toBe('2');
    expect(layoutMounts).toBe(1);

    await act(async () => {
      window.location.hash = '#/b';
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(document.getElementById('page-b')).toBeTruthy();
    expect(document.getElementById('count')!.textContent).toBe('2');
    expect(layoutMounts).toBe(1);

    await act(async () => {
      window.location.hash = '#/a';
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(document.getElementById('count')!.textContent).toBe('2');
    expect(layoutMounts).toBe(1);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
