# Plan 004: Reducción de Sobreingeniería (código muerto, duplicación y abstracciones especulativas)

**Fecha de auditoría:** 18/10/2026
**Commit de referencia:** `456e6f8` (árbol limpio al auditar; HEAD de `Avances-Felipe`)
**Prioridad:** Media (es deuda; no hay hallazgo de seguridad nuevo acá)
**Complejidad:** S por fase (5 fases independientes)
**Riesgo:** Bajo en fases 1–3 · Medio en fase 4 (decisiones de producto) · Alto en fase 5 (estructural)
**Estado:** ✅ Fases 1–4 y los diferidos `H1`/`F-05` ejecutados y verificados (19/10/2026). Fase 5 iniciada por demanda el 21–22/10/2026 (wizard, `ast_validator` y la rama conversacional de `execute_query`). Abiertos: lote Docker/deps/scripts (exige build limpio), `DUP-09`, `H15` y el resto de la Fase 5.

---

## 🎯 Contexto

El proyecto creció por fases (chat → gobernanza → informes → proyecciones ML) y
acumuló tres clases de peso que no paga renta: código muerto verificable,
duplicación textual y abstracciones para escenarios que no existen. Este plan
inventaría todo lo encontrado, fija el orden de ataque y registra lo ya ejecutado.

## 🔬 Cómo se hizo la auditoría

- 4 pasadas paralelas de exploración (subagentes read-only): backend
  `chat_engine/catalog/admin_catalog/ml_forecast`; backend
  `auth/reports/system/telemetry/api/core/db/scripts/tests`; frontend completo;
  deps/scripts/docker/CI.
- Lente: ladder ponytail (¿necesita existir? → ¿ya existe en el repo? → stdlib →
  nativo → dependencia ya instalada → una línea → código nuevo).
- **Cada hallazgo fue re-verificado** por el responsable con `git grep`/lecturas
  antes de entrar acá. Los "nadie lo usa" son conteos estáticos de referencias.
- Límite declarado: no cubre consumidores externos al repo (clientes de API,
  colecciones Postman) ni reflexión/dinamismo en runtime.

## 📏 Cuánto hay (estimación verificada por muestreo)

| Área | Muerto | Duplicación | Especulativo / config | Estructural |
|---|---|---|---|---|
| `chat_engine`, `catalog`, `admin_catalog`, `ml_forecast` | ~140 LOC + canal `thinking_process` (12 puntos) | ~200 LOC (6 sitios) | `F-18` (mock-check en prod) | 6 archivos >600 líneas |
| `auth`, `reports`, `system`, `telemetry`, `api`, `core`, `db` | ~180 LOC + ~60 en schemas/re-exports | ~470–560 LOC (seeder, fixture JWT ×31, exporters) | `H14` (multi-dialecto de 1 dialecto) | `init_db.py` 666 · `prompts.py` 444 |
| Frontend | ~45 LOC + 29 exports sin consumidor | ~350–420 LOC (9 sitios: copiar/descargar/tooltip/selector/banner) | `ESTADO-01/02` | 5 gigantes: 1765/973/789/527/487 |
| Scripts / deps / infra | ~360 LOC de scripts sin referencia | compose partner ~75 líneas espejo | `pyproject` muerto, techo `bcrypt`, drift de Python | 2 composes, 3 fuentes de versión |

**Total: ~1.500–1.800 LOC candidatas a reducción, 1 defecto activo (`F-07`),
1 riesgo latente (`BS-04`) y 10 archivos gigantes.** Son estimaciones por ítem
(anexo A), no una medición byte a byte.

---

## 🛠️ Fases

### Fase 1 — Saneamiento mecánico (bajo riesgo, sin decisiones) ✅ ejecutada en este pase

Borrado de código muerto verificado + micro-dedups + arreglos sin cambio de
comportamiento. Detalle con ubicación en el anexo A:

- **Defecto activo:** `F-07` — `audit_connection_nulls` creaba engine sin
  `dispose()` (pool huérfano por request); pasa a `connector_engine`.
- **Backend muerto:** `F-03` (clase + cuerpo inalcanzable tras el tripwire),
  `F-08`, `F-09`, `F-15`, `H6` (pass-throughs), `H7`/`H8` (schemas y
  re-exports sin importadores), `H10` (constantes), `H11` (setting inerte).
