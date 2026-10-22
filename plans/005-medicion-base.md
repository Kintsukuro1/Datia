# 005 — Línea base de medición

**Fecha:** 21/10/2026
**HEAD:** `d3b8723` (árbol de trabajo sucio: cambios acumulados de sesiones
anteriores de este mismo trabajo, sin commits intermedios; ver `git status` — no
forman parte de esta medición)
**Alcance:** medir, no corregir. `pytest` no se ejecutó en este paso.

Comandos ejecutados desde la raíz salvo donde se indique `backend/`.

---

## 1. Knip (`npx knip`)

Salida: exit 1.

- Binarios no listados: **0**. `pytest` aparecía como falso positivo (no es un binario de Node; lo invoca un script de `package.json`) y se silenció con `ignoreBinaries`.
- Exports sin uso: **11**
- Tipos exportados sin uso: **11**
- Export duplicado: **1** (`App|default`, `src/App.tsx`)
- Avisos de configuración: **2** (`src/main.tsx` entry redundante porque Knip ya lo detecta; `.css` no se sigue con `project` solo TS/TSX)

Exports sin uso:

| Archivo:línea | Símbolo |
|---|---|
| `src/App.tsx:7` | `App` |
| `src/components/dashboard/executiveDashboardUtils.ts:9` | `computeChartStats` |
| `src/components/dashboard/executiveDashboardUtils.ts:11` | `formatMetricNumber` |
| `src/constants/index.ts:63` | `PROVIDER_LABELS` |
| `src/features/admin/hooks/useAdminPermissions.ts:22` | `permissionKey` |
| `src/features/chat/hooks/useChatEngine.tsx:17` | `LONG_WAIT_NOTICE_SECONDS` |
| `src/features/dashboard/components/assistant/AssistantMarkdownBody.tsx:49` | `parseMarkdownContent` |
| `src/features/dashboard/components/assistant/AssistantMarkdownBody.tsx:156` | `formatInlineMarkdown` |
| `src/features/dashboard/components/charts/theme.ts:98` | `TOOLTIP_CSS` |
| `src/features/dashboard/components/charts/theme.ts:118` | `buildTooltipHtml` |
| `src/hooks/useSystemHealth.ts:7` | `DEFAULT_POLLING_INTERVAL_MS` |

Tipos exportados sin uso:

| Archivo:línea | Símbolo |
|---|---|
| `src/components/dashboard/executiveDashboardUtils.ts:13` | `ColorTheme` |
| `src/components/dashboard/executiveDashboardUtils.ts:14` | `ChartStats` |
| `src/features/admin/services/catalog_service.ts:74` | `NullColumnInfo` |
| `src/features/admin/services/catalog_service.ts:81` | `NullTableInfo` |
| `src/features/chat/hooks/useChatEngine.tsx:74` | `FullThread` |
| `src/features/dashboard/components/assistant/AssistantMarkdownBody.tsx:5` | `TableData` |
| `src/features/dashboard/components/assistant/AssistantMarkdownBody.tsx:11` | `MarkdownSection` |
| `src/features/projections/services/projection_service.ts:13` | `ProjectionStep` |
| `src/hooks/useSystemHealth.ts:9` | `SystemHealthStatus` |
| `src/types/index.ts:86` | `ExecutiveReport` |
| `src/types/index.ts:105` | `PresentationHints` |

Configuración usada: `knip.json` con `entry` = `src/main.tsx` + `src/__tests__/**/*.test.ts`,
`project` = `src/**/*.{ts,tsx}` e `ignoreBinaries` = `["pytest"]`. No se silenció
ningún export. Los dos avisos de configuración se dejan visibles: `src/main.tsx`
es redundante porque Knip ya lo detecta, y el `.css` no se sigue con un `project`
limitado a TS/TSX.

---

## 2. Build (`npm run build`)

Exit 0, `tsc && vite build`. 2264 módulos, 10,68 s. Chunks principales:

| Chunk | Tamaño | Gzip |
|---|---:|---:|
| `echarts-B_uk2IZ3.js` | **1.053,95 kB** | 349,87 kB |
| `AdminPage-DxYZx9to.js` | 208,49 kB | 43,88 kB |
| `vendor-DY0Shy9b.js` | 180,03 kB | 59,29 kB |
| `ChatDashboardPage-BQ84E0M-.js` | 148,34 kB | 35,28 kB |
| `index-M75ImsI-.js` | 91,78 kB | 25,29 kB |
| `index-D28n2Yz2.css` | 94,34 kB | 15,28 kB |

Vite avisa de chunks >500 kB; el único que lo supera es `echarts`.
El warning es el esperado según `vite.config.ts`.

---

## 3. Ruff C901 (`ruff check app --select C901`)

Config: `backend/ruff.toml`, `mccabe max-complexity = 15`, línea 100.
Salida: **24 funciones** por encima del umbral de complejidad (exit 1).

