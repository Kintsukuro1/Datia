# Plan 009 — Mejoras web modernas (auditoría `modern-web-guidance`)

**Fecha:** 11/11/2026
**Estado:** ✅ Ejecutado y verificado (11/11/2026)
**Alcance:** frontend (`src/`, `index.html`). No toca backend, SQL, RBAC, masking ni auditoría.

## Objetivo

Aplicar las mejoras de alto valor detectadas con `modern-web-guidance` sin reescribir la
arquitectura: entrada segura para IME, login autocompletable y anunciado, tema nativo
coherente, scrollbars estándar, rendimiento del chat (ECharts fuera del camino inicial),
tablas accesibles y verificación del lote.

## Reglas para los subagentes

1. **Cada subagente edita solo los archivos de su frente.** Si necesita tocar otro, lo
   informa y no lo hace.
2. **No editar `src/__tests__/`** salvo el subagente 5 (verificación). Los frentes 1–4
   pueden ejecutar tests existentes, pero no agregar ni modificar archivos de prueba.
3. **No commitear, no push, no tocar `plans/`** (el plan es de solo lectura para ellos).
4. **No revertir cambios existentes** del árbol de trabajo. Leer el archivo actual antes
   de editar y preservar el trabajo previo.
5. **No correr `npm run build` ni `npm run test:e2e`** en los frentes 1–4: escriben
   `dist/` y compiten por puertos. El frente 5 corre la verificación completa.
6. Verificación mínima de cada frente: `npx tsc --noEmit` y los tests existentes que
   correspondan. Reportar comandos y salida real.

## Frentes

### Frente 1 — Entrada segura (IME)

**Archivos:** `src/components/chat/ChatPromptInput.tsx`, `src/pages/ProjectionsPage.tsx`.

**Tareas:**
- En ambos `onKeyDown`, después de `preventDefault()` y antes de enviar, cortar si
  `e.nativeEvent.isComposing` o `e.nativeEvent.keyCode === 229`.
- Mantener el comportamiento actual: Enter envía, Shift+Enter inserta salto de línea.
- No cambiar el auto-resize del textarea en este frente.

**Criterio de aceptación:** un `keydown` con `isComposing: true` no dispara el envío; un
`keydown` normal sigue disparándolo. `tsc` limpio.

### Frente 2 — Login, tema y documento

**Archivos:** `src/pages/LoginPage.tsx`,
`src/components/auth/MandatoryPasswordChangeModal.tsx`, `index.html`,
`src/styles/globals.css`, `src/shared/layout/Header.tsx`,
`src/app/router/AppRouter.tsx`.

**Tareas:**
- Login: agregar `name="username"`, `autoComplete="username"`, `spellCheck={false}`,
  `autoCapitalize="none"`, `autoCorrect="off"`, `enterKeyHint="next"` al usuario;
  `enterKeyHint="done"` a la contraseña. Mantener `current-password` y el toggle de
  mostrar/ocultar.
- Errores: `role="alert"` en el bloque de error de `LoginPage` y en el de
  `MandatoryPasswordChangeModal`.
- `index.html`: `<meta name="color-scheme" content="light dark">` y
  `<link rel="preload" href="/fonts/plus-jakarta-sans-latin.woff2" as="font"
  type="font/woff2" crossorigin>`.
- `globals.css`: `color-scheme` en `:root`/`html.light`/`html.dark`; scrollbars estándar
  (`scrollbar-width: thin`, `scrollbar-color` con variables) y mover los `::-webkit-*`
  dentro de `@supports not (scrollbar-color: auto)`.
- Tema: si no hay `datia-theme` en `localStorage`, usar
  `matchMedia('(prefers-color-scheme: dark)')`; mantener el script inline de
  `index.html` sincronizado con la misma regla.
- `AppRouter`: actualizar `document.title` por ruta (`Consultar`, `Proyecciones`,
  `Administración`, `Opciones`, `Iniciar sesión`, `Cambiar contraseña`). Agregar un
  skip link "Saltar al contenido" hacia `<main id="main-content" tabIndex={-1}>`.

**Criterio de aceptación:** `tsc` limpio; el login conserva el flujo actual; el tema
claro/oscuro sigue funcionando; las scrollbars se ven en Firefox y Chromium.

### Frente 3 — Rendimiento y accesibilidad del chat