- **Scripts sin referencia:** `bootstrap_postgres.py`, `verificar_intencion.py`
  (duplicado 1:1 de un test), `revision_de_funcionamiento.py` (one-off declarado).
- **Deps:** línea `email-validator` duplicada de `pydantic[email]`.
- **Duplicación chica:** `F-06` (set de tablas de metadata ×2 → `core/constants`),
  `H13` (`role_focus` ×2 → helper).
- **Frontend muerto:** `MUERTO-01..05` (método sin llamador, props fantasma,
  interfaz sin uso, `idx` muerto, 5 iconos importados y no usados).
- **Frontend dedup:** `DUP-01` (descarga blob ×4 → helper), `DUP-03`
  (`formatTooltipHtml` ×4 → `charts/theme`).
- **CI:** `CI-01/02` — un solo job `frontend` (un `npm ci`), sin `tsc` repetido;
  `SC-R-02` — `check-extra-charts.mjs` pasa a test de vitest y entra a CI.
- **Doc/scripts:** comentario falso de `setup.ps1` sobre fallback a SQLite. El
  bloque de verificación del marcador **se conserva**: `docker compose up` sin
  `--build` puede reutilizar una imagen vieja que sí tenía el fallback, así que
  el guard cubre ese caso real (corrección encontrada al ejecutar; ver anexo).

**Criterio de aceptación:** `pytest`, `npm test`, `tsc --noEmit` y `npm run build`
verdes con salida real pegada; ningún cambio de contrato HTTP; LOC netas negativas.

**Resultado verificado (18/10/2026):** `677 passed, 102 subtests passed in 320.32s`
(pytest) · `tsc exit 0` · `14 archivos / 95 tests passed` (vitest: +1 archivo/+5
tests, entró `extra_charts.test.ts`) · `built in 12.78s` (build sin errores).

### Fase 2 — Deduplicaciones backend (riesgo medio, con tests que ya cubren)

| ID | Qué | LOC |
|---|---|---|
| `F-01` | `governance_guard`: 3 métodos casi idénticos → 1 helper fail-closed | ~90 |
| `F-02` | `chat_engine/router`: bloque de auditoría de `/query` y `/query/stream` → 1 helper | ~28 |
| `H1` | Doble seeder (`setup_postgres_full` × `init_db`): una sola fuente | ~110–150 |
| `H4` | `export_pdf`/`export_excel`: ~60 líneas idénticas → helper común (compliance) | ~60 |
| `H5` | `init_db` re-implementa `ensure_schema_migrations` inline | ~47 |
| `H3` | Fixture JWT re-escrita en 31 archivos de test → helper en `conftest` | ~250–310 |
| `F-05` | Resolución de conexión repetida 5 veces (decidir semántica del 404) | ~50 |
| `H12` | Predicado "es admin" duplicado (`auth/router` × `deps`) | ~11 |
| `F-14` | Mensajes "no se puede proyectar" duplicados (`forecast_service` × `ml_service`) | ~10 |
| `F-13` | Predicado de celda nula ×8 (solo unificar; la rama que escribe es plan 002) | ~8 |
| `F-11` | Shim de firma vieja + alias solo-test en `sql_executor`/`engine` | ~4 |
| `F-10` | Rama URL-string de `execute_raw_sql` (reescribir el test que la usa) | ~14 |
| `H15` | Recorte del solape `test_traceability_defaults` × `test_reports_export` | ~25 |

**Resultado verificado (18/10/2026, sin commit):** full pytest `677 passed, 102
subtests passed in 489.75s` sobre el árbol con Fases 1–3.

Ejecutados: `F-01`, `F-02`, `H4`, `H5`, `H12`, `F-13`, `F-14`, `F-11`, `F-10`,
`H3` (24 archivos de tests → `make_auth_headers` en `conftest.py`; el diff no
elimina ningún `assert`).
Diferidos con causa en ese pase: `H1` y `F-05` (exigían decisión de producto y
verificación runtime; ejecutados en el pase del 19/10/2026, ver abajo) y `H15`
(el solape cubre superficies distintas; recortar era cosmético).

