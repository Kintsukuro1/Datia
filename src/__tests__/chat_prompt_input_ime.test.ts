// @vitest-environment jsdom
//
// El textarea del chat es el otro punto donde un Enter puede ser el que
// confirma una composición IME (chino, japonés, coreano). Si se envía ahí, la
// pregunta sale a medio escribir y se pierde la composición.
//
// Sin JSX a proposito (vite.config.ts solo globbea `src/**/*.test.ts`).
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';

import { ChatPromptInput } from '../components/chat/ChatPromptInput';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('ChatPromptInput — Enter y composición IME', () => {
  let container: HTMLDivElement;
  let root: Root;

  const renderInput = (onSubmit: (text?: string) => void) => {
    act(() => {
      root.render(
        React.createElement(ChatPromptInput, {
          promptInput: 'ventas por mes',
          setPromptInput: () => {},
          isGenerating: false,
          onSubmit,
        })
      );
    });
  };

  const textarea = () => container.querySelector<HTMLTextAreaElement>('textarea')!;

  const pressEnter = async (event: KeyboardEvent) => {
    await act(async () => {
      textarea().dispatchEvent(event);
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('un Enter con isComposing no envía la consulta', async () => {
    const onSubmit = vi.fn();
    renderInput(onSubmit);

    await pressEnter(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        bubbles: true,
        cancelable: true,
        isComposing: true,
      })
    );

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('un Enter con keyCode 229 no envía la consulta', async () => {
    const onSubmit = vi.fn();
    renderInput(onSubmit);

    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(event, 'keyCode', { value: 229 });
    await pressEnter(event);

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('un Enter normal envía el texto escrito', async () => {
    const onSubmit = vi.fn();
    renderInput(onSubmit);

    await pressEnter(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    );

    expect(onSubmit).toHaveBeenCalledWith('ventas por mes');
  });

  it('Shift+Enter no envía: es un salto de línea', async () => {
    const onSubmit = vi.fn();
    renderInput(onSubmit);

    await pressEnter(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        shiftKey: true,
        bubbles: true,
        cancelable: true,
      })
    );

    expect(onSubmit).not.toHaveBeenCalled();
  });
});
