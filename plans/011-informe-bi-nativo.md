# Plan 011 — Informe BI y Paneles nativos (reemplazo del panel PHP)

**Fecha:** 17/11/2026
**Estado:** ✅ Implementado y verificado (17/11/2026)
**Objetivo:** reemplazar el panel BI que el compañero implementó en PHP por un
módulo nativo de Dat.ia: FastAPI para los datos y React/TypeScript para la
interfaz. Nada de PHP, nada de iframe, nada de un segundo servidor. El
resultado debe conservar la experiencia (informe con pestañas, filtros, KPIs,
matriz diaria, detalle y paneles guardados) y respetar los cuatro pilares del
producto.

**Referencia analizada:** carpeta entregada por el compañero, con `power bi/`
(PHP), `PowerBIPage.tsx`, `PanelsPage.tsx`, `bi_service.ts` y
`backend/app/modules/bi_panels/`. Esa carpeta **no forma parte de este repo**;
es una entrada de referencia y no se copia.

---

## 1. Qué hay hoy y qué se decide

### 1.1 Inventario de la entrega de referencia

| Artefacto | Qué hace | Decisión |
|---|---|---|
| `power bi/config.php` | Lee el `.env`, resuelve PostgreSQL/SQLite, la clave Fernet, columnas ocultas y límites. | **No se porta.** Esa configuración ya vive en `app/core/config.py` y en el engine de conectores. |
| `power bi/api.php` | Valida la sesión contra Datia, calcula permisos, lee el esquema, arma el panel y ejecuta SQL con PDO. | **Se reimplementa** en `backend/app/modules/bi/` usando la gobernanza, el `ASTValidator`, el `SQLExecutor` y la auditoría que ya existen. |
| `power bi/index.php` | Interfaz del informe: 5 pestañas, filtros, KPIs, gráficos Chart.js, matriz diaria y detalle. | **Se reimplementa** como página React nativa (`/powerbi`). |
| `power bi/vendor/chart*.js` | Chart.js y su plugin de etiquetas copiados a mano. | **No se usan.** Dat.ia ya tiene ECharts instalado y modularizado. |
| `PowerBIPage.tsx` | Iframe que apunta al PHP y le pasa el token por `postMessage`. | **Se elimina.** La página pasa a llamar a la API con el `Authorization: Bearer` normal. |
| `PanelsPage.tsx` | Constructor de visuales y paneles guardados con ECharts. | **Se reutiliza como referencia visual y se adapta** a los tokens, servicios y `WidgetChart` actuales. |
| `bi_service.ts` | Cliente HTTP de `/bi/*`. | **Se reutiliza la forma del contrato**, con los nombres y el manejo de errores actuales. |
| `bi_panels/router.py` | Endpoints de esquema, consulta y visuales guardados. | **Base de diseño, no se copia tal cual.** Usa `resolve_db_path` (no existe hoy), acepta SQLite (hoy no existe) y no aplica las reglas de enmascarado actuales. |
| `bi_panels/models.py` | Tabla `bi_visuals` para guardar la definición de un visual. | **Se conserva la idea** con `utcnow_naive`, FK a la conexión y cascade. |
| `bi_panels/path_repair.py` | Repara rutas absolutas de SQLite copiadas de otro PC. | **No se porta.** Hoy las cargas se convierten a PostgreSQL y `DatabaseType` solo admite PostgreSQL. |
| `exportar_powerbi.py` | Exporta tablas SQLite a CSV para abrirlas en Power BI de Microsoft. | **Fuera de alcance.** No aplica RBAC, no enmascara columnas y exporta datos de cliente. Si se necesita, va en un plan aparte con permisos y auditoría. |
| `INSTALAR_POWERBI_PANELES.txt` | Instrucciones para levantar PHP en el puerto 8090. | **No se porta.** Su modelo operativo (dos servidores, token por URL/mensaje) desaparece. |

### 1.2 Por qué no se copia lo que ya existe en la entrega

- **Dos servidores y dos sesiones.** El PHP abre su propio proceso, lee el
  `.env` y descifra credenciales por su cuenta. Eso duplica la superficie de
  seguridad y hace que un cambio de `FERNET_KEY` o de gobernanza tenga dos
  lugares que mantener.
- **Gobernanza duplicada.** `api.php` consulta `role_table_permissions` y
  `role_column_permissions` a mano. La versión nativa usa
  `DynamicSchemaPruningService.get_authorized_schema_prompt`, que es la misma
  fuente que usa el chat y que ya tiene caché e invalidación.
