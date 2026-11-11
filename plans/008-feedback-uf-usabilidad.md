# 008 — Feedback del usuario final: lenguaje, navegación y proyecciones

**Fecha:** 10/11/2026
**HEAD de referencia:** `3e5b7e5` más los cambios sin commitear del árbol actual
**Origen:** `C:\Users\Felipe\Downloads\cambios en datia.docx` — 26 puntos y 27 capturas
**Estado:** ✅ Bloques A–F implementados y verificados (11/11/2026). Único pendiente declarado: reemplazar el footer por el PNG original (E2) y la pasada visual de Admin (V1 parcial).

> **Regla de trabajo:** los agentes no commitean. Cada bloque termina con
> `pytest`, `npm test`, `tsc --noEmit` y `npm run build` verdes, con la salida
> real pegada. Lo que no se ejecutó se declara, no se da por verde.
>
> **Pilares:** este plan no agrega ningún camino de ejecución SQL, no toca
> `ast_validator`, `governance_guard`, `mask_rows` ni `audit_logs`. Las
> proyecciones siguen pasando por el validador y por el enmascarado. Si un
> cambio de C4 necesitara una columna nueva, se valida contra las dimensiones
> que ya filtró el RBAC (`resolution["dimension_columns"]`), nunca contra el
> esquema crudo.

---

## 1. Diagnóstico

### 1.1 Lo que el feedback confunde (verificado en código, no interpretado)

| Afirmación del documento | Qué dice el código |
|---|---|
| "El usuario final no tiene opción de parar la consulta" | El **chat sí** tiene "Cancelar" (`ChatDashboardPage.tsx`, tarjeta del intento). El que **no** tenía era **Proyecciones**: `runProjection` creaba un `AbortController` y ningún botón lo usaba. La captura del punto 1 es de Proyecciones. |
| "Pedí un año y responde 6 meses" | `SUPPORTED_HORIZONS = (3, 6, 12)` (`ml_forecast/schemas.py`), el prompt del LLM también ofrece solo 3/6/12 (`ml_service.py`) y la granularidad arranca en mes. "Un año" no encaja en el contrato; el LLM devuelve 6 y el backend lo publica como válido. |
| "No me da la respuesta que esperaba" (cuál producto) | `analyze_question` elige métrica y fecha, **nunca una dimensión**. La dimensión solo existía si se elegía a mano en "Ajustes avanzados". Por eso la narrativa habla del total. |
| "Opciones lo debería ver el UF" | `/settings` no tenía guard (`AppRouter.tsx`) y el botón se mostraba a todos (`Header.tsx`). |
| "Debe ir el nombre de la persona creada" | La tabla `users` solo tenía `username` y `email`; el formulario nunca pedía un nombre. |

### 1.2 Inventario de los 26 puntos

| # | Punto del documento | Estado | Dónde / bloque |
|---|---|---|---|
| 1 | Sin opción de parar la consulta | ✅ | Botón **Detener** en Proyecciones (`cancelProjection`). C0. |
| 2 | Horizonte, granularidad y ajustes avanzados sobran | ✅ | C1–C3 |
| 3 | "Un año" devuelve 6 meses | ✅ | C1–C2 |
| 4 | No da la respuesta esperada ("cuál producto") | ✅ (LLM simulado) | C4; falta verlo con el modelo real |
| 5 | "Este mensaje está de más" (hint de Enter / walk-forward) | ✅ | Hint eliminado y fases reescritas en `ProjectionsPage.tsx` |
| 6 | Login: perfiles demo no deben ir "al momento de la venta" | 🔁 (para pruebas) | Selector restaurado, gateado por `VITE_DEMO_MODE=true`; el build sin la variable no incluye selector ni credenciales |
| 7 | Texto de inicio muy técnico; fuera bcrypt y "sesiones auditadas" | ✅ | `LoginPage.tsx` sin "RBAC", "bcrypt", "bitácora" |
| 8 | `DATIA` → `Dat.ia` en títulos y dentro de la app | ✅ | 12 ocurrencias en `src` + `<title>` de `index.html` |
| 9 | Falta el footer TriData / Capstone | ✅ (parcial) | Footer en la barra lateral con la copia del `.docx`. Asset original pendiente (E2). |
| 10 | "Los botones no funcionan" en Firefox y Edge | ✅ | F: no reprodujo; queda test de regresión |
| 11 | Cuadro amarillo menos técnico | ✅ | Aviso de espera larga y "Estamos preparando la respuesta" |
| 12 | "IA LOCAL" está de más | ✅ | Badge del Header y pill del input eliminados |
| 13 | "IA local activa (ollama)", MOTOR y METADATA con nombre amigable | ✅ | `SystemHealthPopover.tsx`: Asistente / Base interna / Disponible |
| 14 | "DATIA · Asistente Text-to-SQL" de más | ✅ | Badge eliminado de `ChatEmptyState.tsx` |
| 15 | Subtítulo con AST Guardrail y RBAC | ✅ | Reescribo en lenguaje de usuario |
| 16 | Reformular la escritura de la navegación | ✅ | "Consultar", "Opciones", "Proyecciones", "Administración" |
| 17 | Dos botones de Proyecciones | ✅ | Queda el del Header; eliminado el del subheader del chat |
| 18 | La base se muestra tres veces | ✅ | Selector del subheader eliminado; footer reemplaza al de la esquina inferior |
| 19 | Sugerencias rápidas sobran; renombrar Smart Builder | ✅ | Sugerencias fuera; "Smart Builder" → "Armar consulta" |
| 20 | "IA local" en el input y "Enter ↵" | ✅ | Ambos eliminados de `ChatPromptInput.tsx` |
| 21 | Opciones solo para admin | ✅ | Nav oculto para no-admin + guard en `/settings` |
| 22 | Botón "Presentar" de más | ✅ | Botón, modo presentación y atajo F11 eliminados |
| 23 | Simplificar los dos botones del admin en uno | ✅ | "Agregar fuente de datos" |
| 24 | Poner el modo ayuda en Dat.ia, donde estaban Proyecciones/Presentar | ✅ | E1: modal "Cómo usar Dat.ia" |
| 25 | Respuesta de la alerta menos técnica | ✅ | `system/router.py`: "Hay tablas sin clasificar…" |
| 26 | Nombre de la persona creada en el alta de usuario | ✅ | `users.full_name`, migración automática, formulario, éxito, banner y tabla |

