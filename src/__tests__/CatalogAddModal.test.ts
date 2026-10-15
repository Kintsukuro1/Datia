// @vitest-environment jsdom
// El modal "Añadir Regla" (Admin -> Catálogo) crasheaba al abrirse: el useState
// de isSaving vivía por debajo del `if (!isOpen) return null`, así que el render
// con isOpen=false montaba 5 hooks y el siguiente con isOpen=true montaba 6, y
// React aborta con "Rendered more hooks than during the previous render". Este
// test hace exactamente esa transición (cerrado -> abierto), que es la que
// rompía: lo que se cuida es el orden de los hooks.
//
// Sin JSX a propósito: vite.config.ts solo globbea `src/**/*.test.ts`, y el test
// tiene que correr en la suite. Con createElement se evita depender de eso.
import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { CatalogAddModal } from '../components/admin/CatalogAddModal';

// Sin esto React avisa de que el entorno no está configurado para act().
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('CatalogAddModal: abrir el modal no rompe el orden de los hooks', () => {
  let container: HTMLDivElement;
  let root: Root;

  const props = { onClose: () => {}, onAdd: () => true };
  const modal = (isOpen: boolean) => React.createElement(CatalogAddModal, { isOpen, ...props });

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('pasar de isOpen=false a true renderiza el dialogo en vez de lanzar', () => {
    act(() => {
      root.render(modal(false));
    });
    expect(container.querySelector('[role="dialog"]')).toBeNull();

    // Con isSaving debajo del early return, este render es el que lanzaba.
    act(() => {
      root.render(modal(true));
    });
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('montar directo con isOpen=true renderiza el dialogo', () => {
    act(() => {
      root.render(modal(true));
    });
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });
});
