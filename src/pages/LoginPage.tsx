import React, { useState } from 'react';
import { useAuth } from '../features/auth/context/AuthContext';
import {
  AlertCircle,
  ArrowRight,
  ChevronDown,
  Database,
  Eye,
  EyeOff,
  Headset,
  Loader2,
  Lock,
  Network,
  ShieldCheck,
  Sparkles,
  User,
  Users,
} from 'lucide-react';
import { CORPORATE_ROLES } from '../constants';
import logoDatiaDark from './Logo_datia_2.png';
import logoDatiaLight from './Logo_Datia_3.png';

// Credenciales de los perfiles demo. Viven aca porque el backend las siembra
// (DEMO_MODE=true) y el desplegable rellena el formulario con ellas. En builds
// no-demo (VITE_DEMO_MODE=false) el bloque entero — mapas incluidos — cae del
// bundle: una instalacion cliente no publica claves conocidas.
//
// El NOMBRE del rol sale de `CORPORATE_ROLES`, que es la unica lista del
// frontend. Escribir el nombre dos veces hacia que el perfil del desplegable y
// el badge del Header dijeran cosas distintas en cuanto uno de los dos se toca.
// El orden sigue el de la matriz de `init_db`, de mas a menos privilegios.
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE !== 'false';

const DEMO_PASSWORDS: Record<string, string> = {
  'Administrador de Plataforma': 'admin123',
  'Director Ejecutivo (C-Level)': 'director123',
  'Analista Financiero & Comercial': 'economista123',
  'Gerente de Talento & Operaciones': 'talento123',
  'Analista de Datos & BI': 'bi123',
  'Ingeniero de Infraestructura & TI': 'ti123',
  'Oficial de Cumplimiento & Seguridad': 'seguridad123',
  'Usuario Consultor': 'consultor123',
};

const DEMO_USERNAMES: Record<string, string> = {
  'Administrador de Plataforma': 'admin',
  'Director Ejecutivo (C-Level)': 'director',
  'Analista Financiero & Comercial': 'economista',
  'Gerente de Talento & Operaciones': 'talento',
  'Analista de Datos & BI': 'bi',
  'Ingeniero de Infraestructura & TI': 'ti',
  'Oficial de Cumplimiento & Seguridad': 'seguridad',
  'Usuario Consultor': 'consultor',
};

// Condicional a proposito: con DEMO_MODE constante en false, el minificador
// descarta la rama y con ella `DEMO_PASSWORDS`/`DEMO_USERNAMES`. Los mapas se
// referencian SOLO aca adentro; sacarlos de este ternario los devuelve al bundle.
const DEMO_PROFILES = DEMO_MODE
  ? CORPORATE_ROLES
      .filter((r) => DEMO_USERNAMES[r.name])
      .map((r) => ({
        role: r.name,
        username: DEMO_USERNAMES[r.name],
        password: DEMO_PASSWORDS[r.name],
      }))
  : [];

const PERFIL_INICIAL = DEMO_PROFILES[0]?.username ?? '';