- **Sin trazabilidad real.** El PHP no escribe `audit_logs`. La versión nativa
  audita cada informe y cada visual consultado.
- **SQLite no existe hoy.** El proyecto actual solo soporta PostgreSQL
  (`DatabaseType.POSTGRESQL`) y convierte las cargas a PostgreSQL. Portar la
  rama SQLite sería mantener un motor que el producto ya no usa.
- **El código de referencia no arranca tal cual.** `bi_panels/router.py` llama
  `DynamicSchemaPruningService.resolve_db_path` y
  `get_physical_table_columns(..., db_path=...)`, que no existen con esa firma
  en el árbol actual. `models.py` usa `datetime.utcnow`, que el repo ya
  reemplazó por `utcnow_naive`.
- **Bundle y estilo.** `PanelsPage.tsx` importa `echarts-for-react` completo;
  el repo usa `WidgetChart` + `shared/echarts` para no duplicar el chunk de
  ECharts. Los colores y clases del PHP son una copia manual del tema; la
  versión nativa usa los tokens de `globals.css` y `tailwind.config.js`.

### 1.3 Qué se conserva de la experiencia

- Informe automático sobre cualquier base incorporada: elegir base, tabla,
  columna de fecha, grupo, categoría y medida.
- Pestañas: **Resumen general**, **Detalle diario**, **Distribución**,
  **Datos** y **Guía de lectura**.
- Filtros de año, mes (`Todos` incluido), valor de grupo y valor de categoría.
- KPIs: registros, total de la medida, grupos, días con datos, promedio diario
  y promedio por grupo.
- Evolución diaria o mensual, barras por grupo, torta por categoría, cruce
  grupo × categoría y matriz grupo × día con porcentaje de participación.
- Selección automática de fecha, grupo y categoría, con override manual.
- Estado en la URL para que el enlace sea compartible.
- Botón **Power BI** en cada respuesta del chat, que abre el informe en la
  base y la tabla de esa consulta.
- Paneles guardados por usuario, con constructor de visuales y recálculo en
  vivo.

---

## 2. Decisiones de diseño

1. **Un solo módulo backend:** `backend/app/modules/bi/`. No se resucita
   `bi_panels` porque su contrato y sus dependencias no coinciden con el árbol
   actual.
2. **Una sola página de informe nativa:** `/powerbi` (se conserva la ruta y la
   etiqueta para no romper enlaces ni la costumbre del usuario). La página se
   llama `BIReportPage` y no usa iframe.
3. **Paneles guardados en `/panels`:** reutiliza el constructor de la entrega,
   adaptado a `WidgetChart`, a la API actual y a las reglas de enmascarado.
4. **Solo PostgreSQL.** Si una conexión no es PostgreSQL, el endpoint responde
   400 con un mensaje claro. No se porta la rama SQLite.
5. **Toda consulta pasa por el pipeline existente:**
   `get_authorized_schema_prompt` → construcción con identificadores validados
   → `ASTValidator.validate_and_secure_sql` → `SQLExecutor.execute_raw_sql`.
   No hay un `execute` alternativo ni un atajo para admin.
6. **Auditoría obligatoria.** El informe y los visuales son caminos que
   devuelven datos de cliente: usan `_persist_audit_log(..., required=True)`.
   Si la auditoría no se puede guardar, no se devuelven datos.
7. **Un registro de auditoría por informe, no diez.** El informe ejecuta varias
   consultas (años, meses, KPIs, series, matriz, cruce y detalle). Se guarda un
   solo `AuditLog` con el conjunto de SQL ejecutados concatenado, el estado
   final y las filas del detalle. Los visuales guardados, que ejecutan una sola
   consulta, generan un registro por consulta.
8. **Reglas de columnas:**
   - `BLOCKED`: no aparece en el esquema ni en ninguna consulta. El
     `ASTValidator` la rechaza si alguien intenta colarla.
   - `MASKED`: en el informe automático no se ofrece como campo a un
     no-admin; en los paneles se ofrece marcada, solo para
     `count`/`count_distinct`, sin filtros y con la etiqueta enmascarada. El
     administrador las ve en claro, igual que en el chat.
   - `HIDDEN_COLUMNS` (`password`, `hashed_password`, `encrypted_password`,
     `token`, `api_key`, `secret`, `secret_key`): se ocultan incluso al
     administrador. Es defensa en profundidad, no una política RBAC.
9. **Sin dependencias nuevas.** ECharts, `echarts-for-react`, `lucide-react`,
   `sqlglot`, Pydantic y SQLAlchemy ya están instalados.