Leyenda: ✅ hecho y verificado · ⏳ pendiente · 🚧 parcial.

---

## 2. Decisiones tomadas por el responsable (10/11/2026)

| # | Decisión |
|---|---|
| 1 | Horizonte: la pregunta manda. Aceptar de **1 a 36 pasos**, con parser determinista sobre el texto y el LLM como respaldo. |
| 2 | "¿Cuál producto?": **inferir la dimensión** y publicar el desglose top-N que ya existe. |
| 3 | Alta de usuario: agregar **Nombre completo** (columna nueva, formulario y mensajes). |
| 4 | Footer: se usa la copia del `.docx` hasta recibir el PNG original (el responsable no eligió entre las dos opciones). |

Consecuencias que el plan asume:

- Al quitar los selectores de horizonte/granularidad/ajustes, **desaparece el
  override manual** cuando el resolvedor automático se equivoca. Es una
  decisión del responsable, no un olvido; se documenta como techo conocido.
- Un horizonte de 36 pasos sobre una serie corta va a publicar
  `no_confiable`. Eso es el comportamiento correcto del backtest, no un bug.
- `full_name` es opcional; las cuentas viejas quedan con `NULL` y la UI sigue
  mostrando el username.

---

## 3. Bloques de trabajo

### Bloque A — Marca, lenguaje y limpieza de UI ✅

**Implementado**

- `src/shared/layout/Header.tsx`: `Dat.ia`, sin badge "IA Local", navegación
  nueva.
- `src/pages/LoginPage.tsx`: selector de perfiles de prueba gateado por
  `VITE_DEMO_MODE=true`; fuera bcrypt, "sesiones auditadas" y jerga. El build
  con la variable en `false` no incluye selector ni credenciales.
- `src/components/chat/ChatEmptyState.tsx`: sin badge técnico, sin sugerencias,
  sin pie de "AST Guardrail / RBAC / IA local".
- `src/components/chat/ChatPromptInput.tsx`: sin "IA Local" ni "Enter ↵";
  "Smart Builder" → "Armar consulta".
- `src/shared/layout/SystemHealthPopover.tsx`: nombres y estados amigables, sin
  latencias ni modelo.
- `src/pages/ProjectionsPage.tsx`: sin hint de Enter, fases y descripción
  simples, estimación en segundos legibles.
- `src/components/admin/AdminConnectorsTab.tsx`: "Dat.ia" y un solo botón de
  alta.
- `index.html`: `<title>Dat.ia</title>`.

### Bloque B — Navegación, permisos y layout ✅

- "Opciones" solo admin en desktop y móvil; `/settings` con guard real en
  `src/app/router/AppRouter.tsx`.
- Subheader del chat sin selector de base, sin "Proyecciones" y sin
  "Presentar"; se eliminó el modo presentación y el `F11`.
- `SidebarChatHistory.tsx`: footer TriData en lugar de "Fuente BD activa".
- `useAdminUsers.ts` y `AdminUsersTab.tsx`: banner y tabla con nombre de
  persona.

### Bloque C — Proyecciones: la pregunta manda ✅

Es el bloque grande. Orden interno obligatorio: C1 → C2 → C3 → C4 → C5. C4
puede hacerse después de C3 sin depender de C5.

#### C0. Detener la consulta ✅

- Botón **Detener** en el formulario y en la tarjeta de progreso de
  Proyecciones, atado a `runControllerRef`.
- **C0.b resuelto:** si la consulta corre en **otro hilo**, el chat muestra
  "Hay una consulta en curso en «X»" con un botón **Detener** al lado (mismo
  `handleCancelPrompt`). El usuario siempre puede parar.