| Función | Archivo:línea | Complejidad |
|---|---|---:|
| `validate_and_secure_sql` | `chat_engine/ast_validator.py:97` | 90 |
| `execute_query` | `chat_engine/engine.py:171` | 59 |
| `get_authorized_schema_prompt` | `chat_engine/dynamic_schema.py:424` | 48 |
| `auto_enrich_catalog` | `catalog/services/catalog_enricher.py:524` | 37 |
| `ensure_schema_migrations` | `core/database.py:37` | 34 |
| `init_db` | `db/init_db.py:195` | 34 |
| `generate_excel` | `reports/excel_exporter.py:18` | 37 |
| `heuristic_enrich` | `catalog/services/catalog_enricher.py:86` | 24 |
| `rank_relevant_tables` | `chat_engine/dynamic_schema.py:340` | 24 |
| `generate_completion` | `chat_engine/llm_service.py:398` | 23 |
| `generate_pdf` | `reports/pdf_exporter.py:25` | 22 |
| `infer_postgres_type` | `admin_catalog/importers/postgres_importer.py:9` | 21 |
| `check_llm_connectivity` | `system/health_service.py:44` | 21 |
| `get_dynamic_suggestions` | `chat_engine/suggestions_service.py:58` | 20 |
| `admin_update_user` | `auth/router.py:451` | 19 |
| `apply_in_memory_remediation` | `catalog/services/null_manager.py:287` | 19 |
| `get_system_anomalies` | `system/router.py:162` | 19 |
| `get_current_user` | `api/deps.py:67` | 17 |
| `apply_null_policy` | `catalog/services/null_manager.py:111` | 17 |
| `cast_postgres_val` | `admin_catalog/importers/postgres_importer.py:85` | 16 |
| `_enrich_columns_with_llm` | `catalog/services/catalog_enricher.py:445` | 16 |
| `_render_table_as_ddl` | `chat_engine/dynamic_schema.py:234` | 16 |
| `stream_completion` | `chat_engine/llm_service.py:558` | 16 |
| `detect_statistical_anomalies` | `chat_engine/kpi_calculator.py:713` | 16 |

### Medición extra: longitud de funciones (>80 líneas)

C901 mide complejidad, no longitud. Con un script AST de una pasada sobre
`backend/app` (sin tocar el repo) salen **exactamente 44 funciones de más de
80 líneas**, que coincide con la expectativa del encargo.

| Líneas | Función | Archivo:línea |
|---:|---|---|
| 489 | `execute_query` | `chat_engine/engine.py:171` |
| 473 | `init_db` | `db/init_db.py:195` |
| 447 | `validate_and_secure_sql` | `chat_engine/ast_validator.py:97` |
| 340 | `generate_pdf` | `reports/pdf_exporter.py:25` |
| 258 | `get_authorized_schema_prompt` | `chat_engine/dynamic_schema.py:424` |
| 258 | `auto_enrich_catalog` | `catalog/services/catalog_enricher.py:524` |
| 255 | `get_system_anomalies` | `system/router.py:162` |
| 234 | `generate_excel` | `reports/excel_exporter.py:18` |
| 224 | `run_prediction` | `chat_engine/router.py:387` |
| 209 | `build_dynamic_visualization` | `chat_engine/kpi_calculator.py:145` |
| 193 | `upload_database_file` | `catalog/services/connector_service.py:269` |
| 174 | `apply_null_policy` | `catalog/services/null_manager.py:111` |
| 171 | `admin_update_user` | `auth/router.py:451` |
| 167 | `ensure_schema_migrations` | `core/database.py:37` |
| 158 | `_rebuild_shared_results` | `chat_engine/router.py:713` |
| 158 | `generate_completion` | `chat_engine/llm_service.py:398` |
| 148 | `check_llm_connectivity` | `system/health_service.py:44` |
| 148 | `process_chat_query_stream` | `chat_engine/router.py:225` |
| 146 | `_migrate_role_aliases` | `db/init_db.py:47` |
| 142 | `execute_with_self_healing` | `chat_engine/sql_executor.py:76` |
| 141 | `run_ml_projection` | `ml_forecast/router.py:97` |
| 128 | `generate_unified_synthesis_with_llm` | `chat_engine/kpi_calculator.py:572` |
| 128 | `audit_data_quality` | `chat_engine/forecast_service.py:441` |
| 122 | `get_system_health` | `system/router.py:37` |
| 122 | `build_visibility_diagnostic_response` | `chat_engine/response_builder.py:93` |
| 117 | `get_data_dictionary` | `catalog/services/catalog_service.py:148` |
| 116 | `build_projection` | `ml_forecast/ml_models.py:288` |
| 115 | `heuristic_enrich` | `catalog/services/catalog_enricher.py:86` |
| 111 | `login_user` | `auth/router.py:160` |
| 106 | `get_governance_coverage` | `admin_catalog/router.py:354` |
| 104 | `get_dynamic_suggestions` | `chat_engine/suggestions_service.py:58` |
| 104 | `introspect_connection_metadata` | `catalog/services/schema_inspector.py:85` |
| 100 | `stream_completion` | `chat_engine/llm_service.py:558` |
| 93 | `generate_candidate_sql` | `chat_engine/sql_generator.py:21` |
| 90 | `get_current_user` | `api/deps.py:67` |
| 89 | `test_llm_completion` | `chat_engine/llm_diagnostic_router.py:118` |
| 88 | `detect_statistical_anomalies` | `chat_engine/kpi_calculator.py:713` |
| 88 | `delete_connector` | `catalog/services/connector_service.py:525` |
| 87 | `get_shared_chat_thread` | `chat_engine/router.py:925` |
| 87 | `submit_query_feedback` | `chat_engine/router.py:1152` |
| 87 | `_render_table_as_ddl` | `chat_engine/dynamic_schema.py:234` |
| 86 | `get_dynamic_suggestions` | `chat_engine/router.py:614` |
| 86 | `check_domain_governance` | `chat_engine/governance_guard.py:139` |
| 82 | `rank_relevant_tables` | `chat_engine/dynamic_schema.py:340` |