10. **Compatibilidad de enlaces del chat:** el botón del chat navega a
    `/powerbi?conn=ID&table=TABLA`. La página traduce `conn`/`table` a los
    parámetros del backend y conserva el resto del estado en la URL.
11. **El cruce debe cuadrar.** El PHP mostraba solo las 12 categorías
    principales pero sumaba todas las celdas en el total de la fila; con más
    de 12 categorías, el total no coincidía con lo visible. La versión nativa
    agrupa el resto en **Otros** y calcula el total de la fila sobre las
    columnas visibles (incluida Otros). Es una corrección, no una copia.

---

## 3. Contrato de API

Prefijo real: `/api/v1/bi`. Todas las rutas exigen `Authorization: Bearer`.

### 3.1 Informe

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/bi/connections` | Bases que el usuario puede usar. Un no-admin no ve una base si no tiene al menos una tabla permitida. |
| `GET` | `/bi/schema?connection_id=ID` | Tablas y columnas visibles para este usuario, con `kind`, `masked` e `is_admin`. |
| `GET` | `/bi/report` | Todos los números del informe. Parámetros: `connection_id`, `table`, `date_column`, `group_column`, `category_column`, `measure`, `year`, `month`, `group_value`, `category_value`. |

Respuesta de `/bi/report` (campos principales):

```json
{
  "connection": {"id": 1, "name": "Demo"},
  "table": {"name": "fact_ventas", "columns": [{"name": "fecha", "type": "date", "kind": "date", "masked": false}]},
  "roles": {"date": "fecha", "group": "ejecutivo", "category": "categoria", "measure": "monto"},
  "period": {"years": [2025, 2026], "months": [1, 2], "year": 2026, "month": 1},
  "options": {"group": ["Ana", "Luis"], "category": ["Software", "Servicios"]},
  "kpis": {
    "registros": 120,
    "total": 4500000,
    "grupos": 4,
    "dias": 20,
    "promedio_diario": 225000,
    "promedio_por_grupo": 1125000
  },
  "by_group": [{"label": "Ana", "value": 1800000}],
  "by_category": [{"label": "Software", "value": 2500000}],
  "daily": [{"label": "2026-01-02", "value": 300000}],
  "matrix": {"days": ["2026-01-02"], "rows": [{"label": "Ana", "cells": {"2026-01-02": 300000}, "total": 300000}]},
  "cross": {"categories": ["Software"], "rows": [{"label": "Ana", "cells": {"Software": 300000}, "total": 300000}]},
  "detail": {"columns": ["fecha", "ejecutivo", "categoria", "monto"], "rows": []},
  "audit_log_id": 123
}
```

Reglas del contrato:

- `date_column` vacío significa "automática"; `-` significa "sin fecha".
- `group_column`/`category_column` vacíos significan "automática".
- `measure` vacío o `count` significa "cantidad de registros"; cualquier otro
  valor debe ser una columna numérica visible y no enmascarada.
- `month=all` significa "todo el año"; ausente significa "último mes con datos
  del año elegido".
- `matrix` y `cross` son `null` cuando faltan las columnas que los componen.
- `detail.rows` nunca incluye columnas `BLOCKED`, `MASKED` ni `HIDDEN_COLUMNS`
  para un no-admin; para el admin, nunca incluye `HIDDEN_COLUMNS`.
- Límites: detalle 200 filas (`MAX_DETAIL_ROWS`), grupos 25, categorías 12,
  techo del guardarraíl `settings.DEFAULT_ROW_LIMIT` (500).

### 3.2 Visuales guardados

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/bi/query` | Vista previa de un visual sin guardarlo. |
| `GET` | `/bi/visuals?panel_name=` | Visuales del usuario actual. |
| `POST` | `/bi/visuals` | Guarda un visual (ejecuta la consulta antes de guardar). |
| `POST` | `/bi/visuals/{id}/data` | Recalcula un visual guardado con los datos y permisos de hoy. |
| `DELETE` | `/bi/visuals/{id}` | Borra un visual del usuario actual. |

`BIVisualSpec` conserva la forma de la entrega: `connection_id`, `table`,
`chart_type` (`bar`, `hbar`, `line`, `area`, `pie`, `kpi`, `table`),
`dimension`, `date_grain` (`day`, `month`, `year`), `measure`, `aggregation`
(`sum`, `avg`, `count`, `count_distinct`, `min`, `max`), `filters`, `sort`,
`limit`.

---

## 4. Fases de ejecución

### Fase 0 — Línea base y deriva

**Archivos:** ninguno (solo lectura).