**Archivos:** `src/pages/ChatDashboardPage.tsx`,
`src/components/chat/ChatMessageItem.tsx`,
`src/components/dashboard/ExecutiveDashboardView.tsx`,
`src/components/chat/SidebarChatHistory.tsx`,
`src/components/chat/ChatEmptyState.tsx`, y un archivo nuevo
`src/components/dashboard/WidgetsModal.tsx`.

**Tareas:**
- Sacar ECharts del camino inicial del chat:
  - Extraer el modal "Tablero Ejecutivo" a `WidgetsModal.tsx` y cargarlo con
    `React.lazy` + `Suspense` solo cuando `isWidgetsOpen`.
  - Cargar `ExecutiveDashboardView` con `React.lazy` dentro de `ChatMessageItem`, o
    `ChartSection` con `React.lazy` dentro de `ExecutiveDashboardView`, para que
    ECharts no se descargue hasta que haya un dashboard visible.
- Estado del chat: agregar un `role="status"` visualmente oculto que anuncie "Consulta en
  curso" una vez; `role="alert"` en el párrafo de `pendingError`. No poner `aria-live`
  sobre el contador de segundos ni sobre la narrativa token a token.
- Targets: subir a mínimo 24×24 el botón de cerrar alertas
  (`ChatDashboardPage`), el de eliminar conversación (`SidebarChatHistory`) y el de
  editar pregunta (`ChatMessageItem`). Agregar `aria-label` a los botones icon-only que
  hoy solo tienen `title` (abrir historial, refrescar fuentes si cae en este frente).
- Quitar las clases muertas `scrollbar-thin` / `scrollbar-thumb-slate-800` de los
  archivos de este frente (el CSS estándar lo define el frente 2).
- Imágenes de `ChatEmptyState`: agregar `width`/`height` y `decoding="async"`; no
  convertir los PNG en este frente.

**Criterio de aceptación:** el chunk de ECharts no se carga al entrar a `/chat` sin
abrir un dashboard; `tsc` limpio; el modal de widgets y los dashboards siguen
funcionando; los tests existentes de chat/proyecciones pasan.

### Frente 4 — Tablas accesibles

**Archivos:** `src/components/datagrid/DataGridTable.tsx`,
`src/components/admin/AdminConnectorsTab.tsx`,
`src/components/admin/AdminUsersTab.tsx`,
`src/components/admin/AdminAuditTab.tsx`,
`src/components/admin/AdminLearnedTab.tsx`,
`src/components/admin/catalog/DataDictionarySection.tsx`,
`src/components/admin/catalog/SemanticCatalogSection.tsx`,
`src/components/admin/AdminRolesDomainsTab.tsx`,
`src/features/projections/components/ProjectionResults.tsx`,
`src/features/projections/components/ProjectionHistory.tsx`.

**Tareas:**
- Agregar `<caption className="sr-only">` a las tablas que no lo tienen.
- `scope="col"` en todos los `<th>` de columna que falten.
- `aria-sort="ascending|descending"` en el `<th>` de la columna ordenada de
  `DataGridTable`; quitar el `aria-label="Ordenar por …"` del botón y dejar el nombre
  visible de la columna.
- Envolver los contenedores con scroll horizontal con `tabindex="0"`, `role="region"` y
  `aria-label` (o `aria-labelledby` apuntando al caption).
- No cambiar la lógica de orden, paginación ni los datos.

**Criterio de aceptación:** `tsc` limpio; las tablas siguen ordenando y paginando;
los tests de proyecciones existentes pasan.

### Frente 5 — Verificación y tests (después de 1–4)

**Archivos:** solo `src/__tests__/` (nuevos o existentes).

**Tareas:**
- Agregar tests para:
  - IME: `keydown` con `isComposing: true` no envía; Enter normal sí.
  - Login: atributos `autocomplete`, `name`, `required` y `role="alert"` del error.
  - Tablas: `caption`, `scope="col"`, `aria-sort` en `DataGridTable`; wrapper con
    `tabindex`/`role="region"`.
  - Chat: el estado "Consulta en curso" existe una sola vez y el error tiene
    `role="alert"`.
- Correr y pegar salida real de:
  - `npm test -- --run`
  - `npx tsc --noEmit`
  - `npm run build`