---

## 4. Vulture (`vulture app --min-confidence 80`)

Salida: exit 1, **9 hallazgos**.

| Archivo:línea | Hallazgo | Lectura |
|---|---|---|
| `app/api/deps.py:7-10` | `unused import 'app'` (4) | Falso positivo: imports por efecto secundario que registran los modelos SQLAlchemy. |
| `app/db/init_db.py:1-4` | `unused import 'app'` (4) | Mismo caso: registro de modelos, no imports muertos. |
| `app/modules/admin_catalog/importers/postgres_importer.py:341` | `unused variable 'sql_path'` | Stub deliberado de una función removida por seguridad (ejecutar SQL arbitrario); el parámetro queda por compatibilidad de firma. |

No se creó `vulture_whitelist.py`: los 8 falsos positivos son imports por efecto
secundario, ninguno de routers/Pydantic/fixtures, y el whitelist no cambiaría
el comando de medición documentado.

---

## 5. `any` en `src/` (sin tests)

**107 líneas** con `any`: 93 usan `: any` y 15 usan `as any`
(una línea tiene ambos). Repartidas en 37 archivos.

Top por archivo:

| Ocurrencias | Archivo |
|---:|---|
| 11 | `src/pages/ChatDashboardPage.tsx` |
| 10 | `src/features/chat/services/query_service.ts` |
| 10 | `src/shared/api/api_client.ts` |
| 6 | `src/features/admin/hooks/useAdminCatalog.ts` |
| 5 | `src/features/admin/hooks/useAdminPermissions.ts` |
| 5 | `src/components/admin/DatabaseWizardModal.tsx` |
| 5 | `src/features/dashboard/components/charts/extraChartConfig.ts` |

---

## 6. Tokens Tailwind: `dark-` vs `app-` (sin tests)

| Prefijo | Ocurrencias | Archivos |
|---|---:|---:|
| `dark-` | 214 | 35 |
| `app-` | 477 | 25 |
| ambos en el mismo archivo | — | 11 |

Archivos con ambos prefijos (duplicación de tokens):

| `dark-`/`app-` | Archivo |
|---:|---|
| 10/2 | `src/components/admin/AdminLearnedTab.tsx` |
| 8/7 | `src/components/admin/CatalogAddModal.tsx` |
| 6/4 | `src/components/admin/CatalogEditModal.tsx` |
| 9/10 | `src/components/admin/UserAddModal.tsx` |
| 7/3 | `src/components/admin/UserEditModal.tsx` |
| 6/5 | `src/components/admin/UserPasswordResetModal.tsx` |
| 7/4 | `src/components/admin/UserSessionsModal.tsx` |
| 1/13 | `src/components/chat/ChatMessageItem.tsx` |
| 1/3 | `src/components/chat/ChatPromptInput.tsx` |
| 10/6 | `src/components/traceability/TraceabilityModal.tsx` |
| 1/33 | `src/pages/ChatDashboardPage.tsx` |

La medición es textual sobre `.ts`/`.tsx`; no distingue clases de otros usos de
esas cadenas. Sirve como orden de magnitud, no como inventario exacto.

---

## 7. Lo que no se midió

- `pytest` completo: fuera del alcance de este paso; tarda ~8 min.
- `npm test` (vitest): no se ejecutó en este paso.
- `npm run analyze` (vite-bundle-visualizer): abre un servidor interactivo; no
  se ejecutó para no dejar un proceso colgado.

---

## 8. Nota sobre el paso 7.3 del encargo de entorno

Ese paso pedía `git diff --stat -- backend/app src` vacío. No podía estarlo: el
árbol arrastra cambios acumulados de sesiones anteriores de este mismo trabajo,
sin commits intermedios que los separen. Lo que sí quedó verificado es el delta
de esa sesión: **0 escrituras** a `backend/app/` o `src/` (15 `write`/`edit`,
todas a configuración, planes o agentes) y stat idéntico al inicio y al cierre.

Mientras no haya commits, el check no se puede cumplir en su forma literal. Para
los pasos siguientes, comparar los hashes de `git diff -- backend/app src` y de
`git status --porcelain -- backend/app src` antes y después, en vez de exigir
que el diff esté vacío.
