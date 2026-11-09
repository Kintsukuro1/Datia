import React from 'react';

interface Props {
  children: React.ReactNode;
}

interface State {
  errorId: string | null;
}

/**
 * Ultimo punto de recuperacion de la SPA.
 *
 * Sin esto, un error de render (por ejemplo un `detail` que llega como objeto y
 * React no puede pintar) desmonta el arbol entero y deja la pagina en negro
 * hasta recargar. El ID es lo unico que se muestra: el stack queda en consola
 * para quien da soporte, no en pantalla para el usuario.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { errorId: null };

  static getDerivedStateFromError(): State {
    return { errorId: `ui-${Date.now().toString(36)}` };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('[Datia] Error de render', this.state.errorId, error, info);
  }

  render(): React.ReactNode {
    if (!this.state.errorId) return this.props.children;

    return (
      <div
        role="alert"
        className="flex min-h-screen items-center justify-center bg-dark-base p-6 text-app-text"
      >
        <div className="max-w-md rounded-2xl border border-white/10 bg-white/5 p-6 text-center">
          <h1 className="mb-2 text-lg font-semibold">Algo falló en la interfaz</h1>
          <p className="mb-4 text-sm text-app-muted">
            La sesión y los datos no se modificaron. Recargá la página para
            volver a intentar.
          </p>
          <p className="mb-4 font-mono text-xs text-app-muted">
            ID de error: {this.state.errorId}
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            Recargar
          </button>
        </div>
      </div>
    );
  }
}
