// @vitest-environment jsdom
//
// La tabla de datos subyacente se navega con lector de pantalla, no solo con
// el mouse: nombre accesible, encabezados de columna y orden anunciado en el
// `<th>` (`aria-sort`), no en el botón como si fuera una acción suelta.
//
// Sin JSX a proposito (vite.config.ts solo globbea `src/**/*.test.ts`).
import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';

import { DataGridTable } from '../components/datagrid/DataGridTable';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('DataGridTable — accesibilidad de la tabla', () => {
  let container: HTMLDivElement;
  let root: Root;

  const columns = ['producto', 'monto'];
  const rows = [
    { id: 1, producto: 'Cemento', monto: 100 },
    { id: 2, producto: 'Áridos', monto: 250 },
  ];

  const renderTable = () => {
    act(() => {
      root.render(React.createElement(DataGridTable, { columns, rows }));
    });
  };

  const headerButton = (label: string) =>
    Array.from(container.querySelectorAll<HTMLButtonElement>('th button')).find(
      (button) => button.textContent?.trim() === label
    ) ?? null;

  const header = (label: string) =>
    Array.from(container.querySelectorAll('th')).find(
      (candidate) => candidate.textContent?.trim() === label
    ) ?? null;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('declara caption, scope y un contenedor con scroll enfocable', () => {
    renderTable();

    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table!.querySelector('caption')?.textContent?.trim()).toBeTruthy();

    const headers = Array.from(table!.querySelectorAll('th'));
    expect(headers).toHaveLength(columns.length);
    for (const header of headers) {
      expect(header.getAttribute('scope')).toBe('col');
    }

    const wrapper = table!.parentElement!;
    expect(wrapper.getAttribute('role')).toBe('region');
    expect(wrapper.getAttribute('tabindex')).toBe('0');
    expect(wrapper.getAttribute('aria-label')?.trim()).toBeTruthy();
  });

  it('anuncia el orden en el th de la columna ordenada, y lo alterna', () => {
    renderTable();

    // Sin orden activo no se anuncia ninguno: `none` no es lo mismo que "asc".
    expect(container.querySelectorAll('th[aria-sort]')).toHaveLength(0);

    act(() => {
      headerButton('monto')!.click();
    });
    expect(header('monto')!.getAttribute('aria-sort')).toBe('ascending');

    act(() => {
      headerButton('monto')!.click();
    });
    expect(header('monto')!.getAttribute('aria-sort')).toBe('descending');

    // El orden se mueve con la columna: la anterior deja de anunciarse.
    act(() => {
      headerButton('producto')!.click();
    });
    expect(header('monto')!.getAttribute('aria-sort')).toBeNull();
    expect(header('producto')!.getAttribute('aria-sort')).toBe('ascending');
  });
});