**Aceptación:** correr una proyección, pulsar Detener, la tarjeta desaparece y
no se publica resultado; el botón del formulario vuelve a "Proyectar".

#### C1. Parser determinista de horizonte y granularidad

**Dónde:** `backend/app/modules/ml_forecast/ml_service.py` — función pura
nueva, sin tocar SQL.

```python
def parse_question_timeframe(question: str) -> tuple[Optional[int], Optional[str]]:
    """(pasos, granularidad) que pide el texto. None si no pide nada."""
```

**Reglas**

| Texto | Pasos | Granularidad |
|---|---|---|
| "un año", "el próximo año", "12 meses" | 12 | month |
| "2 años" | 24 | month |
| "6 meses" | 6 | month |
| "3 trimestres" | 9 | month |
| "4 semanas", "semanal" | 4 | week |
| "30 días" | 30 | day |
| "en los próximos N …" | N según unidad | según unidad |

- Números en dígitos y en palabras (`un/una/uno`, `dos` … `doce`).
- Tope **36**; si el texto pide más, se recorta a 36 y se declara el recorte
  en el origen (`horizon_source="pregunta_recortada"`).
- Si no hay coincidencia, `(None, None)` y manda el resto de la cadena.

**Precedencia:** `payload.horizon` (API) > texto de la pregunta > LLM >
`DEFAULT_HORIZON=6`. `payload.horizon` queda por compatibilidad, pero la UI
nueva manda `null`.

**Granularidad:** `payload.granularity` > la del texto > `"month"`.

**Tests** (`backend/tests/test_ml_horizon_from_question.py`): tabla de casos,
más un test de propiedad con Hypothesis de que todo resultado está en `1..36`
y la granularidad es `day|week|month`.

#### C2. Contrato y prompts

- `schemas.py`: reemplazar la validación "solo 3/6/12" por `1 <= horizon <= 36`.
  `DEFAULT_HORIZON` sigue en 6.
- `ml_service.py`:
  - `SUPPORTED_HORIZONS` deja de ser la lista cerrada. Agregar
    `MAX_HORIZON = 36` y `MIN_HORIZON = 1`.
  - `_validate_llm_choice`: acepta `1..36`.
  - `_ask_llm_for_columns`: el prompt ofrece `1..36` y pide respetar lo que
    diga la pregunta.
  - `_resolve_horizon`: documentar la prioridad nueva.
- `/projections/estimate` (`router.py`): parsear la pregunta también, para no
  asumir el horizonte máximo. Sin parseo, usar `DEFAULT_HORIZON`.
- Tests: reescribir `test_horizonte_solo_3_6_12_o_auto`
  (`test_ml_honesty.py`) al contrato nuevo **sin borrar los asserts de
  rechazo** (0, 37, -1 siguen siendo inválidos). Agregar el caso "un año".
- `test_estimate_auto_asume_el_horizonte_mas_largo` se ajusta: con una pregunta
  que pide 12 meses, la estimación usa 12; sin horizonte en el texto, usa el
  default.

**Aceptación:** `ProjectionRequest(question=..., horizon=None)` con "proyecta
un año" produce `horizon=12`, `granularity="month"` y
`horizon_source="pregunta"`.

#### C3. UI sin selectores

**Dónde:** `src/pages/ProjectionsPage.tsx`.

- Eliminar estados y controles de horizonte, granularidad y "Ajustes
  avanzados" (`loadContext`, `columnContext`, `tableOverride`,
  `metricOverride`, `dateOverride`, `dimensionOverride`, `advancedOpen`).
- Llamar `projectionService.run(prompt, connectionId, null, undefined, signal)`.
- La tarjeta de progreso deja de decir "Horizonte: 6 pasos / Auto".
- En el resultado, "Horizonte publicado" se muestra con unidad:
  "12 meses", "6 semanas" o "30 días" según `granularity`.
- Actualizar `src/__tests__/projections_page.test.ts` (hoy espera el texto
  "Granularidad" y "12 pasos"). El test debe verificar que los selectores **no
  existen** y que la pregunta viaja tal cual.

**Aceptación:** la pantalla no tiene `Horizonte`, `Granularidad` ni `Ajustes
avanzados`; una pregunta con "un año" publica 12 periodos mensuales.

#### C4. Dimensión inferida ("cuál producto")

**Dónde:** `ml_service.analyze_question` y el router.

- El JSON del análisis agrega `dimension_column`.
- El prompt ofrece solo las columnas de texto de
  `resolution["dimension_columns"]` (ya filtradas por RBAC) y pide elegir una
  cuando la pregunta pida "cuál", "quién", "qué producto", "por producto" o
  "desglosado por".
- Validación fail-closed: si la columna no está en la lista permitida o no es
  dimensión, se descarta (`None`) y no hay desglose. Nunca se usa el esquema
  crudo.