1. `git rev-parse --short HEAD` y `git status --short`.
2. `cd backend; venv\Scripts\python.exe -m pytest -q` y anotar el total real.
3. `npx tsc --noEmit`, `npm test`, `npm run build`.
4. Confirmar que no hay `*.php` ni `bi_panels` en el árbol actual.

**Criterio de salida:** números de línea base copiados al informe de ejecución.
No se declara ninguna cifra de este plan como verificada sin esa salida.

### Fase 1 — Backend del informe

**Archivos nuevos:**
- `backend/app/modules/bi/__init__.py`
- `backend/app/modules/bi/models.py`
- `backend/app/modules/bi/schemas.py`
- `backend/app/modules/bi/governance.py`
- `backend/app/modules/bi/sql_builder.py`
- `backend/app/modules/bi/report_service.py`
- `backend/app/modules/bi/router.py`
- `backend/tests/test_bi_schema_api.py`
- `backend/tests/test_bi_report_api.py`
- `backend/tests/test_bi_security.py`

**Archivos existentes que se tocan:**
- `backend/app/api/v1/router.py` — registrar `bi_router` con prefijo `/bi`.
- `backend/app/db/init_db.py` — importar `app.modules.bi.models` antes de
  `create_all`.

**No tocar:** `chat_engine/*`, `admin_catalog/*`, `catalog/*`, `auth/*`.

**Tareas:**

1. `governance.py`
   - Resolver la conexión con `SchemaInspector.resolve_connection` o una
     consulta equivalente; un `connection_id` explícito inexistente es 404.
   - Exigir rol con `require_assigned_role`.
   - Resolver `is_admin = is_admin_user(user) or role_name in ADMIN_ROLES`,
     con el mismo criterio que el motor del chat.
   - Obtener `allowed_tables`, `blocked_columns`, `masked_columns`,
     `table_columns` y `table_labels` de
     `DynamicSchemaPruningService.get_authorized_schema_prompt(db=..., role_id=...,
     user_role=..., connection_id=..., is_admin=...)`.
   - Aplicar `HIDDEN_COLUMNS` y construir la lista de tablas/columnas visibles
     con `kind` (`date`/`number`/`text`) y `masked`.
   - Intersecar con `get_physical_db_tables`: una tabla que solo existe en el
     catálogo semántico no se ofrece, porque la consulta fallaría.
   - Para un no-admin, una base sin tablas permitidas no aparece en
     `/bi/connections`; con `connection_id` directo, 403.
2. `sql_builder.py`
   - Helpers `quote_ident`, `literal`, `date_part_expr`, `date_bucket_expr`.
     Usar `sqlglot.exp.to_identifier` y `sqlglot.exp.Literal`; nunca interpolar
     valores del navegador.
   - Construir el SELECT del informe y el de un visual. Solo PostgreSQL.
   - Validar que la tabla y cada columna existan en el esquema autorizado,
     comparando sin distinguir mayúsculas y usando el nombre físico real.
   - Para un no-admin, rechazar con 403 una columna `MASKED` pedida como
     fecha, grupo, categoría o medida. El admin puede usarla como cualquier
     columna visible.
   - Construir filtros con `CAST(col AS TEXT) = <literal>` y período con
     `EXTRACT(YEAR|MONTH FROM CAST(col AS DATE))`.
   - `detail` con `ORDER BY` de fecha descendente y `LIMIT 200`.
3. `report_service.py`
   - Selección automática de fecha/grupo/categoría con la heurística del PHP:
     tipos, nombres de período y cardinalidades (`COUNT(DISTINCT ...)`), con
     un máximo de 12 columnas candidatas. Si la consulta de cardinalidades
     falla, responder 400 con un mensaje que pida elegir las columnas a mano;
     no se inventan columnas.
   - Ejecutar cada SQL por separado con `SQLExecutor.execute_raw_sql` y
     `ASTValidator` previo. La conexión es siempre `CorporateConnection`.
   - Pivotar matriz y cruce en Python; ordenar como el panel original.
   - En el cruce, usar las 12 categorías principales y agrupar el resto en
     **Otros**; el total de cada fila debe ser la suma de las celdas visibles
     (incluida Otros), no de todas las categorías ocultas.
   - Enmascarar la etiqueta si la dimensión es `MASKED` (solo aplica a
     paneles; el informe no ofrece esas columnas a un no-admin y, para el
     admin, las trata como columnas normales).
   - Escribir un único `AuditLog` con `required=True` después de completar
     todas las consultas. En error de validación, `RECHAZADO_RBAC`; en error
     de ejecución, `ERROR_EJECUCION`; en éxito, `APROBADO`.