**Pase del 19/10/2026 (cerrado):** `H1` ejecutado —
`setup_metadata_database` delega en `init_db` y conserva lo suyo: crear las
bases, sembrar la base de negocio y refrescar las credenciales del conector de
plataforma con los settings vigentes. Verificado con doble corrida de
`scripts/setup_postgres_full.py` y re-ejecución de `init_db` sobre una base
desechable: mismo estado (8 roles, 10 usuarios, 1 conector, 84 permisos de
tabla, 58 de columna, 61 entradas de catálogo). `F-05` ejecutado — la
resolución vive en `SchemaInspector.resolve_connection` (id explícito truthy →
404 si no existe; luego la activa; luego la última) y el 404 se propaga en
`governance_guard` y `sql_generator` en vez de degradar a "sin permisos";
`SchemaIntrospectionError` mantiene la degradación de hoy. `H15` sigue
diferido.

Corrección hallada al ejecutar la suite: en `F-01` el helper quedó invocado como
`cls._schema_field_for_role(...)`, pero `engine.py` expone los tres métodos como
fachada de clase (`QueryEngine.get_allowed_tables_for_role =
GovernanceGuard.get_allowed_tables_for_role`) y en ese camino `cls` llega
re-bound a `QueryEngine`, que no tiene el helper. Lo atraparon dos tests de
`test_transaction_hygiene`; la llamada quedó explícita a `GovernanceGuard` (el
código original tampoco usaba `cls`).

### Fase 3 — Frontend + infra (riesgo bajo-medio)

- Frontend: `DUP-02` (copiar+flash ×11 → hook), `DUP-04` (baseDark/dataZoom ×3),
  `DUP-05` (selector de fuente ×3), `DUP-06` (banner de error ×7),
  `DUP-08` (extracción de mensaje ×36 — opcional), `DUP-09` (nav ×8 + tabs ×6),
  `ESTADO-01` (auto-resize con CSS `field-sizing` — verificar navegadores),
  `ESTADO-02` (9 `copied*` que caen con `DUP-02`).
- Infra: `DK-01` (compose partner espejo; unificar con override),
  `DK-02` (gcc/libpq-dev en imagen — **verificar con build limpio antes**),
  `DK-03` (versión de Python en 4 fuentes → una), `DEP-BE-03` (metadata muerta
  de `pyproject`), `DEP-BE-04` (pytest/hypothesis en imagen de producción),
  `DEP-BE-05` (techo `bcrypt<4.1` sin justificación — probar antes de soltar),
  `SC-R-01` (`init-db.js` + detección de Python duplicada),
  `BS-04/BS-05` (archivar/decidir `migrate_fix_rbac_permissions` — ver riesgo),
  `DOC-01` (`PARTE-DOCKER.md` huérfano: enlazar desde README).

**Resultado verificado (18/10/2026, sin commit):** mismo pass — `tsc --noEmit`
exit 0 · vitest `14 archivos / 95 tests` · `npm run build` OK.

Ejecutados: `DUP-02` (hook `useCopyFeedback`: 11 estados `copied`; incluye
`ESTADO-02` y el copiado de imagen de `ChartSection` vía `flash()`), `DUP-04`,
`DUP-06` (`ErrorBanner`: 7 banners → 1), `DEP-BE-03` (`pyproject.toml` recortado
a config de pytest), `DOC-01` (enlace desde `README.md` §4).
Pendientes declarados: `DUP-05`, `DUP-08`, `DUP-09`, `ESTADO-01`, `DK-01..03`,
`DEP-BE-04/05`, `SC-R-01`, `BS-04/05` (varios exigen build limpio de Docker o una
base legacy, no verificables en local).

**Pase del 19/10/2026 (cerrado):** `DUP-05` ejecutado — el bloque de botones de
fuente (pills) pasa a `ConnectionPills`; los toolbars siguen propios porque
difieren de verdad (subtítulo, refresco en encabezado vs. en la lista,
insignias). `DUP-08` ejecutado — `getApiErrorMessage` en `api_client.ts`
reemplaza `err.response?.data?.detail || 'texto'` en 33 sitios (los que además
probaban `err.message` pasan el tercer argumento; el interceptor sigue
normalizando `detail` a string). `ESTADO-01` cerrado como **no-go**:
`field-sizing` es Baseline recién desde 2026-06-16 (Chrome/Edge 123, Firefox
152, Safari 26.2; verificado este pase contra MDN BCD y webstatus.dev) y el
efecto actual son 6 líneas que funcionan en todos lados; se revisa cuando la
cobertura deje de ser un riesgo. `DUP-09` no ejecutado:
unificar nav/tabs sin tests de renderizado es riesgo cosmético, no reducción
verificable; queda disponible. Sigue pendiente el lote Docker/deps
(`DK-01..03`, `DEP-BE-04/05`, `SC-R-01`, `BS-04/05`).