- El router pasa `payload.dimension_column or analysis["dimension_column"]` a
  `run_projection`. El desglose top-N ya existe (`_build_breakdown`,
  `BREAKDOWN_TOP_N`) y el snapshot lo publica.
- La narrativa recibe `breakdown`; el prompt debe poder nombrar el grupo
  principal sin inventar (`payload` ya lo trae).

**Tests:** LLM simulado devuelve una dimensión válida → aparece `breakdown`;
devuelve una bloqueada → `breakdown.available=False` y sin fuga; no devuelve
dimensión → comportamiento actual. Un test de aceptación con la pregunta real
del feedback.

**Aceptación:** "dime cuál es el producto con más ventas en el próximo año"
devuelve `horizon=12`, una dimensión elegida y un desglose con el top-N; la
narrativa nombra el primero. Si no hay dimensión permitida, lo dice.

#### C5. Narrativa y copy de resultados

- `_NARRATION_SYSTEM_PROMPT`: empezar por la respuesta directa; sin "MAPE",
  "naive", "walk-forward", "backtest" ni "banda"; los límites se explican en
  palabras. Se mantiene la regla dura: **solo se citan números del payload**.
- `_template_narration`: mismo criterio para el respaldo determinista.
- `ProjectionResults.tsx`: "Lectura del resultado" → "Qué dicen los números";
  "Confiable" → "Estimación sólida" (o "Revisar"); el pill "IA local" se
  elimina o pasa a "Resumen automático".

**Aceptación:** la narrativa de "un año" habla de 12 meses y no contiene
ninguna de las palabras prohibidas; sigue citando solo cifras del payload.

### Bloque D — Administración y usuarios ✅

- Un solo botón "Agregar fuente de datos" (el wizard ya permite archivo o
  conexión remota).
- `users.full_name`:
  - `backend/app/modules/auth/models.py`
  - `backend/app/core/database.py` (ALTER TABLE para bases existentes)
  - `backend/app/modules/auth/schemas.py` (`UserAdminCreate`, `UserOut`)
  - `backend/app/modules/auth/router.py` (alta, mensaje y auditoría)
  - `src/components/admin/UserAddModal.tsx`, `AdminUsersTab.tsx`,
    `AdminPage.tsx`, `src/types/index.ts`, `auth_service.ts`,
    `useAdminUsers.ts`
- Test nuevo: `test_chat_security.py::test_admin_create_persiste_el_nombre_de_la_persona`.

**Limitación declarada:** el nombre no se puede editar todavía (el PATCH de
usuarios no lo acepta). Si el responsable lo pide, se agrega al modal de
edición; no entra en este plan.

### Bloque E — Ayuda y footer ✅ (E2 pendiente de asset)

#### E1. Botón de ayuda

- Va en el subheader del chat, donde estaban "Proyecciones" y "Presentar".
- No hay contenido definido: el responsable no eligió entre un modal de
  consejos y un enlace a un manual. Propuesta por defecto: modal con cuatro
  bloques (cómo preguntar, qué hace Dat.ia, qué no puede hacer, cómo exportar).
- **Aceptación:** el botón abre y cierra con teclado y click; no tapa el
  historial ni el input; el texto no menciona SQL, RBAC ni modelos.

#### E2. Footer

- Ya está en la barra lateral con la copia del `.docx` (331×175, recortada a la
  franja del logo).
- **Estado:** no se recibió el PNG original; la copia sigue en su lugar. Se ve
  blanda en pantallas grandes.
- Pendiente: reemplazar por el PNG original si el responsable lo entrega.
- Decidir si el footer va también en Login o en todas las pantallas; hoy solo
  está en el chat.

### Bloque F — "Los botones no funcionan" ✅ (no reprodujo)

- La captura es la barra lateral ("Nueva consulta", "Buscar conversación").
  No hay un defecto evidente en el código: `handleNewThread` funciona salvo
  que haya una consulta en vuelo, y en ese caso solo muestra un aviso.
- **Pasos:** levantar backend + frontend, reproducir con Playwright en Chromium
  y Firefox; registrar errores de consola y `page.on('pageerror')`.
- **Si no reproduce:** pedir al responsable el botón exacto y los pasos. No se
  "arregla" a ciegas.
- **Aceptación:** repro escrito con evidencia (captura o log) o declaración
  explícita de que no se pudo reproducir.

**Resultado (11/11/2026):** se agregó `e2e/repro-botones.spec.ts` y se corrió
contra el stack real desde Playwright. **No reprodujo.**

- Chromium (el motor de Edge): 2/2 tests verdes. "Nueva consulta" limpia el
  campo, el buscador acepta y limpia, "Armar consulta" abre y cierra, "Ayuda"
  abre y cierra, y la navegación del Header lleva a Proyecciones, Chat y
  Opciones.
- Firefox 157: 2/2 tests verdes (corrida puntual con una config temporal, ya
  eliminada).
- Director: "Opciones" no se renderiza y `/#/settings` redirige a `/#/chat`.