4. `router.py`
   - Endpoints de la sección 3.1.
   - Convertir `AuditWriteError` en 503 con un mensaje que explique que no se
     devuelven datos sin trazabilidad.
   - No reauditar el 403 de `require_assigned_role`: ese helper ya deja
     `RECHAZADO_RBAC`.
   - Devolver `audit_log_id`.
5. `schemas.py`
   - Pydantic con límites (`table`/columnas `max_length=128`, `limit` 1–500,
     filtros máximo 5).
   - `BISchemaResponse` incluye `is_admin` para que el frontend decida si
     ofrece columnas `MASKED` en el informe.
   - `BIVisual*` de la sección 3.2 (se completa en Fase 3).
6. Tests:
   - Base PostgreSQL desechable con `create_temp_pg_database`; tabla
     `bi_ventas` con dos años, dos meses, 4 ejecutivos y 2 categorías.
   - Rol con permiso a la tabla, columna `rut_cliente` `BLOCKED` y
     `monto` `MASKED`.
   - Informe: KPIs, período por defecto, filtros de año/mes/grupo/categoría,
     medida `sum`, sin fecha, sin grupo/categoría, tabla inexistente 404,
     medida inválida 400.
   - Cruce con más de 12 categorías: aparece **Otros** y el total de cada fila
     coincide con la suma de las celdas visibles.
   - Esquema: blocked ausente, hidden ausente, masked marcada, no-admin sin
     tablas -> 403, admin ve las tablas físicas.
   - Conexiones: no-admin no ve bases sin permisos.
   - Auditoría: una fila `APROBADO` por informe; `RECHAZADO_RBAC` sin rol;
     `AuditWriteError` -> 503 y sin cuerpo de datos.
   - Seguridad: tabla/columna inyectada no se ejecuta; valor `' OR 1=1 --`
     queda como literal; solo SELECT; el detalle no trae `rut_cliente` ni
     `monto` a un no-admin.

**Criterio de salida:** endpoints funcionando contra la base de prueba y tests
de Fase 1 verdes.

### Fase 2 — Frontend del informe

**Archivos nuevos:**
- `src/features/bi/types.ts`
- `src/features/bi/services/bi_service.ts`
- `src/features/bi/report_logic.ts` (helpers puros)
- `src/features/bi/components/BIChart.tsx`
- `src/features/bi/components/ReportKpis.tsx`
- `src/features/bi/components/ReportSections.tsx`
- `src/pages/BIReportPage.tsx`
- `src/__tests__/bi_report_logic.test.ts`
- `src/__tests__/bi_service.test.ts`

**Archivos existentes que se tocan:**
- `src/app/router/AppRouter.tsx` — ruta lazy `/powerbi`, título "Power BI".
- `src/shared/layout/Header.tsx` — botón "Power BI" (desktop y móvil).
- `src/components/chat/ChatMessageItem.tsx` — botón "Power BI" junto a SQL.

**No tocar:** `ChatDashboardPage` salvo el botón; backend.

**Tareas:**

1. `bi_service.ts` con `apiClient` y `getApiErrorMessage`; timeout explícito
   de 60 s para `/bi/report` (varias consultas) y 30 s para el resto.
2. `BIReportPage`:
   - Barra de origen: base, tabla, fecha, grupo, categoría y medida.
   - Slicers: año, mes, valor de grupo y valor de categoría.
   - 5 pestañas con `role="tablist"`/`role="tabpanel"`, `aria-selected` y
     `hidden`; tablas con `caption`, `scope="col"` y contenedor enfocable.
   - KPIs, línea de evolución, barras, torta, cruce, matriz diaria, detalle y
     guía contextual.
   - Si `schema.is_admin` es `false`, no ofrecer columnas `MASKED` como fecha,
     grupo, categoría ni medida; si es `true`, ofrecerlas como columnas
     normales.
   - Estados de carga, vacío y error con reintento.
   - Estado en la URL (`conn`, `table`, `date`, `group`, `cat`, `measure`,
     `year`, `month`, `gv`, `cv`, `tab`), compatible con el botón del chat.
   - `data-testid` estables para selects, pestañas y tablas (`bi-connection`,
     `bi-table`, `bi-tab-resumen`, `bi-kpis`, `bi-detail`).
   - Tema claro/oscuro por tokens; sin `postMessage` ni `localStorage` de
     token para el iframe (ya no hay iframe).