### Fase 4 — Decisiones de producto (requieren OK explícito)

| ID | Decisión | Costo |
|---|---|---|
| `INV-1` | `holt_damped` + `statsmodels/pandas/scipy`: se queda, o sale con sus **222 MB** y **3,14 s** de import | alto |
| `F-04` | Canal `thinking_process` muerto end-to-end: borrar del contrato o dejarlo como reserva | medio |
| `F-17` | `OPENAI_COMPATIBLE_API_KEY` definido y nunca leído: cablearlo o borrarlo | bajo |
| `F-12` | `MAX_HISTORY_TURNS`: el setting no lee nadie (Fase 1 lo borra); si quieren el knob, se lee desde `settings` | bajo |
| `F-16` | `audit_connection_nulls(db=...)` nunca usa `db`: quitar el parámetro (toca 2 call sites + 3 tests) | bajo |
| `F-18` | Detección de `MagicMock` en producción: reemplazar por fixture que limpie el cache | medio |

**Pase del 19/10/2026 (cerrado):** `INV-1` → se quita `holt_damped` junto con
`statsmodels`, `scipy` y `pandas` (222,4 MB de sitios en disco medidos);
`F-04` → se borra `thinking_process` del contrato (backend, tipo y JSX; la
etiqueta `holt_damped` del frente se conserva para snapshots históricos);
`F-17` → se borra el setting sin lectores; `F-16` → se quita el parámetro `db`
inerte; `F-18` → causa real identificada (caché de clase compartido entre
tests) y reemplazada por la fixture autouse `dynamic_schema_cache_clean`;
`F-12` → se queda el literal `MAX_HISTORY_TURNS = 20` de
`chat_engine/schemas.py`: no hay pedido del knob y el setting ya no existe.
Verificación del pase: pytest `689 passed, 102 subtests passed` (7:44), `tsc
--noEmit` exit 0, vitest `14 archivos / 95 tests`, `npm run build` OK.

### Fase 5 — Gigantes estructurales (por demanda, NO como proyecto)

`DatabaseWizardModal.tsx` (1765), `useChatEngine.tsx` (973), `ChatDashboardPage.tsx`
(789), `chat_engine/router.py` (1186), `init_db.py` (666), `prompts.py` (444),
`KPI/LibrarySection` etc. **Regla YAGNI:** se parten cuando haya que tocarlos por
otro motivo; no se abren frentes de refactor por estética. La tabla del anexo A
sirve de mapa para cuando llegue ese momento.

Pase por demanda del 21–22/10/2026: `DatabaseWizardModal` partido en
`src/components/admin/database-wizard/` (7 componentes + configuración);
`validate_and_secure_sql` salió del umbral C901 con helpers de 18–25;
`execute_query` bajó de 59 a 48 con la rama conversacional extraída. Quedan sin
tocar `useChatEngine`, `ChatDashboardPage`, `chat_engine/router.py` e
`init_db.py`.

Medición del 22/10 sobre `backend/app` con `ruff.toml`: funciones sobre C901 =
**26 en el árbol de trabajo**, contra **24 de la línea base del plan 005**. El
neto +2 viene entero de `ast_validator`: la función de 90 salió del umbral pero
dejó 3 helpers de 18, 20 y 25. Bajó el peor caso (90 → 48), no el conteo. La
meta sigue siendo el conteo, no solo el máximo.

---

## ✅ Criterios de aceptación (todas las fases)

1. La suite completa verde con la salida real pegada en el informe
   (`pytest`, `npm test`, `tsc --noEmit`, `npm run build`).
2. Sin cambios de contrato HTTP ni de comportamiento observable (fases 1–3).
3. Cada borrado con "cero llamadores" verificado por comando, no por lectura.
4. Si algo no se pudo verificar (p. ej. `DK-02` sin `docker build`), el informe
   lo declara y el ítem queda pendiente.

## 🔗 Relación con otros planes

- **002** (solo lectura): implementado el 19/10/2026 por separado. `F-13` solo
  unificó el predicado; el chat ya no llama `apply_null_policy`, que queda
  reservado al endpoint administrativo. No acoplar.