El test queda como regresión. Si el usuario final vuelve a verlo, hace falta el
botón exacto y los pasos: no se toca código sin repro.

---

## 4. Orden de ejecución recomendado

| Orden | Bloque | Por qué |
|---|---|---|
| 1 | **F** (reproducir botones) | Si los botones no responden para el tester, todo lo demás es invisible. Es barato y desbloquea la prueba. |
| 2 | **C0.b** + **C1–C3** | Es el reclamo principal del documento ("un año" → 6 meses) y quita controles que confunden. |
| 3 | **C4** | Responde "cuál producto"; depende de C1–C3 para no mezclar causas. |
| 4 | **C5** | Copy de la narrativa; se calibra con C4 ya funcionando. |
| 5 | **E1** | Ayuda; necesita el contenido. |
| 6 | **E2** + verificación visual | Footer original y pasada de Playwright por las pantallas tocadas. |

Cada bloque se cierra con la suite completa **antes** de empezar el siguiente.
No se acumulan dos bloques sin verificar.

---

## 5. Verificación por bloque

```text
cd backend; venv\Scripts\python.exe -m pytest -q     # 1020 passed, 258 subtests
npx tsc --noEmit                                      # exit 0
npm test -- --run                                     # 20 archivos / 153 tests
npm run build                                         # exit 0 (warning echarts 704 kB esperado)
npm run test:e2e                                      # 8 passed
```

- Para C1/C2/C4, además: `pytest tests/test_ml_honesty.py tests/test_ml_models.py
  tests/test_ml_projection_audit.py tests/test_ml_service_rbac.py -q`.
- Para C3/C5: `npm test -- --run src/__tests__/projections_page.test.ts`.
- La salida real se pega en el informe del bloque. Si un test se cambia porque
  cambió el contrato, se reescribe el assert; **no se borra**.
- Pasada visual con Playwright (skill `webapp-testing`) al cerrar C3, C4 y E1.

---

## 6. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| El parser confunde "un año, mes a mes" con granularidad semanal | Gana la primera unidad de duración explícita; tabla de casos y test de propiedad. |
| El LLM elige una dimensión bloqueada | Se valida contra `resolution["dimension_columns"]` (RBAC) y se descarta en falso. Test dedicado. |
| 36 pasos sobre series cortas publican bandas enormes | `evaluate_quality` ya marca `no_confiable`; no se recorta el horizonte en silencio. |
| Quitar "Ajustes avanzados" deja al usuario sin escape cuando el resolvedor falla | Decisión del responsable. Se documenta el techo; si aparece en uso real, se reincorpora un override mínimo (tabla + métrica) en otro plan. |
| El cambio de horizonte rompe la estimación de duración | `/estimate` parsea la misma pregunta; test del caso "12 meses" y del caso sin horizonte. |
| `full_name` en bases existentes | `ALTER TABLE ... ADD COLUMN ... NULL` en `database.py`, con el patrón `_safe_alter` que ya usa el proyecto. |
| El footer del `.docx` se ve borroso | Se usa hasta recibir el original; E2 lo reemplaza. |
| "Los botones no funcionan" no reproduce | No se toca código sin repro; se piden pasos exactos. |

---

## 7. Fuera de alcance

- MySQL/SQL Server, autenticación, RBAC del backend y cualquier cambio en
  `ast_validator`, `governance_guard`, `mask_rows` o `audit_logs`.
- Editar el nombre completo de un usuario existente (solo alta y lectura).
- Rehacer la narrativa de proyecciones como un informe largo: se mantiene el
  contrato de 2–3 párrafos.
- Cambiar el prefijo de las contraseñas temporales (`Datia-`): no es un título
  de la aplicación y cambiarlo obliga a tocar tests de seguridad sin beneficio
  para el UF.
- Commits: solo con indicación explícita del responsable.

---

## 8. Registro de ejecución (10/11/2026)

**Bloques A, B y D implementados en el árbol de trabajo.**

Evidencia real:

```text
backend> venv\Scripts\python.exe -m pytest -q
1003 passed, 1 warning, 258 subtests passed in 397.28s (0:06:37)

npm test
Test Files  20 passed (20)
     Tests  156 passed (156)

npx tsc --noEmit
(exit 0, sin salida)

npm run build
✓ built in 9.81s
(warning de chunk de echarts 704 kB, el esperado)
```

- Se actualizó la línea base de `AGENTS.md` (1002 → 1003 y fecha/HEAD).
- No se commiteó nada.

### 11/11/2026 — Bloques C, E y F

**Implementado**

- `ml_forecast/timeframe.py`: parser determinista de horizonte y granularidad
  ("un año" = 12 meses, "6 semanas" = 6 semanas, "3 trimestres" = 9 meses),
  con tope de 36, descarte de menciones históricas ("últimos 12 meses") y
  `requested_steps` para declarar el recorte.
- `schemas.py`: rango publicable 1–36; se eliminó la lista cerrada 3/6/12.
- `ml_service.py`: prompts y validación del LLM en 1–36; el análisis acepta
  `dimension_column`; la narrativa (LLM y plantilla) quedó en lenguaje simple.
