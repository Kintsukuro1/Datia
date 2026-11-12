// @vitest-environment jsdom
//
// El login es la primera pantalla y la que más se completa con gestores de
// contraseñas: si el campo no se declara como `username`, el autofill no lo
// reconoce y el usuario teclea credenciales a mano. El error de validación,
// además, tiene que anunciarse (`role="alert"`), no solo pintarse en rojo.
//
// Sin JSX a proposito (vite.config.ts solo globbea `src/**/*.test.ts`).
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';

const { auth } = vi.hoisted(() => ({
  auth: {
    login: vi.fn(),
    error: null as string | null,
    clearError: vi.fn(),
  },
}));

vi.mock('../features/auth/context/AuthContext', () => ({
  useAuth: () => auth,
}));

import { LoginPage } from '../pages/LoginPage';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

describe('LoginPage — autofill y errores anunciados', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    auth.login.mockReset();
    auth.clearError.mockReset();
    auth.error = null;
    auth.login.mockResolvedValue(undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const renderPage = () => {
    act(() => {
      root.render(React.createElement(LoginPage));
    });
  };

  it('declara los campos de acceso para que el gestor de contraseñas los rellene', () => {
    renderPage();

    const username = container.querySelector<HTMLInputElement>('#login-username-input');
    expect(username).not.toBeNull();
    expect(username!.getAttribute('name')).toBe('username');
    expect(username!.getAttribute('autocomplete')).toBe('username');
    // El usuario no es una frase: sin mayúscula automática ni corrección.
    expect(username!.getAttribute('spellcheck')).toBe('false');
    expect(username!.getAttribute('autocapitalize')).toBe('none');
    expect(username!.getAttribute('autocorrect')).toBe('off');
    expect(username!.getAttribute('enterkeyhint')).toBe('next');
    expect(username!.required).toBe(true);

    const password = container.querySelector<HTMLInputElement>('#login-password-input');
    expect(password).not.toBeNull();
    expect(password!.getAttribute('name')).toBe('password');
    expect(password!.getAttribute('autocomplete')).toBe('current-password');
    expect(password!.getAttribute('enterkeyhint')).toBe('done');
    expect(password!.required).toBe(true);
  });

  it('el error de validación se anuncia como alert y no intenta iniciar sesión', async () => {
    renderPage();

    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 0));
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toContain('completa todos los campos requeridos');
    // La validación es local: no se manda una petición con campos vacíos.
    expect(auth.login).not.toHaveBeenCalled();
  });
});