3. `report_logic.ts` con funciones puras: formateo es-CL, etiqueta de período,
   día/mes, porcentaje de participación, flechas, pivote de matriz y cruce,
   serialización/lectura de URL y `buildPowerBiUrl(result)` para el botón del
   chat. Es lo que se testea con Vitest (entorno node).
4. `BIChart` usa `WidgetChart` (ECharts modular) o `ReactEChartsCore` con
   `shared/echarts`; **no** importar `echarts-for-react` completo.
5. Tests:
   - `readReportState`/`buildReportParams` con y sin `conn`; `month=all`.
   - `formatPeriod`, `dayLabel`, `participationPct`, `arrowFor`.
   - Matriz y cruce con celdas faltantes (no confundir ausente con 0).
   - `buildPowerBiUrl` con y sin `connection_id`/tabla.
   - Rutas de `biService` y parámetros enviados.
6. Accesibilidad: labels asociados, `aria-live` para el estado de carga,
   targets de 24 px, sin `window.prompt` para crear paneles (campo en línea).

**Criterio de salida:** `/powerbi` renderiza el informe con datos demo, el
botón del chat abre base/tabla correctas y los tests de lógica pasan.

### Fase 3 — Paneles guardados

**Archivos nuevos:**
- `backend/app/modules/bi/visual_service.py`
- `backend/tests/test_bi_visuals_api.py`
- `src/pages/PanelsPage.tsx`
- `src/features/bi/components/VisualRenderer.tsx`
- `src/features/bi/components/VisualBuilder.tsx`
- `src/__tests__/bi_visuals_logic.test.ts`

**Archivos existentes que se tocan:**
- `backend/app/modules/bi/models.py` — `BIVisual`.
- `backend/app/modules/bi/schemas.py` — contratos de visuales.
- `backend/app/modules/bi/router.py` — endpoints de visuales.
- `backend/app/api/v1/router.py` — ya registrado en Fase 1.
- `backend/app/db/init_db.py` — ya importado en Fase 1.
- `src/app/router/AppRouter.tsx` — ruta lazy `/panels`, título "Paneles".
- `src/shared/layout/Header.tsx` — botón "Paneles".

**Tareas:**

1. `BIVisual`:
   - `user_id` FK `users.id` `ondelete=CASCADE`, indexado.
   - `connection_id` FK `corporate_connections.id` `ondelete=CASCADE`.
   - `panel_name` 120, `title` 255, `spec_json` Text, `position`, `created_at`
     con `utcnow_naive`.
   - Máximo 100 visuales por usuario.
2. `visual_service.py`:
   - Reutiliza la construcción y validación de SQL de Fase 1.
   - Ejecuta la consulta antes de guardar: no se guarda un visual que el
     usuario no puede ver hoy.
   - Reglas de `MASKED`: para un no-admin, solo `count`/`count_distinct`, sin
     filtros, etiqueta enmascarada; `sum`/`avg`/`min`/`max` se rechazan con
     403. Para el admin, se comporta como una columna normal (igual que el
     chat).
   - `date_grain` con `TO_CHAR`/`EXTRACT`; `sort` y `limit` del spec.
   - Cada consulta de visual escribe su propio `AuditLog` con
     `required=True`.
3. Frontend:
   - Lista de conexiones autorizadas, esquema, constructor con vista previa
     (debounce 350 ms), selector de tipo de gráfico, dimensión, grano de
     fecha, medida, agregación, filtros, orden y límite.
   - Guardar en panel, listar por panel, recalcular, ver SQL y borrar.
   - `VisualRenderer` con `WidgetChart`; tabla, KPI, barra horizontal, línea,
     área y torta.
   - Errores con `getApiErrorMessage`; sin `err?.response?.data?.detail`
     suelto.
   - `data-testid` estables (`panels-new`, `panels-save`, `panels-card`).
4. Tests:
   - Crear/listar/borrar solo los visuales del usuario actual.
   - Aislamiento entre usuarios (el visual de A no se ve ni se consulta por B).
   - Límite de 100; `position` estable.
   - Spec inválida 400; columna blocked 403; masked con `sum` 403 y con
     `count` permitida; etiqueta masked enmascarada.
   - `date_grain` y `sort`; preview no guarda; guardar ejecuta y audita.
   - Recalcular después de cambiar permisos: el visual guardado no se rompe,
     pero devuelve lo que el usuario puede ver hoy.

**Criterio de salida:** `/panels` crea, guarda, recarga y borra visuales sin
salir de Dat.ia.

### Fase 4 — Endurecimiento, e2e y limpieza