- `router.py`: la pregunta fija horizonte y granularidad; `/estimate` parsea la
  misma pregunta; el origen se publica como `pregunta`/`pregunta_recortada`.
- `ProjectionsPage.tsx`: fuera Horizonte, Granularidad y Ajustes avanzados; la
  UI manda `horizon=null` y no manda overrides. Detener en el formulario y en
  la tarjeta de progreso.
- `ChatDashboardPage.tsx`: "Detener" visible cuando la consulta corre en otro
  hilo; botón "Ayuda" en el subheader.
- `HelpModal.tsx`: ayuda en cinco bloques, sin SQL ni RBAC.
- `ProjectionResults.tsx`: "Qué dicen los números", "Estimación sólida",
  sin pill "IA local" y horizonte con unidad ("12 meses", "6 semanas").
- E2E nuevos: `e2e/projections.spec.ts` (un año → 12 meses) y
  `e2e/repro-botones.spec.ts` (controles + permisos + ayuda).

**Evidencia real**

```text
backend> venv\Scripts\python.exe -m pytest -q
1020 passed, 1 warning, 258 subtests passed in 419.20s (0:06:59)

npm test
Test Files  20 passed (20)
     Tests  153 passed (153)

npm run build
✓ built in 10.14s
(warning de chunk de echarts 704 kB, el esperado)

npm run test:e2e
5 passed (1.0m)
  ok smoke de login
  ok admin: los controles del chat responden (incluye Ayuda)
  ok director: Opciones oculto y /settings redirige
  ok proyecciones: "próximo año" publica "Horizonte publicado: 12 meses"
```

- Firefox 157: los dos tests de `repro-botones` también pasaron (config
  temporal, ya eliminada).
- Línea base de `AGENTS.md` actualizada a 1020 / 153 / 4.
- No se commiteó nada.

**Limitación declarada:** la elección de dimensión se validó con el LLM
simulado (el entorno de test no tiene Ollama). Falta verla con el modelo real
en la instalación del cliente.

**Selector de perfiles de prueba (a pedido del responsable, 11/11/2026):**
restaurado en `LoginPage.tsx` y gateado con `VITE_DEMO_MODE === 'true'` (no
`!== 'false'`, para que un build sin la variable quede cerrado por defecto).

Verificación del bundle:

```text
build con VITE_DEMO_MODE=true  → 1 coincidencia de "admin123" + selector
build con VITE_DEMO_MODE=false → 0 coincidencias de "admin123" + 0 selectores
```

Test e2e nuevo: elegir "Director Ejecutivo" rellena usuario y contraseña, y el
botón "Ingresar" entra al chat.

### 11/11/2026 — Lote 2 con 5 subagentes

**Implementado y revisado**

- **Permisos:** guard de ruta para `/admin` (igual que `/settings`) y el
  endpoint de anomalías deja de enviar a los no-admin las de `type="security"`
  (tablas sin clasificar y bloqueos AST), recalculando `count` y
  `has_critical`. Test en `test_anomalies_rbac.py`.
- **Narrativa:** la plantilla determinista nombra el grupo con mayor
  proyección cuando hay `breakdown` disponible. Se corrigió el alcance de la
  frase: dice "entre los grupos publicados", porque el desglose es el top-N por
  volumen histórico, no toda la dimensión. Test con dos grupos y sin desglose.
