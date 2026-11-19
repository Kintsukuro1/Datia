# Plan 013 — Paneles BI: pestañas de primera clase, filtros de panel, tipos extra y auto-sugerencia (Ola 2)

**Fecha:** 19/11/2026
**Estado:** ✅ Implementado y verificado (19/11/2026)
**Objetivo:** que `/panels` funcione como un dashboard de verdad: pestañas que se
renombran y eliminan (hoy son strings implícitos), filtros que aplican a toda la
pestaña (hoy solo por visual), más tipos de gráfico (gauge, donut, scatter,
apiladas) y sugerencias de visuales con la IA local. Sin dependencias nuevas, sin
PHP, sin romper los visuales ya guardados.

**Origen:** pedido del responsable (19/11/2026) tras revisar `/panels`. Ejecuta la
"ola 2" anotada en `plans/README.md` ("filtros relativos, tipos extra
(gauge/scatter), compartir panel por link y auto-sugerencia de visuales"), menos
"compartir por link" y "filtros relativos", que el responsable no eligió.

## Alcance

1. **Pestañas de primera clase**: renombrar y eliminar una pestaña explícitamente.
   Hoy `panel_name` es un string en cada visual (`models.py:30`) y no existe ni
   renombrar ("Ganancias" → "Ganancias 2026") ni borrar una pestaña vieja sin
   borrar visual por visual.
2. **Filtros de panel (slicers)**: una barra de filtros por pestaña que se aplica
   a TODOS los visuales de esa pestaña cuya tabla coincida (estilo slicer de
   Power BI). Los filtros declaran base+tabla+columna; un visual de otra tabla no
   se toca.
3. **Tipos extra de gráfico**: `gauge`, `donut`, `scatter` (misma fila
   label/value, solo cambia el render) y `stacked` (barras apiladas por una
   segunda dimensión "serie").
4. **Auto-sugerencia de visuales**: la IA local propone 2–4 definiciones de
   visual a partir del esquema autorizado; el usuario hace clic en una, la ve en
   el constructor con vista previa y decide si guardar. Nunca se auto-guarda ni
   se auto-ejecuta nada.

## Fuera de alcance

- Compartir panel por link (el responsable lo descartó por ahora; además
  requiere decidir gobernanza: hoy cada visual es privado por `user_id`).
- Filtros relativos ("últimos 30 días"): cambian el contrato de período, ola 3.
- Scatter numérico×numérico: exige dos medidas en la fila (cambia `BIRow` de
  raíz). El scatter de este plan es dimensión→eje, valor→eje Y. Techo declarado.
- Filtros cruzados entre visuales del panel (cross-filtering ya existe en el
  informe `/powerbi`, no se duplica en paneles).
- DAX, publicación a la nube: no se copian a propósito.

## Decisiones de diseño (con el código ya leído)

### F1 — Entidad `bi_panels` con unión por string (mínimo diff)

- Tabla nueva `bi_panels`: `id`, `user_id` (FK `users.id` CASCADE), `name`
  (`String(120)`), `filters_json` (`Text`, default `'[]'`), `created_at`;
  `UniqueConstraint(user_id, name)`. Se crea con `Base.metadata.create_all`
  (`db/init_db.py:202`), el mismo mecanismo que usó `bi_visuals` en el plan 011.
- `bi_visuals.panel_name` **sigue siendo string y la clave de unión**: no se
  agrega FK `panel_id` ni se migran visuales existentes. Renombrar = una
  transacción con dos `UPDATE` (`bi_visuals.panel_name` + `bi_panels.name`);
  eliminar = `DELETE` de los visuales del usuario en ese panel + su fila
  `bi_panels`.
- `GET /bi/panels` devuelve la unión de `bi_panels` del usuario con los
  `panel_name` distintos de `bi_visuals` (retrocompatible: los paneles creados
  antes de este plan existen aunque no tengan fila en `bi_panels`), cada uno con
  sus filtros.
- Endpoints nuevos (todos `get_current_user`, scoping por `user_id`):
  - `GET /bi/panels`
  - `PATCH /bi/panels/rename` — `{panel_name, new_name}`; 400 si `new_name` ya
    existe para el usuario; 404 si `panel_name` no existe.
  - `PUT /bi/panels/filters` — `{panel_name, filters: List[BIPanelFilter]}` con
    máximo 5; upsert de la fila `bi_panels` (la crea si el panel era solo
    string).
  - `DELETE /bi/panels/{panel_name}` — borra visuales + fila de panel.
- `BIPanelFilter = {connection_id, table, column, op, value}`: el filtro declara
  a qué base y tabla apunta, porque una pestaña puede mezclar visuales de varias
  tablas y bases.
- **Merge en consulta, no en el guardado**: `run_visual` recibe un parámetro
  nuevo `panel_filters: List[BIFilter] = []` y `build_visual_sql` los procesa con
  el MISMO `resolve(allow_masked=False)` que usa para los filtros del visual
  (`sql_builder.py:355-381`). `get_visual_data` carga la fila `bi_panels`,
  filtra los que matchean `connection_id` + `table` del visual y los pasa.
  `preview_visual` **no** los aplica: el constructor edita un visual, no el
  panel. El SQL guardado (`spec_json`) no cambia: el merge es en memoria, por
  request, con los permisos de hoy.
- Sin re-validar el spec mergeado con Pydantic (el `max_length=5` de
  `spec.filters` haría explotar la unión 5+5): se pasa como lista separada.

### F2 — Tipos: tres gratis, uno con serie

- `BIChartType` += `"gauge" | "donut" | "scatter" | "stacked"`.
- **gauge**: comparte el camino sin dimensión de `kpi` (misma consulta
  `SELECT value ... LIMIT 1`); render = ECharts gauge con min/max del propio
  resultado.
- **donut**: variante de `pie` (radio `['55%','80%']` + total al centro).
- **scatter**: misma fila label/value que barras; render de puntos.
- **stacked**: campo nuevo opcional `series: Optional[str]` en `BIVisualSpec`
  (default `None`: los visuales guardados antes del cambio siguen idénticos).
  `build_visual_sql` agrupa por dimensión **y** serie:
  `SELECT dim AS label, CAST(serie AS TEXT) AS series, value ... GROUP BY ambos
  ORDER BY label ASC, series ASC LIMIT`. La serie se resuelve con
  `allow_masked=False` para no-admin (una columna enmascarada en la leyenda se
  deniega, como los filtros: enmascarar leyendas serie por serie es más frágil
  que denegar).
- `BIRow` acepta `series` opcional; `visualToCsv` agrega la columna `Serie`
  cuando existe; el pivot `rowsToStacked` vive en `panels_logic.ts` (puro,
  testeable en vitest).
- `ASTValidator.validate_and_secure_sql` ya valida proyecciones con alias
  (`ast_validator.py:647-657`); el SELECT de tres alias pasa por el mismo camino
  que hoy pasa label/value. Se cubre con test específico.

### F3 — Auto-sugerencia validada server-side

- `POST /bi/suggest-visuals` — `{connection_id, table}` → hasta 4 specs
  candidatas con título.
- Camino: `load_context` (gobernanza del usuario) → esquema autorizado en texto
  compacto (solo tablas/columnas permitidas con su `kind`) →
  `LLMService.generate_completion` (`chat_engine/llm_service.py:529`, proveedor
  configurado, sin dependencias nuevas) con prompt de JSON estructurado →
  parseo tolerante → **validación server-side de cada sugerencia contra
  `BIContext`**: tabla permitida, columnas existentes, no enmascaradas para
  no-admin en filtros/serie, `chart_type`/`aggregation`/`sort`/`date_grain`
  dentro de los literales, medida numérica si `sum`/`avg`. Las inválidas se
  **descartan**, no se "corrigen" (el LLM no decide; el contexto decide).
- La respuesta no ejecuta nada: el usuario hace clic en una sugerencia, se abre
  el constructor precargado, previsualiza y guarda por el camino ya auditado.
- LLM caído o respuesta no-JSON → 503 honesto ("IA no disponible"), sin
  sugerencias falsas ni fallback inventado.
- Sin fila de auditoría propia: no consulta la base corporativa ni devuelve
  filas (mismo criterio que `GET /bi/schema`); la consulta del visual que el
  usuario decida correr audita como siempre.
- Al implementar el prompt, cargar `prompt-engineering-patterns` (regla del
  repo para JSON estructurado).

### Gobernanza y pilares (declaración)

- **Solo lectura**: ningún camino nuevo de ejecución; stacked y merge de filtros
  pasan por `build_visual_sql` → `ASTValidator` → `SQLExecutor` como hoy.
- **RBAC fail-closed**: filtros de panel validados con `resolve()` del contexto
  del usuario (columna enmascarada de no-admin → `BIAccessDenied`); sugerencias
  validadas contra el esquema autorizado; rename/delete scoping estricto por
  `user_id`.
- **Masking**: la serie enmascarada se deniega para no-admin; el sink
  `mask_query_rows` sigue en el único funnel (`visual_service.py:160`).
- **Trazabilidad**: cada visual ejecutado audita su SQL REAL (que incluye las
  condiciones del panel mergeadas); rename/delete/filtros son metadata del
  propio usuario sin consulta corporativa y no auditan (precedente documentado
  en `reorder_visuals`, `visual_service.py:336`).

## Criterios de aceptación

- Creo "Ganancias", agrego visuales, la renombro a "Ganancias 2026": los
  visuales siguen en la pestaña y el nombre viejo desaparece de las tabs. Un
  panel ajeno con el mismo nombre no se toca (test de scoping).
- Elimino un panel: sus visuales y filtros se borran tras confirmar; los
  visuales de otros paneles quedan intactos.
- Un filtro de panel (ej. `fecha >= '2026-01-01'` sobre su tabla) aplica a todos
  los visuales de esa tabla en la pestaña; el SQL visible (ícono de ojo) incluye
  la condición. Un visual de otra tabla no cambia.
- Un filtro de panel sobre columna enmascarada (no-admin) rompe el visual con
  el mensaje de denegación: fail-closed, nunca se filtra en silencio.
- La vista previa del constructor NO aplica filtros de panel.
- Gauge, donut y scatter se guardan, renderizan y exportan CSV. Stacked agrupa
  por dos ejes, muestra leyenda y su CSV tiene columna `Serie`.
- Visuales guardados antes del plan siguen renderizando idéntico (`series=None`,
  tipos anteriores).
- "Sugerir" devuelve solo specs válidas para el rol del usuario; el clic abre el
  constructor; nada se guarda sin acción explícita del usuario; con el LLM
  caído el botón muestra el error honesto.
- `pytest`, `npm test`, `tsc --noEmit`, `npm run build` (y `npm run test:e2e`
  si el alcance toca flujos cubiertos) verdes, con la salida real pegada al
  cierre.

## Tests previstos

**Backend (pytest):**
- Rename/delete panel: visuales migran/borran; 400 al renombrar a nombre
  existente; 404 al tocar un panel que no es del usuario.
- Merge de filtros: aplica con tabla coincidente, no aplica con otra
  tabla/base, no aplica en `preview_visual`; filtro enmascarado → 403 auditado
  como `RECHAZADO_RBAC`; tope de 5 filtros de panel.
- SQL stacked: forma del SQL (dos GROUP BY, tres alias), `validate_and_secure_sql`
  lo acepta; serie enmascarada → 403; gauge/donut/scatter aceptados por el
  literal y por `_visual_out` round-trip.
- Sugerencias: columna inexistente del LLM se descarta; tabla no autorizada se
  descarta; LLM caído → 503.

**Frontend (vitest, lógica pura):**
- `rowsToStacked` (pivot de filas con `series`), `visualToCsv` con y sin
  `Serie`, validación de nombre de panel (trim, tope 120).

**e2e (si el flujo lo amerita):** renombrar un panel persiste tras recargar; el
SQL del visual muestra la condición del filtro de panel.

## Estimación

- F1 (pestañas + filtros): backend models/schemas/servicio/router + frontend
  (tabs renombrables, confirm de borrado, barra de filtros). Es el frente
  mayor.
- F2: `sql_builder` + renderer; gauge/donut/scatter son solo render.
- F3: un endpoint + prompt + chips en el constructor.

Los tres frentes son independientes: se pueden ejecutar y verificar por fases.

## Resultado

Implementado y verificado el 19/11/2026. HEAD inicial `c24970e`, sin deriva (el
árbol solo traía los planes 011/012 ya commiteados).

**Backend**
- `models.py`: tabla nueva `bi_panels` (`user_id` + `name` únicos,
  `filters_json`); `bi_visuals.panel_name` sigue siendo el string de unión.
- `schemas.py`: `BIPanelFilter`/`BIPanelRename`/`BIPanelFiltersUpdate`/
  `BIPanelOut`; `BISuggestRequest`/`BISuggestion`/`BISuggestionsOut`;
  `BIChartType` += `gauge|donut|scatter|stacked`; `BIVisualSpec.series`
  opcional (default `None`: los visuales previos no cambian).
- `sql_builder.py`: `build_visual_sql` acepta `panel_filters` y procesa
  ambos filtros con el MISMO loop `filter_conditions`/`resolve`
  (`allow_masked=False`); serie apilada con `GROUP BY` doble y orden fijo
  (eje, serie); serie enmascarada de no-admin → `BIAccessDenied`.
- `visual_service.py`: `run_visual(panel_filters=...)` (solo lo pasa
  `get_visual_data`, nunca la vista previa); `list/rename/filters/delete` de
  paneles con scoping por `user_id` y transacción única en el rename.
- `router.py`: `GET /bi/panels`, `PATCH /bi/panels/rename`,
  `PUT /bi/panels/filters`, `DELETE /bi/panels/{panel_name}`,
  `POST /bi/suggest-visuals`.
- `suggest_service.py` (nuevo): prompt tabla-acotado vía
  `LLMService.generate_completion`, parseo tolerante y validación de cada
  sugerencia con un dry-run de `build_visual_sql` (la sugerencia inválida se
  descarta, no se corrige). LLM caído o respuesta no-JSON → 503 honesto.
- Tabla nueva creada por el bootstrap de siempre (`init_db` → `create_all`
  en el arranque del backend; no hace falta migración manual).

**Frontend**
- `types.ts` / `bi_service.ts`: tipos y métodos de paneles + sugerencias.
- `panels_logic.ts`: `rowsToStacked` (pivot puro), `chartNeedsNoDimension`,
  `visualToCsv` con columna `Serie`.
- `VisualRenderer.tsx`: gauge, donut, scatter y stacked (ECharts ya tenía
  `GaugeChart`/`ScatterChart` registrados; las apiladas son `BarChart` con
  `stack`).
- `PanelsPage.tsx`: toolbar de pestaña activa (renombrar en línea, eliminar
  con confirm, filtros con contador), `PanelFiltersBar` (base+tabla+columna
  por filtro, columnas confidenciales ni se ofrecen), botón "Sugerir
  visuales (IA local)" con chips que precargan el constructor, y los 4 tipos
  nuevos en la grilla de selección (la serie se limpia al abandonar
  `stacked`; el orden se oculta en apiladas).

**Tests**
- Nuevos: `test_bi_panels_api.py` (11), `test_bi_suggestions_api.py` (4),
  +4 en `test_bi_visuals_api.py` (apiladas y tipos nuevos); +4 en
  `bi_panels_logic.test.ts` (pivot, CSV con serie, tipos sin eje). Las
  sugerencias se testean con el LLM mockeado: la validación server-side y
  los 503 son lo verificable sin depender del servidor local.

**Evidencia real**
- Línea base (antes): pytest **1136 passed, 264 subtests (7:46)**; `tsc` exit 0;
  vitest **26 archivos / 211 tests**; build OK (echarts 704,15 kB).
- Después: pytest **1155 passed, 264 subtests (7:39)**; `npx tsc --noEmit`
  exit 0; `npm test` **26 archivos / 215 tests**; `npm run build` exit 0;
  `npm run test:e2e` **12 passed + 1 skipped (1,3 min)** (incluido el flujo
  completo de paneles); suites de pilares
  **167 passed, 111 subtests (1:08)**.
- ruff C901 (advisory): `build_visual_sql` ya estaba sobre el umbral en HEAD
  (19) y subió a 24; `_resolve_columns` (19) y `build_report` (27) en
  `report_service.py` son preexistentes y no se tocaron.

Declarado: (1) las sugerencias contra el LLM real no se verificaron
automáticamente — se mockeó al LLM y se probó la validación y los 503; probar
el botón con el modelo local queda como verificación manual. (2) No se
agregaron e2e nuevos para renombrar/filtros/sugerencias: el contrato está
cubierto por los tests de API y el e2e existente de paneles (que pasó)
cubre que la UI no se rompió. (3) `build_visual_sql` sobre el umbral C901
es preexistente; el refactor queda en la lista por demanda del plan 004.

No se commiteó; el cambio queda en el árbol para revisión del responsable.