**Archivos que se tocan:**
- `e2e/bi-report.spec.ts` (nuevo)
- `e2e/panels.spec.ts` (nuevo)
- `DOCS/28_INFORME_BI_Y_PANELES.md` — cambiar estado a "implementado" con la
  evidencia real.
- `plans/README.md` — fila 011 y párrafo de pendientes.
- `DOCS/README.md` — enlace al documento nuevo.
- `README.md` — solo si la lista de funcionalidades lo amerita.

**Tareas:**

1. E2E:
   - Login admin, abrir Power BI, elegir la base demo y `fact_ventas`, ver
     KPIs y las 5 pestañas, cambiar año/mes y comprobar que el detalle cambia.
   - Paneles: nuevo visual, vista previa, guardar, recargar y borrar.
   - Usuario sin permisos: no ve la base en el selector.
   - El botón del chat se cubre con el test puro de `buildPowerBiUrl` y una
     comprobación manual; no se ata el e2e de BI a una consulta al LLM.
2. Limpieza:
   - Confirmar que no hay `*.php`, `vendor/chart*.js`, `VITE_POWERBI_URL` ni
     instrucciones de `php -S` en el repo.
   - No agregar la carpeta `power bi/` ni `INSTALAR_POWERBI_PANELES.txt` al
     repo. Si están en la carpeta de entrega, se retiran de esa entrega.
   - No portar `exportar_powerbi.py`.
3. Verificación final: correr todos los comandos de la sección 6 y pegar la
   salida real.

**Criterio de salida:** suite completa verde, e2e verde, documentación
actualizada y ninguna dependencia de PHP.

---

## 5. Criterios de aceptación

- `GET /api/v1/bi/connections`, `/schema` y `/report` funcionan con JWT y sin
  PHP, sin iframe y sin `postMessage`.
- Un usuario sin rol recibe 403 y queda `RECHAZADO_RBAC` en `audit_logs`.
- Un usuario sin permiso a una tabla no la ve en el esquema ni puede pedirla
  por nombre; recibe 403 y queda auditoría.
- Las columnas `BLOCKED` no aparecen en esquema, filtros, medidas ni detalle.
- Las columnas `MASKED` no aparecen en el informe automático de un no-admin;
  en paneles, un no-admin solo las usa como conteo y su etiqueta sale
  enmascarada. El admin las ve como columnas normales.
- El detalle no devuelve más de 200 filas y no incluye columnas ocultas.
- Toda consulta ejecutada es un único SELECT validado por `ASTValidator` y
  ejecutado por `SQLExecutor`; la conexión es de solo lectura.
- Si falla la escritura de auditoría, el endpoint responde 503 y no devuelve
  datos.
- El botón Power BI del chat abre la base y la tabla de la consulta.
- `/powerbi` conserva las 5 pestañas, los filtros, los KPIs y el estado en la
  URL; `/panels` conserva el constructor y los paneles guardados.
- No se agregaron dependencias; el chunk de ECharts no crece por importar
  `echarts-for-react` completo.
- `pytest`, `npm test`, `npx tsc --noEmit`, `npm run build` y
  `npm run test:e2e` verdes, con la salida real en el informe.

---

## 6. Verificación

| Comando | Esperado |
|---|---|
| `cd backend; venv\Scripts\python.exe -m pytest -q` | Suite completa verde; anotar total real |
| `cd backend; venv\Scripts\python.exe -m pytest -q tests/test_bi_schema_api.py tests/test_bi_report_api.py tests/test_bi_visuals_api.py tests/test_bi_security.py` | Enfocados verdes |
| `npx tsc --noEmit` | exit 0 |
| `npm test` | Vitest verde; anotar archivos/tests |
| `npm run build` | exit 0; warning esperado del chunk de ECharts |
| `npm run test:e2e` | Power BI, paneles y smoke verdes |

No se declara terminado nada que no se haya ejecutado. Si un comando no se
corre, se dice explícitamente.

---

## 7. Riesgos y efectos diferidos

