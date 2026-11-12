import React, { Suspense, lazy, useEffect } from 'react';
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../features/auth/context/AuthContext';
import { hasPendingPasswordChange } from '../../features/auth/temp_password';
import { isPlatformAdmin } from '../../constants';
import { Header } from '../../shared/layout/Header';
import { ToastContainer } from '../../components/shared/ToastContainer';
import { MandatoryPasswordChangeModal } from '../../components/auth/MandatoryPasswordChangeModal';
import { TemporaryPasswordBanner } from '../../components/auth/TemporaryPasswordBanner';
import { LoginPage } from '../../pages/LoginPage';
import { ChatEngineProvider } from '../../features/chat/hooks/useChatEngine';

// Antes las cuatro paginas eran imports estaticos: entrar a /login descargaba
// Admin, Settings y todo ECharts (~1 MB) sin que el usuario los fuera a pedir.
// `lazy` los parte en chunks por ruta; el fallback es el mismo spinner que
// usa el guard de sesion, asi que no hay un estado visual nuevo que mantener.
const ChatDashboardPage = lazy(() => import('../../pages/ChatDashboardPage').then((m) => ({ default: m.ChatDashboardPage })));
const SettingsPage = lazy(() => import('../../pages/SettingsPage').then((m) => ({ default: m.SettingsPage })));
const AdminPage = lazy(() => import('../../pages/AdminPage').then((m) => ({ default: m.AdminPage })));
const ProjectionsPage = lazy(() => import('../../pages/ProjectionsPage').then((m) => ({ default: m.ProjectionsPage })));

const RouteFallback: React.FC = () => (
  <div className="flex items-center justify-center h-full min-h-[50vh] text-[var(--app-text)]">
    <div className="flex flex-col items-center gap-3">
      <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      <span className="text-sm">Cargando pantalla...</span>
    </div>
  </div>
);

// Sin esto todas las pantallas comparten el titulo "Dat.ia" en la pestana, el
// historial y el nombre accesible de la ventana: no se distingue una de otra.
const ROUTE_TITLES: Record<string, string> = {
  '/': 'Consultar',
  '/chat': 'Consultar',
  '/projections': 'Proyecciones',
  '/admin': 'Administración',
  '/settings': 'Opciones',
  '/login': 'Iniciar sesión',
  '/change-password': 'Cambiar contraseña',
};

const RouteTitle: React.FC = () => {
  const { pathname } = useLocation();

  useEffect(() => {
    const label = ROUTE_TITLES[pathname];
    document.title = label ? `${label} — Dat.ia` : 'Dat.ia';
  }, [pathname]);

  return null;
};

const ProtectedLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
          <span className="text-sm">Cargando sesión...</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Con clave temporal pendiente, /change-password es la UNICA ruta valida.
  //
  // Antes el modal era un overlay `z-[100]` sobre el dashboard: el usuario no
  // podia interactuar, pero el dashboard ya estaba montado y disparaba sus
  // requests, que morian todos en 403 (`deps.py` bloquea toda ruta salvo
  // /auth/me y /auth/change-password). El primer mensaje que veia era una tanda
  // de errores, no "cambiá tu clave".
  //
  // Un `Navigate` en el guard lo resuelve una vez para todas las pantallas, en
  // vez de un condicional en cada una. El logout sigue disponible: lo tiene el
  // propio modal ("Salir de mi cuenta"), que en esta ruta es la pantalla
  // completa, asi que el overlay ya no tapa nada.
  if (hasPendingPasswordChange(user)) {
    return <Navigate to="/change-password" replace />;
  }

  return (
    <div className="flex flex-col h-screen bg-[var(--app-bg)] text-[var(--app-text)] overflow-hidden font-sans">
      {/* Skip link: con HashRouter un `href="#main-content"` haria que el router
          interprete "main-content" como ruta, asi que el foco se mueve a mano. */}
      <a
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
      >
        Saltar al contenido
      </a>
      <Header />
      <TemporaryPasswordBanner />
      <main id="main-content" tabIndex={-1} className="flex-1 overflow-hidden relative flex flex-col min-h-0">
        <ChatEngineProvider>{children}</ChatEngineProvider>
      </main>
      <ToastContainer />
    </div>
  );
};

/**
 * Destino unico mientras hay clave temporal pendiente.
 *
 * Sin Header ni navegacion: el unico contenido posible es el formulario de
 * cambio. El boton de salir va dentro del modal, asi que el usuario siempre
 * tiene salida de una sesion que no puede usar para nada mas.
 */
const MandatoryPasswordChangeRoute: React.FC = () => {
  const { user, isLoading, completePasswordChange, logout } = useAuth();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // Ya no hay clave pendiente (el backend la limpio): la pantalla era temporal.
  if (!hasPendingPasswordChange(user)) {
    return <Navigate to="/chat" replace />;
  }

  return (
    <div className="min-h-[100dvh] bg-[var(--app-bg)] text-[var(--app-text)]">
      <MandatoryPasswordChangeModal
        isOpen={true}
        onSuccess={completePasswordChange}
        onCancel={logout}
      />
      <ToastContainer />
    </div>
  );
};

export const AppRouter: React.FC = () => {
  const { user } = useAuth();

  return (
    <HashRouter>
      <RouteTitle />
      <Routes>
        <Route path="/change-password" element={<MandatoryPasswordChangeRoute />} />
        <Route
          path="/login"
          element={user ? (
            // Con clave pendiente el login no es la pantalla: mandar al chat
            // seria mandar a ProtectedLayout, que redirige igual, pero con un
            // salto visible.
            <Navigate to={hasPendingPasswordChange(user) ? '/change-password' : '/chat'} replace />
          ) : (
            <>
              <LoginPage />
              <ToastContainer />
            </>
          )}
        />
        <Route
          path="/chat"
          element={
            <ProtectedLayout>
              <Suspense fallback={<RouteFallback />}>
                <ChatDashboardPage />
              </Suspense>
            </ProtectedLayout>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedLayout>
              {user && isPlatformAdmin(user) ? (
                <Suspense fallback={<RouteFallback />}>
                  <SettingsPage />
                </Suspense>
              ) : (
                <Navigate to="/chat" replace />
              )}
            </ProtectedLayout>
          }
        />
        <Route
          path="/admin"
          element={
            <ProtectedLayout>
              {user && isPlatformAdmin(user) ? (
                <Suspense fallback={<RouteFallback />}>
                  <AdminPage />
                </Suspense>
              ) : (
                <Navigate to="/chat" replace />
              )}
            </ProtectedLayout>
          }
        />
        <Route
          path="/projections"
          element={
            <ProtectedLayout>
              <Suspense fallback={<RouteFallback />}>
                <ProjectionsPage />
              </Suspense>
            </ProtectedLayout>
          }
        />
        <Route
          path="*"
          element={
            <Navigate
              to={
                !user
                  ? "/login"
                  : hasPendingPasswordChange(user)
                    ? "/change-password"
                    : "/chat"
              }
              replace
            />
          }
        />
      </Routes>
    </HashRouter>
  );
};