- **003** (engines): ya resolvió el patrón `connector_engine`; `F-07` es el
  último módulo que lo ignoraba.
- Este plan no introduce features: solo quita peso. Si un ítem exige una feature
  nueva (p. ej. cablear `MAX_HISTORY_TURNS`), se decide en Fase 4 y se sale de acá.

---

## Anexo A — Inventario completo

Leyenda estado: ✅ hecho (Fase 1 o pases del 18–19/10) · 🔜 Fase 2/3 · ⚖️ decisión (Fase 4) · 🏗️ estructural (Fase 5) · ❎ no se toca o no-go (justificado)

| ID | Ubicación (referencia) | Qué | ~LOC | Riesgo | Estado |
|---|---|---|---|---|---|
| F-07 | `catalog/services/null_manager.py:32-34` | Engine sin dispose en auditoría de nulos (defecto activo) | 3 | bajo | ✅ |
| F-03 | `admin_catalog/importers/postgres_importer.py:12-27,381-405` | Clase + cuerpo inalcanzable tras el `raise` tripwire | 41 | bajo | ✅ |
| F-08 | `chat_engine/kpi_calculator.py:138-164` | `normalize_llm_number` sin llamadores | 27 | bajo | ✅ |
| F-09 | `catalog/services/catalog_enricher.py:202-236` | `_find_existing_item` sin llamadores | 35 | bajo | ✅ |
| F-15 | `admin_catalog/router.py:1,38` | `logging`/`logger` sin uso | 2 | bajo | ✅ |
| H6 | `reports/generator.py:22-28` | `generate_pdf/excel` pass-through sin llamadores | 7 | bajo | ✅ |
| H7 | `auth/schemas.py:26-54` | `TokenData`, `UserCreateByAdmin`, alias `UserCreate`, `UserUpdate` sin uso | ~20 | bajo | ✅ |
| H8 | `auth/__init__.py`, `telemetry_audit/__init__.py`, `reports/__init__.py`, `reports/schemas.py` | Re-exports sin importadores | ~42 | bajo | ✅ |
| H10 | `core/constants.py:38-44` | `DOMAIN_*` y `DEFAULT_LLM_MODEL` sin uso en backend | 7 | bajo | ✅ |
| H11/F-12 | `core/config.py:53-56` | `MAX_HISTORY_TURNS` inerte (el límite real es literal en `chat_engine/schemas.py:14`) | 4 | bajo | ❎ (decidido 19/10: no hay pedido del knob) |
| H2a | `backend/scripts/bootstrap_postgres.py` | Sin referencias; delega en el camino vivo | 90 | bajo | ✅ |
| H2b | `backend/scripts/verificar_intencion.py` | Duplicado 1:1 de `test_intent_classifier.py` | 25 | bajo | ✅ |
| H2c | `backend/scripts/revision_de_funcionamiento.py` | One-off declarado ("corregidos hoy", 03/10) | 136 | bajo | ✅ |
| DEP-BE-02 | `requirements.txt:4,6` | `email-validator` duplica `pydantic[email]` | 1 | bajo | ✅ |
| F-06 | `schema_inspector.py:25-30` × `dynamic_schema.py:132-137` | Mismo set de 11 tablas ×2 | 11 | bajo | ✅ |
| H13 | `core/prompts.py:310-318 × 405-413` | `role_focus` ×2 | 8 | bajo | ✅ |
| MUERTO-01 | `chat/services/query_service.ts:303-311` | `clearAllThreads` sin llamadores | 9 | bajo | ✅ |
| MUERTO-02 | `admin/AdminCatalogTab.tsx:9-16` | `AdminCatalogTabProps` nunca leída | 8 | bajo | ✅ |
| MUERTO-03 | `dashboard/ExecutiveReportView.tsx:14-38` | Props `copiedReport/onCopyReport` que nadie pasa | 12 | bajo | ✅ |
| MUERTO-04 | `dashboard/KPISection.tsx:353-356,521-522` | Prop `idx` nunca usada | 3 | bajo | ✅ |
| MUERTO-05 | 4 archivos (icons) | Iconos importados y no renderizados | 5 | bajo | ✅ |
| DUP-01 | `report_service.ts:107,144`, `audit_service.ts:17`, `DataGridTable.tsx:212` | Descarga blob ×4 | ~39 | bajo | ✅ |
| DUP-03 | `bar/line/pie/extra ChartConfig.ts` | `formatTooltipHtml` ×4 | 30 | bajo | ✅ |
| CI-01 | `ci.yml` (job typecheck) | `tsc` corre 2× (explícito y dentro de `build`) | — | bajo | ✅ |
| CI-02 | `ci.yml` jobs frontend | 2 jobs × `npm ci` → 1 job | — | bajo | ✅ |
| SC-R-02 | `scripts/check-extra-charts.mjs` + `package.json:18` | Harness propio → test vitest (cierra hueco de CI) | 51 | bajo | ✅ |
| DK-04 | `setup.ps1:155-158` | Comentario falso (fallback SQLite ya no existe en el código actual) → corregido; el bloque SE CONSERVA (imagen vieja posible: `up` sin `--build`) | 4 | bajo | ✅ (solo comentario) |
| F-01 | `governance_guard.py:14-159` | 3 métodos con ~90 líneas de boilerplate idéntico | 146 | medio | ✅ pase 18/10 |
| F-02 | `chat_engine/router.py:121-159 × 269-298` | Auditoría de `/query` y `/query/stream` copiada | 28 | bajo | ✅ pase 18/10 |
| H1 | `init_db.py:258-651` × `scripts/setup_postgres_full.py:355-599` | Doble seeder corriendo en cada arranque | 110-150 | medio | ✅ pase 19/10 |
| H4 | `reports/generator.py:98-174 × 176-235` | `export_pdf`/`export_excel` mismo procedimiento | 60 | medio | ✅ pase 18/10 |
| H5 | `init_db.py:204-256` | ALTER inline redundante con `ensure_schema_migrations` | 47 | bajo | ✅ pase 18/10 |
| H3 | 31 archivos de `backend/tests` | Fixture JWT re-escrita | 250-310 | bajo | ✅ pase 18/10 (24 archivos + `conftest`) |
| F-05 | `schema_inspector.py:53`, `engine.py:294`, `dynamic_schema.py:464`, `router.py:615`, `catalog_service.py:68` | Resolución de conexión ×5 | ~50 | medio | ✅ pase 19/10 |
| H12 | `auth/router.py:27-37` × `api/deps.py:175-176` | "Es admin" duplicado (seguridad) | 11 | medio | ✅ pase 18/10 |
| F-14 | `forecast_service.py:316-325` × `ml_service.py:399-408` | Mensajes idénticos | 10 | bajo | ✅ pase 18/10 |
| F-13 | `null_manager.py:246-287`, `null_handler.py:29` | Celda nula ×8 | 8 | bajo | ✅ pase 18/10 (solo unificación; la rama que escribe es plan 002) |
| F-11 | `sql_executor.py:79-81`, `engine.py:56` | Shim + alias solo-test | 4 | bajo | ✅ pase 18/10 |
| F-10 | `sql_executor.py:52-65` | Rama URL-string solo-test | 14 | medio | ✅ pase 18/10 |
| H15 | `test_traceability_defaults.py:41-65` | Solape con `test_reports_export` | 25 | bajo | ❎ (diferido con causa: el solape cubre superficies distintas; ver Fase 2) |
| DUP-02 | 11 componentes | Copiar+flash ×11 (9 estados) | ~40 | bajo | 🔜 F3 |
| DUP-04 | `bar/line/pie ChartConfig.ts` | `baseDark`+`dataZoom`+`markLine` ×3 | 70-90 | medio | 🔜 F3 |
| DUP-05 | `AdminCatalogTab:124`, `AdminPermissionsTab:126`, `AdminLearnedTab:74` | Selector de fuente ×3 | ~100 | medio | ✅ pase 19/10 (`ConnectionPills`) |
| DUP-06 | 3 tabs admin | Banner de error ×7 | ~56 | bajo | 🔜 F3 |
| DUP-08 | 36 sitios `src` | `err.response?.data?.detail \|\| ...` | 36 | bajo | ✅ pase 19/10 (33 sitios → `getApiErrorMessage`) |
| DUP-09 | `Header.tsx`, `AdminPage.tsx` | Nav ×8 + tabs ×6 | ~160 | medio | ❎ (no ejecutado: sin tests de render; ver Fase 3) |
| ESTADO-01 | `ChatPromptInput.tsx:31-36` | Auto-resize por efecto (¿CSS `field-sizing`?) | ~12 | medio | ❎ no-go (Baseline 2026-06-16: Chrome/Edge 123, Firefox 152, Safari 26.2; ver Fase 3) |
| ESTADO-02 | 11 componentes | 9 `copied*` + timers (cae con DUP-02) | ~30 | bajo | 🔜 F3 |
| DK-01 | `docker-compose.yml` × `docker-compose.partner.yml` | ~75 líneas espejo (ya divergieron) | ~75 | medio | 🔜 F3 |
| DK-02 | `backend/Dockerfile:6-10` | `gcc`+`libpq-dev` con wheels binarios (verificar build) | — | medio | 🔜 F3 |
| DK-03 | Dockerfile/CI/README/DOCS | Versión de Python en 4 fuentes | — | bajo | 🔜 F3 |
| DEP-BE-03 | `backend/pyproject.toml:5-19` | Metadata muerta + `readme` inexistente | ~14 | bajo | 🔜 F3 |
| DEP-BE-04 | `backend/Dockerfile:14-15` | pytest/hypothesis en imagen de prod | — | bajo | 🔜 F3 |
| DEP-BE-05 | `requirements.txt:18` | Techo `bcrypt<4.1.0` sin justificación | 1 | medio | 🔜 F3 |
| SC-R-01 | `scripts/init-db.js`, `run-backend.js` | ~40 líneas idénticas de detección de Python | ~130 | bajo | 🔜 F3 |
| BS-04 | `scripts/migrate_fix_rbac_permissions.py` | Migración sin referencias **sin filtro `granted_by_admin`** (riesgo latente) | 67 | medio | 🔜 F3 |
| BS-05 | `scripts/check_credential_health.py` | Diagnóstico sin referencias (solapa `rotate --dry-run`) | 45 | medio | 🔜 F3 |
| DOC-01 | `PARTE-DOCKER.md` | Huérfano; duplica secretos de README | 108 | bajo | 🔜 F3 |
| INV-1 | `ml_models.py:38,61,167-194` | `statsmodels`+`pandas`+`scipy` por 1 de 4 métodos | ~222 MB | alto | ✅ pase 19/10 |
| F-04 | `sql_generator.py:59,97` → UI | `thinking_process` muerto end-to-end | ~30 | medio | ✅ pase 19/10 |
| F-17 | `core/config.py:77` | `OPENAI_COMPATIBLE_API_KEY` sin lectores | 1 | bajo | ✅ pase 19/10 |
| F-16 | `null_manager.py:28` | `db` nunca usado | 1 | bajo | ✅ pase 19/10 |
| F-18 | `dynamic_schema.py:444` | `MagicMock` detectado en producción | 1 | medio | ✅ pase 19/10 (fixture `dynamic_schema_cache_clean`) |
| F-19/H16/GIGANTE-01..05 | 10 archivos (>600 líneas) | Responsabilidades mezcladas | — | alto | 🏗️ F5 |
| DUP-07 | 14 modales | Cáscara de overlay ×14 — **no hacer**: costo/beneficio malo (ya razonado en `useModalA11y`) | ~210 | alto | ❎ |
| MUERTO-06/07 | `types/index.ts:93-115`, 29 exports | Contrato tipado del payload / ruido inofensivo | — | — | ❎ (dejados a propósito) |
| (proyecciones) | `ProjectionsPage`/`estimate`, formatters | Ya evaluados en la auditoría previa; `estimate` aceptado salvo recorte futuro | — | — | ❎ |

## Anexo B — Decisiones de Fase 4 (resueltas el 19/10/2026; se conservan las recomendaciones originales como registro)

1. **`INV-1`** — `holt_damped` y sus 3 dependencias (222 MB instalados, import 3,14 s):
   ¿se quedan? Recomendación: sacar el método ahora (la doctrina del módulo es
   "gana el error medido, no la sofisticación") y reintroducirlo con evidencia.
2. **`F-04`** — borrar `thinking_process` del contrato (backend + tipo TS + JSX) o
   dejarlo como reserva explícita.
3. **`F-17`** — cablear `OPENAI_COMPATIBLE_API_KEY` (LM Studio/OpenAI-compat real)
   o borrar el setting.
4. **`F-18`** — `MagicMock` en `dynamic_schema`: la salida correcta es una fixture
   que limpie `_schema_cache`, pero toca tests con estado compartido.