| Riesgo | Efecto | Mitigación |
|---|---|---|
| `get_authorized_schema_prompt` devuelve `blocked_columns`/`masked_columns` como nombres sin tabla. | Una columna homónima en otra tabla se oculta de más. | Es fail-closed: se documenta y no se relaja. Si molesta, un plan aparte debe cambiar el contrato del guard. |
| El informe ejecuta ~10 consultas por request. | Latencia en bases grandes y un engine por consulta. | Medir en Fase 1. Si el demo supera 3 s, agregar un batch de solo lectura en `SQLExecutor` (con tests de pilar), no antes. |
| `COUNT(DISTINCT)` para autoseleccionar grupo/categoría. | Costo en tablas grandes. | Máximo 12 candidatas; el usuario puede elegir a mano. Si falla, 400 accionable. |
| `CAST(col AS DATE)` sobre texto no ISO. | Error de ejecución. | 400 con mensaje que indique elegir una columna de fecha real. No se adivina el formato. |
| Admin ve columnas MASKED en claro. | Consistente con el chat, pero sensible. | Documentado; `HIDDEN_COLUMNS` sigue oculto para todos. |
| FK de `bi_visuals` a la conexión. | Borrar una conexión borra sus visuales. | Es el comportamiento deseado; cascade explícito y test. |
| Un solo audit por informe. | Menos granularidad que una fila por SQL. | El `sql_generated` contiene el conjunto completo y el estado final; documentado. |
| Nombres de tabla/columna con mayúsculas. | SQL inválido si se usa el nombre del request. | Se usa siempre el nombre físico del esquema, entre comillas. |
| Cambio de permisos entre el esquema y la consulta. | 403/404 inesperado. | Se recalcula en cada request; los visuales se recalculan con permisos de hoy. |

---

## 8. Fuera de alcance

- Exportar a Microsoft Power BI real (`.pbix`, ODBC, CSV masivo). Si se pide,
  va en un plan aparte con RBAC, masking y auditoría.
- Exportar el informe a PDF/Excel. El módulo `reports` existe, pero no se toca
  aquí.
- SQLite, MySQL, SQL Server u Oracle.
- Cambiar el motor de chat, el `ASTValidator`, la gobernanza o el
  `SQLExecutor`.
- Dependencias nuevas (`chart.js`, `pandas`, etc.).
- Rediseñar el chat o el tablero ejecutivo.

---

## 9. Notas para el ejecutor

1. Este plan **no autoriza** a copiar archivos de la carpeta de referencia.
   Se leen como contrato, se reimplementan.
2. No commitear. El trabajo queda en el árbol para revisión del responsable.
3. Un test = un comportamiento. Los tests de pilares se corren completos:
   `test_ast_validator`, `test_ast_hardening`, `test_governance_fail_closed`,
   `test_masked_columns`, `test_audit_fail_closed`.
4. Si un test falla por un defecto de producción, se reporta; no se baja el
   assert.
5. Si el alcance se desborda, se detiene y se reporta antes de tocar los
   pilares.
6. Al cerrar, actualizar el estado de este plan y la fila 011 de
   `plans/README.md` con la fecha y la evidencia real.

---

## 10. Resultado

Implementado y verificado el 17/11/2026.

**Backend**
- `backend/app/modules/bi/`: `models.py`, `schemas.py`, `governance.py`,
  `sql_builder.py`, `report_service.py`, `visual_service.py`, `router.py`.
- `backend/app/api/v1/router.py`: `/api/v1/bi`.
- `backend/app/db/init_db.py`: tabla `bi_visuals`.
- Tests: `test_bi_report_api.py` (14), `test_bi_visuals_api.py` (11).

**Frontend**
- `src/pages/BIReportPage.tsx`, `src/pages/PanelsPage.tsx`.
- `src/features/bi/`: servicio, tipos, lógica pura, `BIChart`,
  `ReportSections`, `VisualRenderer`, `panels_logic`.
- Rutas `/powerbi` y `/panels`, botones en el header y botón Power BI junto a
  SQL en el chat.
- Tests: `bi_report_logic.test.ts`, `bi_service.test.ts`.
- E2E: `e2e/bi.spec.ts`.

**Evidencia real**
- `pytest -q`: **1133 passed, 264 subtests (7:46)**.
- Tests BI enfocados: **25 passed**.
- Pilares: **86 passed + 95 subtests**.
- `npx tsc --noEmit`: **exit 0**.
- `npm test`: **25 archivos / 202 tests**.
- `npm run build`: **exit 0**; chunk de ECharts **704,15 kB** (el esperado).
- `npm run test:e2e`: **12 passed + 1 skipped (1,8 min)**.

**Incremento (17/11/2026, a pedido del responsable):** edición de visuales
guardados (`PUT /bi/visuals/{id}`, re-ejecuta antes de aceptar el cambio) y
reordenado por panel (`PUT /bi/visuals/positions`, lista completa e
idempotente). En la UI: lápiz para editar, arrastre HTML5 nativo y botones
↑ / ↓ accesibles por teclado.

No se commiteó; el cambio queda en el árbol para revisión del responsable.