export const LoginPage: React.FC = () => {
  const { login, error, clearError } = useAuth();

  // Form Fields
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  // Perfil elegido en el desplegable de la demo. Es estado propio, no el valor de
  // `username`: el desplegable elige una cuenta y el login manual escribe en el
  // campo. Si compartieran estado, teclear un usuario cerrando en el perfil
  // "Administrador" pondria las credenciales de otro sin que nadie lo pidiera.
  const [perfilDemo, setPerfilDemo] = useState<string>(PERFIL_INICIAL);
  // Un solo toggle para el campo: ya no hay confirmación que comparar.
  const [showPasswords, setShowPasswords] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    clearError();

    if (!username.trim() || !password) {
      setLocalError('Por favor completa todos los campos requeridos.');
      return;
    }

    setIsSubmitting(true);
    try {
      await login(username, password);
    } catch (err: any) {
      setLocalError(err.message || 'Credenciales inválidas. Verifica tu usuario y contraseña.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Elegir un perfil RELLENA el formulario en vez de entrar directo. El login
  // manual queda por encima y es el que manda; con autologin, cambiar el
  // desplegable se comia la sesion de alguien que solo estaba mirando el
  // catalogo de roles. Rellenar deja la accion a un click explicito y hace
  // visible que esas credenciales son de la demo y no un atajo.
  const handlePerfilChange = (username: string) => {
    setPerfilDemo(username);
    const p = DEMO_PROFILES.find((x) => x.username === username);
    if (!p) return;
    setUsername(p.username);
    setPassword(p.password);
    setLocalError(null);
    clearError();
  };
  const activeError = localError || error;

  return (
    <div className="h-[100dvh] overflow-y-auto bg-app-bg text-app-text">
      <div className="flex min-h-full items-center justify-center p-4">
        <div className="animate-fadeIn flex w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-app-border bg-app-surface shadow-2xl lg:min-h-[calc(100dvh-2rem)] lg:flex-row">
          {/* COLUMNA IZQUIERDA: marca, propuesta de valor y hechos verificables */}
          <div className="relative flex flex-col justify-between gap-10 overflow-hidden bg-gradient-to-br from-app-surface to-app-bg p-8 lg:w-[55%] lg:p-12">
            {/* Decoración: dos halos del color de marca + retícula de puntos.
                aria-hidden porque es puramente visual. */}
            <div aria-hidden="true" className="pointer-events-none absolute -left-32 -top-32 h-96 w-96 rounded-full bg-brand-600/10 blur-[100px]" />
            <div aria-hidden="true" className="pointer-events-none absolute -bottom-24 -right-16 h-[420px] w-[420px] rounded-full bg-brand-600/10 blur-[130px]" />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 opacity-40 [background-image:radial-gradient(rgba(0,110,230,0.15)_1px,transparent_1px)] [background-size:24px_24px]"
            />

            {/* Cabecera: logo real + wordmark (mismo que el Header) */}
            <div className="relative z-10 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-lg border border-app-border bg-app-card shadow-md">
                <img src={logoDatiaLight} alt="Logo Dat.ia" className="block h-full w-full object-contain p-1 dark:hidden" />
                <img src={logoDatiaDark} alt="Logo Dat.ia" className="hidden h-full w-full object-contain p-1 dark:block" />
              </div>
              <span className="text-xl font-bold tracking-tight">DATIA</span>
            </div>

            {/* Contenido central */}
            <div className="relative z-10 max-w-xl space-y-4">
              <div className="inline-flex items-center gap-2 rounded-md border border-app-border bg-app-card px-3 py-1 text-xs text-app-muted">
                <Database className="h-4 w-4 text-brand-400" />
                <span>Plataforma Unificada de Inteligencia Semántica</span>
              </div>

              <h1 className="text-3xl font-bold tracking-tight lg:text-4xl">Democratización de Datos</h1>
              <p className="max-w-md text-sm leading-relaxed text-app-muted lg:text-base">
                Consultá tus bases corporativas en lenguaje natural, con gobernanza, control de acceso y trazabilidad de extremo a extremo.
              </p>

              <div className="flex flex-col gap-5 pt-2">
                <div className="flex items-start gap-3.5">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600/15 text-brand-400">
                    <Sparkles className="h-4 w-4" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold">Asistente de IA sobre tus datos</h2>
                    <p className="mt-0.5 text-xs leading-relaxed text-app-muted">
                      Generación de consultas SQL, análisis instantáneo y respuestas sintetizadas en segundos.
                    </p>
                  </div>
                </div>

                <div className="flex items-start gap-3.5">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600/15 text-brand-400">
                    <Network className="h-4 w-4" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold">Catálogo semántico y diccionario de datos</h2>
                    <p className="mt-0.5 text-xs leading-relaxed text-app-muted">
                      Terminología de negocio mapeada sobre esquemas relacionales complejos.
                    </p>
                  </div>
                </div>

                <div className="flex items-start gap-3.5">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600/15 text-brand-400">
                    <ShieldCheck className="h-4 w-4" />
                  </div>
                  <div>
                    <h2 className="text-sm font-semibold">Control de acceso por roles (RBAC)</h2>
                    <p className="mt-0.5 text-xs leading-relaxed text-app-muted">
                      Permisos de tablas y columnas y bitácora de auditoría continua.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Pie del panel: solo hechos verificables en el código.
                bcrypt: backend/app/core/security.py. Sesiones: tabla UserSession.
                (El "Argon2/bcrypt" anterior era impreciso: es bcrypt directo.) */}
            <div className="relative z-10 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-app-muted">
              <span className="flex items-center gap-1.5">
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
                Sesiones auditadas
              </span>
              <span className="flex items-center gap-1.5">
                <Lock className="h-4 w-4 text-brand-400" />
                Hashing bcrypt de contraseñas
              </span>
            </div>
          </div>

          {/* COLUMNA DERECHA: acceso */}
          <div className="flex flex-col items-center justify-center gap-5 bg-app-bg p-6 lg:w-[45%] lg:p-12">
            <div className="glass-panel w-full max-w-[420px] space-y-5 rounded-2xl border border-app-border p-6 shadow-xl lg:p-8">
              {/* Encabezado de la tarjeta */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <h2 className="text-xl font-bold tracking-tight">Iniciar sesión</h2>
                  <ShieldCheck className="h-5 w-5 text-brand-400" />
                </div>
                <p className="text-xs text-app-muted">
                  Ingresá tus credenciales corporativas autorizadas.
                </p>
              </div>

              {/* Alert Message */}
              {activeError && (
                <div className="animate-fadeIn flex items-start gap-2.5 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3.5 text-xs text-rose-400">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{activeError}</span>
                </div>
              )}

              {/* Form */}
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="space-y-1.5">
                  <label htmlFor="login-username-input" className="block text-xs font-medium text-app-muted">Usuario Corporativo</label>
                  <div className="relative">
                    <input
                      id="login-username-input"
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="ej. economista"
                      required
                      className="h-11 w-full rounded-xl border border-app-border bg-app-bg pl-10 pr-3 text-sm text-app-text placeholder-gray-500 transition-colors focus:border-brand-500 focus:outline-none"
                    />
                    <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="login-password-input" className="block text-xs font-medium text-app-muted">Contraseña</label>
                  <div className="relative">
                    <input
                      id="login-password-input"
                      name="password"
                      type={showPasswords ? 'text' : 'password'}
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                      className="h-11 w-full rounded-xl border border-app-border bg-app-bg pl-10 pr-11 text-sm text-app-text placeholder-gray-500 transition-colors focus:border-brand-500 focus:outline-none"
                    />
                    <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                    <button
                      type="button"
                      onClick={() => setShowPasswords((v) => !v)}
                      aria-label={showPasswords ? 'Ocultar contraseña' : 'Mostrar contraseña'}
                      className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-app-muted transition-colors hover:bg-app-hover hover:text-app-text"
                    >
                      {showPasswords ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="mt-1 flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-brand-600 text-sm font-semibold text-white shadow-md shadow-brand-600/25 transition-all hover:bg-brand-500 active:scale-[0.99] disabled:opacity-60"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>Ingresando…</span>
                    </>
                  ) : (
                    <>
                      <span>Ingresar</span>
                      <ArrowRight className="h-4 w-4" />
                    </>
                  )}
                </button>
              </form>

              {/* Selector de perfil de la demo: un desplegable y no una fila de
                  botones. Los 8 perfiles no entran en tres botones sin que las
                  etiquetas se corten, y un desplegable deja leer el nombre completo
                  del rol, que es justo lo que hay que comparar entre perfiles.
                  Gateado por DEMO_MODE: en un build no-demo el bloque no se
                  renderiza (y no llega al bundle). */}
              {DEMO_MODE && (
                <div className="border-t border-app-border/60 pt-4">
                  <label
                    htmlFor="login-demo-perfil"
                    className="mb-2 block text-[11px] font-medium text-app-muted"
                  >
                    O entrá con un perfil de la demo
                  </label>
                  <div className="relative">
                    <select
                      id="login-demo-perfil"
                      value={perfilDemo}
                      onChange={(e) => handlePerfilChange(e.target.value)}
                      className="h-10 w-full cursor-pointer appearance-none rounded-xl border border-app-border bg-app-bg/50 pl-9 pr-9 text-xs text-app-text transition-colors hover:border-brand-500/40 focus:border-brand-500 focus:outline-none"
                    >
                      {DEMO_PROFILES.map((p) => (
                        <option key={p.username} value={p.username} className="bg-app-card text-app-text">
                          {p.role} — {p.username}
                        </option>
                      ))}
                    </select>
                    <Users className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-app-muted" />
                  </div>
                  <p className="mt-1.5 text-center text-[10px] text-app-muted/80">
                    Rellena el formulario. El acceso ocurre al pulsar «Ingresar».
                  </p>
                </div>
              )}

              {/* Pie de la tarjeta: el reset de claves es acción del administrador
                  (no hay autoservicio), así que se informa como texto, no como un
                  link a una pantalla que no existe. */}
              <div className="flex items-center justify-center gap-2 rounded-lg border border-app-border bg-app-bg/50 px-3 py-2.5 text-xs text-app-muted">
                <Headset className="h-4 w-4 shrink-0" />
                <span>¿Problemas para acceder? Contactá al administrador.</span>
              </div>
            </div>

            {/* Nota legal: la auditoría de sesiones es real (UserSession + módulo
                de auditoría), a diferencia del "SOC2 Tipo II" del mock. */}
            <p className="max-w-sm text-center text-[11px] leading-relaxed text-app-muted/90">
              Acceso restringido a personal autorizado. Las sesiones quedan registradas en la bitácora de auditoría.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