- **Resultados:** tabla/métrica/eje con nombres legibles y el nombre crudo en
  `title`; "Datos hasta: <último período>"; etiquetas simples ("Error
  promedio", "Repetir el último valor", "¿Mejor que repetir el último
  valor?"); "Error y evidencia por paso" y "Método y evidencia" colapsados bajo
  "Ver detalle técnico" (no se renderizan cerrados). "Proyecciones con Machine
  Learning" → "Proyecciones".
- **Chat:** "Tablero (0)" se oculta solo con conteo confirmado en cero; el
  inicio tiene un enlace a la ayuda; el input vacío ofrece "Armá la consulta
  paso a paso". Se corrigió la regresión detectada por el subagente: al fijar
  un widget, `onWidgetPinned` refresca el conteo y el botón "Tablero" reaparece
  sin recargar.
- **Mi cuenta (lote 2, continuación):** el bloque de usuario del Header es un
  menú con "Cambiar contraseña" (abre `MandatoryPasswordChangeModal` en modo
  voluntario) y "Cerrar sesión"; en móvil la entrada está en el drawer. Se
  corrigió un bug real: el dropdown quedaba debajo del subheader del chat
  (`z-30` hermano y posterior en el DOM), así que el badge "1 Alerta"
  interceptaba los clics; el Header pasó a `z-40`.
- **Detener honesto:** los botones de Proyecciones y el aviso del chat aclaran
  que cortan la espera en pantalla, no el cálculo del servidor.
- **Tooltips del gráfico:** "MAPE del paso" → "Error del paso", "Backtests" →
  "Pruebas", "Sin backtest propio" → "Sin prueba propia", "Banda" → "Margen".
- **Trimestres (continuación):** granularidad `quarter` de punta a punta
  (`forecast_calculator`, SQL `TO_CHAR(..., 'YYYY-"Q"Q')`, `_period_bounds`,
  esquemas, tipos de UI y etiquetas). "3 trimestres" publica 3 pasos
  trimestrales, no 9 mensuales. Test end-to-end con una serie de 8 trimestres:
  pasos `2026-Q3`, `2026-Q4`, `2027-Q1`.
- **Razones de calidad:** dejan de decir MAPE/naive/backtests en la UI;
  ahora dicen "el error promedio (67,7%) supera el límite de 50%", "no mejora
  a repetir el último valor", "el margen del total cubre 3 de 6 pasos",
  "faltan períodos en la serie". El detalle técnico conserva los números.
  El tooltip del badge también se tradujo ("Error promedio", "Mejora a repetir
  el último valor", "pruebas", "períodos"), y el valor del método pasó a
  "Mejor / No mejora a repetir el último valor".
- **Admin:** test e2e de la página real: carga, tiene un solo "Agregar fuente
  de datos" y ya no están "Importar Archivo"/"Conectar BD Remota".
- **Cancelación:** se eligió la opción S del informe (documentar la
  limitación). El copy ya lo dice; la cancelación cooperativa/real queda como
  frente separado.
- **Investigación (read-only):** mapa de próximos frentes. Trimestres es un
  cambio cross-layer (parser, SQL, aritmética de períodos, UI, snapshots) y
  esfuerzo **L**; la cancelación server-side mínima es cooperativa (**M**) o se
  documenta la limitación (**S**); "Mi cuenta" es **S** y reutiliza el modal y
  el endpoint existentes; el footer necesita un asset transparente para el tema
  claro (**S/M**).

**Evidencia real**

```text
backend> venv\Scripts\python.exe -m pytest -q
1031 passed, 1 warning, 264 subtests passed in 375.89s (0:06:15)

npm test
Test Files  20 passed (20)
     Tests  156 passed (156)

npx tsc --noEmit
(exit 0)

npm run build
✓ built in 10.59s

npm run test:e2e
7 passed (1.7m)
```

**Correcciones del revisor (no del subagente):** frase del desglose acotada a
los grupos publicados; refresco del tablero al fijar; locator del e2e de Ayuda
con `exact: true` (el enlace del inicio también contiene "ayuda"); el e2e de
proyecciones abre el detalle técnico antes de buscar el horizonte.

### Hallazgo 11/11/2026 — "10 años" y "10% de las bases"

La pregunta real `Proyectame una ganancia con el 10% de las bases en 10 años`
expuso tres problemas distintos:

1. **El tope de 36 pasos se presentaba como respuesta.** El parser recortaba
   120 meses a 36 y el detalle decía "máximo 36", pero la narrativa no lo
   declaraba: el LLM escribió "próximos 10 meses".
2. **El motor no deriva métricas ni aplica supuestos.** Eligió `monto_total`
   (ventas), no una ganancia; el "10%" se ignoró. La tabla no tiene una columna
   de margen entre las opciones que ofreció el resolvedor, y el pipeline no
   soporta "ganancia = 10% de las ventas".
3. **El método ganador fue la media histórica**, que produce una línea plana.
   Es correcto según el backtest (2,8% vs 4,8% de OLS), pero no responde una
   pregunta de crecimiento a 10 años.

**Guards implementados (backend):**

- `narrate` descarta la prosa del LLM si contradice el horizonte publicado
  ("próximos 10 meses" / "horizonte de 10 meses" con `horizon=36`) y publica la
  plantilla. Cubre "próximos", "horizonte de", "periodo de" y "durante".
- `narrate` descarta la prosa si contiene jerga prohibida (`MAPE`, `naive`,
  `backtest`, `walk-forward`, `banda`); el LLM local la ignora con frecuencia.
- El router antepone a la narrativa: "Pediste 120 meses (10 años); el máximo
  publicable es 36 meses…" cuando hay recorte.
- El router antepone: "La pregunta pide una ganancia con un porcentaje; la
  serie publicada es «monto total». Dat.ia no aplica supuestos de porcentaje
  todavía." cuando la pregunta mezcla ganancia + % y la métrica no es de margen.

**Modo escenario implementado (11/11/2026):**

- `timeframe.parse_growth_scenario` reconoce "crecimiento anual del X%",
  "X% anual" y "ganancia ... X% ... años"; devuelve el porcentaje.
- `ml_service._scenario_projection` compone el crecimiento anual sobre el
  último valor histórico (mensual: `(1+g)^(1/12)`), sin backtest, banda ni MAPE.
- El router deja que el escenario use `requested_steps` (120 para 10 años) en
  lugar del tope estadístico de 36, y publica `scenario` en la respuesta.
- La narrativa del escenario es determinista: valor base, valor final,
  ganancia vs. base y la aclaración de que no es un pronóstico.
- La UI muestra "Escenario de crecimiento", "10% anual", "Ganancia vs. base" y
  oculta el semáforo de calidad, el detalle técnico y la leyenda de banda.
- E2E real con el LLM local encendido: la pregunta exacta publica el escenario
  de 120 meses en 11,7 s.

**Decisión tomada:** "10% de las bases" = crecimiento anual del 10%; el modo
escenario permite 10 años sin fingir un pronóstico estadístico. El modo
estadístico sigue con tope 36 y aviso de recorte.

**Pendiente de decisión de producto:**

- La media plana sigue siendo el método elegido por backtest para el modo
  estadístico; un escenario de crecimiento ya no depende de ella.
- Si se quiere el escenario con una dimensión (producto), falta aplicarlo a
  cada grupo por separado.

**Verificación:** pytest **1035 passed, 264 subtests**; vitest **157**; e2e
**8 passed** (incluye la pregunta real del escenario).

### Próximos frentes (del informe del investigador)

| Frente | Esfuerzo | Dependencia |
|---|---|---|
| Cancelación real/cooperativa de proyecciones | M (cooperativa) / L (real) | Decisión de producto y estado de auditoría nuevo |
| Footer global y tema claro | S/M | Asset transparente de marca |
| Verificar la dimensión con el modelo local real | S | Ollama encendido en la máquina del cliente |

---

## 9. Checklist de ejecución

### Hecho

- [x] A1. `Dat.ia` en toda la app y en el título del navegador.
- [x] A2. Login con selector de perfiles de prueba, gateado por `VITE_DEMO_MODE=true`; sin la variable no hay selector ni credenciales en el bundle.
- [x] A3. Inicio del chat sin badge técnico, sin sugerencias y sin pie de governance.
- [x] A4. Input sin "IA Local", sin "Enter ↵" y "Smart Builder" renombrado.
- [x] A5. Salud del sistema con nombres y estados amigables.
- [x] A6. Alertas y textos de espera sin jerga.
- [x] B1. "Opciones" solo admin + guard de `/settings`.
- [x] B2. Subheader sin selector de base, sin "Proyecciones" y sin "Presentar".
- [x] B3. Footer TriData en la barra lateral (asset del `.docx`).
- [x] C0. Botón **Detener** en Proyecciones.
- [x] C0.b. "Detener" visible cuando la consulta corre en otro hilo del chat.
- [x] C1. Parser determinista de horizonte/granularidad + tests de tabla y propiedad.
- [x] C2. Contrato 1–36, prompts y `/estimate`; test de 3/6/12 reescrito.
- [x] C3. Fuera Horizonte, Granularidad y Ajustes avanzados de la UI + tests.
- [x] C4. Dimensión inferida y desglose top-N + tests de RBAC (LLM simulado).
- [x] C5. Narrativa simple y copy de resultados.
- [x] D1. Un solo botón "Agregar fuente de datos".
- [x] D2. `full_name` de punta a punta + test.
- [x] D3. Verificación completa (pytest, vitest, tsc, build).
- [x] E1. Botón de ayuda + modal "Cómo usar Dat.ia".
- [x] F1. "Los botones no funcionan": no reprodujo en Chromium ni Firefox; test de regresión agregado.
- [x] L2-1. Guard de ruta para `/admin`.
- [x] L2-2. Alertas `security` ocultas a no-admin + conteo recalculado.
- [x] L2-3. La plantilla de respaldo nombra el grupo top del desglose.
- [x] L2-4. Nombres legibles, "Datos hasta", etiquetas simples y detalle técnico colapsado.
- [x] L2-5. "Tablero (0)" oculto, enlace a Ayuda y atajo al constructor; refresco al fijar un widget.
- [x] L2-6. "Mi cuenta" en el Header (cambio de contraseña voluntario y logout), con el fix de z-index del dropdown.
- [x] L2-7. "Detener" honesto (corta la espera, no el cálculo) y tooltips del gráfico sin MAPE/backtest.
- [x] L2-8. Granularidad trimestral real (`quarter` de punta a punta) + test end-to-end con 8 trimestres.
- [x] L2-9. Razones de calidad en lenguaje simple.
- [x] L2-10. Pasada funcional por Admin con Playwright (un solo alta de fuente).
- [x] L2-11. Cancelación: se documentó la limitación (opción S); la UI ya no promete detener el cálculo.
- [x] L2-12. Guards de narrativa: horizonte contradictorio, jerga prohibida, aviso de recorte y aviso de porcentaje no aplicado.

### Pendiente

- [ ] E2. Footer con el PNG original (si se entrega) y decisión de alcance (tema claro, global).
- [ ] Verificar la inferencia de dimensión con el modelo local real (Ollama no está corriendo en este entorno).
- [ ] Cancelación cooperativa/real server-side — M/L, frente separado.
- [ ] Decidir la semántica de "10% de las bases" y si se agrega un modo de escenario (supuesto) o se amplía el tope a 120 para 10 años.