- Revisar el diff completo: que no haya cambios fuera de alcance, que no se hayan
  revertido cambios previos y que no se violen los pilares (no hay backend tocado).
- Si un test falla por un defecto de producción, reportarlo; no arreglar producción
  desde este frente.

**Criterio de aceptación:** suite frontend verde, `tsc` en 0, build OK, informe con la
salida real y lista de archivos modificados.

## Fuera de alcance (para un plan posterior)

- Migración de los 16 modales a `<dialog>` nativo.
- Migración de menús/dropdowns a `popover` nativo.
- Conversión de PNG a WebP/AVIF (requiere generar assets).
- Consolidación total de tokens de color y eliminación de overrides `html.light`.
- `field-sizing: content` para el textarea.
- `content-visibility` o virtualización del historial de chat.

## Resultado (11/11/2026)

Los cinco frentes se ejecutaron. Evidencia real:

- `npm test -- --run`: **23 archivos / 173 tests passed**.
- `npx tsc --noEmit`: **exit 0**.
- `npm run build`: **OK**. ECharts quedó en su propio chunk (`echarts-*.js`, 704,15 kB)
  y ya no se importa estáticamente desde `ChatDashboardPage`; `WidgetChart` (0,28 kB)
  y `ChartSection` (10,95 kB) son chunks separados.
- `npm run test:e2e`: **10 passed + 1 skipped**. El skip es el test de ranking, que
  se salta por diseño cuando los datos demo no producen desglose.
- `pytest`: **1049 passed, 264 subtests** (incluye `builder_only` y la métrica
  nombrada en la pregunta).
- Tests nuevos: `chat_prompt_input_ime.test.ts`, `datagrid_table_a11y.test.ts`,
  `login_page_a11y.test.ts`; ampliación de `projections_page.test.ts` con IME y
  semántica de tablas.

### Corrección post-ejecución (12/11/2026)

El usuario reportó que el selector de fuente del chat había desaparecido y que
“Armar consulta paso a paso” mostraba “no hay columnas” con `ventas_filamentos`
activa. Verificado:

- El selector existía en `3e5b7e5` y se perdió en `6285de6`. Se restauró en el
  subheader de `ChatDashboardPage` (`#chat-active-db-select`), con el mismo
  comportamiento: dropdown si hay más de una fuente, nombre si hay una.
- La causa del mensaje era que `/chat/suggestions` esperaba al LLM (~50 s) antes
  de devolver `builder`; la UI interpretaba `builder=null` como “no hay columnas”.
  Se agregó `builder_only=true` al endpoint: devuelve solo el esquema y no llama
  al LLM (medido: 0,24 s). El frontend pide el constructor por ese camino rápido,
  muestra “Cargando las columnas…” mientras espera y deja las sugerencias
  narrativas en una petición separada. También se eliminó la petición inicial sin
  `connection_id`, que competía con la real.
- Tests: `test_suggestions_builder_only_no_espera_al_llm` (backend),
  `getBuilderOptions` (frontend) y assertion del selector en el e2e de admin.
- Timeout del cliente: subió de 90 s a **180 s**. La consulta real del usuario
  tardó **87–89 s** en el backend y el corte de 90 s la descartaba justo al
  terminar. Medido con `POST /chat/query` y verificado en la UI: ahora muestra
  la respuesta completa.
- Métrica del gráfico: `build_dynamic_visualization` elige la columna numérica
  nombrada en la pregunta antes que el primer numérico del SELECT. Antes
  “suma de monto_total” graficaba `cantidad_registros` solo porque el COUNT
  aparecía primero; ahora grafica `Monto Total`.

**Residual declarado:** el tema toma la preferencia del sistema en la primera carga,
pero la persiste en `localStorage`; si el usuario no elige manualmente y cambia el
tema del sistema con la app abierta, no se sigue el cambio. Queda para un ajuste
posterior si se quiere seguir `matchMedia` en vivo.

**Fuera de alcance, sin cambios:** migración a `<dialog>`/`popover` nativo, conversión
de PNG a WebP/AVIF, consolidación total de tokens y `field-sizing`.

## Verificación final del responsable

El responsable re-ejecuta la verificación completa antes de aceptar el lote:
`pytest`, `npm test -- --run`, `tsc --noEmit`, `npm run build` y, si el cambio lo
justifica, `npm run test:e2e`. Ningún subagente commitea.
