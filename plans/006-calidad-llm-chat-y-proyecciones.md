# 006 — Calidad de respuesta del LLM: chat y proyecciones

**Fecha:** 05/11/2026
**HEAD de referencia:** `4a14801` más los cambios de esta sesión sin commitear
**Estado:** ✅ Fase 1 implementada y verificada (05/11/2026). Fases 2–4 pendientes.
**Alcance:** prompts, contexto, honestidad, latencia, evaluación y aprendizaje de
las dos formas de usar el LLM:

1. **Chat sobre la base**: lenguaje natural → SQL → filas → narrativa.
2. **Proyecciones**: pregunta → elección cerrada de columna/fecha → SQL
   determinista → `numpy`/stdlib → narrativa.

> **Corrección de una premisa:** las proyecciones **no usan pandas**. El único
> uso de `numpy` es `polyfit/polyval` en `ml_models.py:162-164`; el resto es
> `statistics`, `math`, `decimal` y `calendar`. `forecast_calculator.py:5` lo dice
> explícitamente ("Sin pandas, sin sklearn"). No hay que agregar pandas.

---

## 1. Diagnóstico

### 1.1 Tamaño de los prompts (medido el 05/11/2026)

Medición estática con `len(texto)/4` como aproximación a tokens. No hay Ollama
disponible en `11434`, así que no se midió latencia real.

| Componente | Tamaño | Tokens aprox. |
|---|---:|---:|
| System prompt SQL (rol admin) | 2.284 chars | ~571 |
| Esquema demo admin (4 tablas) | 4.199 chars | ~1.050 |
| User prompt SQL con esquema + historial | 4.645 chars | ~1.161 |
| **Entrada total SQL** | — | **~1.732** |
| System prompt síntesis | 5.089 chars | ~1.272 |
| User prompt síntesis (10 filas) | 1.067 chars | ~267 |
| **Entrada total síntesis** | — | **~1.539** |
| Techo de salida síntesis | — | **1.200** |
| Timeout del cliente | — | **90 s** (`useChatEngine.tsx:21`) |

El comentario de `kpi_calculator.py:14-24` estima ~5 tokens/s en CPU: 1.200
tokens de salida serían ~240 s, muy por encima de los 90 s del cliente.

### 1.2 Diferencias de diseño que importan

| Eje | Chat | Proyecciones |
|---|---|---|
| Autor del SQL | LLM | Backend, plantilla fija |
| Superficie de decisión del LLM | tablas, columnas, agregaciones, filtros, sintaxis | elegir 2 nombres de una lista cerrada + prosa |
| Números | LLM redacta sobre `rows[:10]`; solo los KPIs se calculan | backend calcula todo; el LLM no toca números |
| Fallback | si no hay SQL, respuesta offline; si el SQL falla, fallback de emergencia con filas arbitrarias | columnas → heurística; narrativa → plantilla determinista |
| Procedencia | no se publica si la prosa es del modelo o de una plantilla | `analysis_source`, `narration_source`, `horizon_source` |
| Presupuesto | hasta 4 salidas en un JSON de 1.200 tokens | 120 tokens para elegir; 420 para narrar |

La conclusión es incómoda: **la proyección es más confiable para publicar un
número** porque el LLM decide menos. El chat cubre consultas arbitrarias, pero
necesita adoptar el mismo principio: **el modelo propone y redacta; el backend
decide hechos, números y riesgo**.

### 1.3 Causas raíz

1. **Fallback de emergencia** (`sql_executor.py:257`): si falla el SQL y el
   self-healing, ejecuta `SELECT * FROM primera_tabla LIMIT 20` y lo etiqueta
   `APROBADO (Fallback de Emergencia)`. La síntesis narra filas que no responden
   la pregunta.
2. **Síntesis sobre 10 filas** (`prompts.py:476`): no hay perfil agregado del
   resultado; el modelo extrapola totales y porcentajes.
3. **El prompt pide proyecciones y causalidad** (`prompts.py:435-443`):
   `business_impact` What-If y "causa raíz" contradicen la regla de cero
   alucinación; `risk_level` cae a `BAJO` si el modelo no lo emite
   (`kpi_calculator.py:482-484`).
4. **Fences incompletos** (`prompts.py:74-75`, `:478`): la pregunta no se
   neutraliza; los few-shot y los metadatos del catálogo tampoco.
5. **Tablas del prompt ≠ tablas del DDL** (`sql_generator.py:66-70`,
   `dynamic_schema.py:536,662-667`): el prompt lista todas las autorizadas; el
   DDL solo las activas.
6. **Latencia incompatible**: 1.200 tokens de salida contra 90 s de cliente y un
   semáforo de una sola inferencia.
7. **Sin medición de accuracy**: no hay benchmark; no se puede afirmar calidad.

---

## 2. Principios de la solución

- **El LLM propone, el backend decide.** Nombres de columnas, números, riesgo y
  KPIs se validan/calculan en Python. El modelo redacta y elige entre opciones
  cerradas.
- **Sin respuesta es mejor que una respuesta falsa.** Se elimina el fallback de
  emergencia; se devuelve un error accionable.
- **La narrativa cita hechos, no los infiere.** Perfil de datos determinista +
  muestra pequeña para nombres/formato.
- **Los fences son datos, no instrucciones.** Toda entrada del usuario, ejemplo,
  SQL o metadato se envuelve y se neutraliza.
- **Procedencia explícita.** Si la prosa es plantilla o si el análisis es
  heurístico, se publica.
- **No se toca AST, RBAC, CLS, auditoría ni remediación en memoria.**

---

## 3. Fase 1 — Honestidad

Objetivo: dejar de afirmar cosas que el sistema no puede sostener.

### F1-1. Eliminar el fallback de emergencia

**Archivos:** `backend/app/modules/chat_engine/sql_executor.py:241-261`,
`backend/tests/test_cls_select_star.py`, nuevo
`backend/tests/test_sql_executor_honesty.py`.

**Actual:** dos fallos de ejecución → `SELECT * FROM <primera_tabla> LIMIT 20` →
`APROBADO (Fallback de Emergencia)` → la síntesis narra datos no relacionados.

**Propuesto:** borrar el fallback. Si el self-healing no produce SQL válido,
propagar `RuntimeError` con el error original. `engine.py` ya lo convierte en
`ERROR_EJECUCION` sin filas, sin síntesis y sin memoria de aprendizaje.

**Test:** dos ejecuciones fallidas → `RuntimeError`; a nivel motor →
`validation_status == "ERROR_EJECUCION"`, `rows_returned == 0`, SQL sin
`SELECT * FROM`, sin `QueryLearningMemory` nuevo.

### F1-2. Riesgo determinista, nunca del modelo

**Archivos:** `kpi_calculator.py:162,233,335,482-490`, `prompts.py:442,459`,
`schemas.py:41`, tests.

**Actual:** el prompt pide `risk_level`; el parser usa `data.get("risk_level",
"BAJO")` y pisa el riesgo determinista. Sin datos o sin métrica, el informe dice
`BAJO`.

**Propuesto:** una función determinista basada en
`compute_pareto_concentration`:

```text
sin concentración medible → None
top_entity_share > RISK_HIGH_PCT → "ALTO"
top_entity_share > RISK_MEDIUM_PCT → "MEDIO"
resto → "BAJO"
```

`build_dynamic_visualization` usa la misma función; `_parse_executive_report_dict`
devuelve `None` por defecto; la síntesis ignora el `risk_level` del modelo.
No se inventa `"NO_DETERMINADO"` porque la UI lo estiliza como BAJO; se usa
`None` y se oculta el badge.

**Test:** 75% → ALTO; 45.8% → MEDIO; 3 filas parejas → BAJO; `[100,-10,-10]` →
None; 2 filas → None; JSON del modelo con BAJO pero datos 75% → ALTO.

### F1-3. Quitar What-If y "causa raíz"

**Archivos:** `prompts.py:435-443,447-463`, `kpi_calculator.py:158-164,222-235,337-351`,
tests.

**Actual:** el prompt pide "causa raíz", `business_impact` What-If y "meta
numérica concreta"; el fallback determinista también escribe un What-If.

**Propuesto:** quitar `business_impact` del prompt y del JSON; `overview`
describe el resultado y su composición, sin atribuir causas; las recomendaciones
son acciones sugeridas, sin prometer resultados ni fijar umbrales inventados.
`_parse_executive_report_dict` descarta un `business_impact` que el modelo emita.

**Test:** el prompt no contiene `"causa raíz"`, `"What-If"`, `"business_impact"`
ni `"meta numérica"`; el informe determinista tiene `business_impact is None`;
un JSON viejo con `business_impact` produce `None`.

### F1-4. Fence de la pregunta

**Archivos:** `prompts.py:74-75,466-488`, `tests/test_prompt_isolation.py`.

**Actual:** `_wrap_user_input` no neutraliza `</user_question>`; la pregunta de
síntesis se interpola cruda.

**Propuesto:** `_wrap_user_input` elimina `</{label}>` del contenido; la síntesis
usa `_wrap_user_input("user_question", question)`; el system declara que la única
instrucción es la que está dentro de ese fence.

**Test:** pregunta con `</user_question>` no cierra el fence; el texto inyectado
queda dentro; la pregunta de síntesis queda fuera de `<datos_base>`.

### F1-5. Proyecciones: H2 y H3

**Archivos:** `backend/app/modules/ml_forecast/ml_service.py:282-283`,
`backend/app/modules/ml_forecast/router.py:214-266`,
`backend/tests/test_ml_honesty.py`.

**H2:** el LLM puede elegir un ID/folio numérico como métrica porque
`_validate_llm_choice` solo exige `_is_num_col`; el path manual exige además
`looks_like_metric`. **Propuesto:** usar `_is_num_col(c) and looks_like_metric(c)`
también para la elección del LLM; si no pasa, cae a la heurística.

**H3:** la auditoría de proyecciones es best-effort (`required=False`) y una
excepción no prevista deja 500 sin fila. **Propuesto:** pasar `required=True` en
las dos auditorías de `/projections/run` y agregar un `except Exception` que
audite `ERROR_EJECUCION` antes de responder 500. No cambiar el contrato de
`available=False`.

**Test:** LLM mockeado elige `id_cliente` → `analysis_source="heuristica"` y no
se usa como métrica; fallo de auditoría → 500/`AuditWriteError`, sin respuesta
silenciosa; excepción inesperada → fila `ERROR_EJECUCION` y 500.

### F1-6. H1 de proyecciones: identificador mutado

**Archivo:** `backend/app/modules/chat_engine/forecast_service.py:127-136`.

**Actual:** `_quote` filtra a `[A-Za-z0-9_]`; `"Monto ($)"` se convierte en
`"Monto"` y puede medir otra columna sin avisar.

**Propuesto:** usar el helper existente `quote_sql_identifier` (escapa comillas)
o rechazar el identificador cuando no sobreviva intacto. Preferencia: citar
correctamente y preservar el nombre; el AST sigue validando tablas/columnas.
Actualizar `test_prediction_honesty.py:263-275` para verificar que el nombre
sigue siendo un único identificador y que el AST lo acepta.

**Test:** `"Monto ($)"` genera `"Monto ($)"`; no se confunde con `"Monto"`;
nombre con `"` se escapa; AST acepta.

---

## 4. Fase 2 — Contexto y prompt

### F2-1. Perfil de datos agregado

**Archivos:** `kpi_calculator.py` (nueva `build_data_profile`),
`prompts.py:466-488`, `engine.py:635-644`, tests.

Construir en Python, sobre las filas ya enmascaradas y después de la remediación:

```text
{
  "alcance": "filas_devueltas",
  "filas_devueltas": 137,
  "filas_muestra": 10,
  "posible_truncamiento": false,
  "remediacion_aplicada": false,
  "perfil_truncado": false,
  "columnas": {
    "monto": {"tipo":"numerica","no_nulos":137,"nulos":0,"suma":...,"promedio":...,"min":...,"max":...},
    "cliente": {"tipo":"texto","distintos":42,"top":[{"valor":"ACME","n":30}, ...]},
    "fecha": {"tipo":"fecha","min":"2026-01-01","max":"2026-10-31"}
  }
}
```

Reglas: `bool` no es numérico; promedio sobre valores numéricos, no sobre filas;
`top` solo para baja cardinalidad y nunca para columnas MASKED; si el JSON supera
~2.500 chars, recortar `top`, luego fechas, y marcar `perfil_truncado=true`.
La muestra de 10 sigue existiendo para nombres y formato.

### F2-2. Alinear tablas del prompt con el DDL

**Archivos:** `dynamic_schema.py:662-667`, `sql_generator.py:66-70`,
`prompts.py:150-260`.

`get_authorized_schema_prompt` devuelve además `schema_tables` (las activas).
El prompt SQL usa ese subconjunto; el AST/executor siguen recibiendo
`allowed_tables` completo. Si el modelo necesita una tabla podada, el reintento
con esquema expandido se evalúa en Fase 3.

### F2-3. Reestructurar el prompt SQL

**Reglas duras:** una sola sentencia SELECT/CTE; solo tablas y columnas del
`<esquema>`; no inventar filtros; fecha actual; forma de agregación (total =
una fila; desglose = GROUP BY dimensión + métrica); `NULLIF` para ratios; no
convertir NULL en 0; `LIMIT` en detalle; PostgreSQL (`EXTRACT`, `ILIKE`,
`STRING_AGG`); RBAC con `<acceso_denegado>`; salida solo en ```sql.

**Reglas blandas:** agrupar/ordenar cuando pidan resumen/desglose/top; alias
legibles; no agregar columnas no pedidas.

**Orden del user prompt:** rol/motor/fecha → `<esquema>` → `<conversacion_previa>`
→ `<ejemplos>` → `<user_question>` al final → instrucción de salida.

**`is_admin`:** agregar parámetro `is_admin` a
`get_text_to_sql_system_prompt`; `SQLGenerator` ya lo recibe y lo pasa.

### F2-4. Reestructurar el prompt de síntesis

**Perfiles:** `minimal` (narrativa + preguntas), `standard` (narrativa + KPIs +
preguntas), `report` (narrativa + KPIs + informe + preguntas). El perfil se elige
con `pres_hints.show_executive_report`/`show_kpis`.

**System:** verdad (solo números del perfil/datos/SQL), aislamiento, narrativa
(2–3 párrafos, respuesta primero), KPIs (columnas/agg cerrados, valor calculado
por backend), informe descriptivo sin causalidad, 3 preguntas, JSON con
`narrative` primero.

**User:** rol/SQL → `<perfil_datos>` → muestra `<datos_base>` →
`<conversacion_previa>` → `<user_question>` → instrucción JSON.

**Compatibilidad:** `_parse_executive_report_dict` sigue aceptando JSON viejo con
`risk_level`/`business_impact`, pero los descarta; si no hay `overview`, devuelve
`None` y el motor conserva el informe determinista.

---

## 5. Fase 3 — Rendimiento, latencia y modelo

1. **Perfiles de síntesis** para no pedir `executive_report` cuando la UI no lo
   muestra; medir tokens de salida por perfil.
2. **JSON mode / gramática**: `response_format` en endpoints OpenAI-compatible,
   `format: json` en Ollama nativo; fallback si el proveedor no lo soporta.
3. **Payload por proveedor**: `options.num_ctx`, `repeat_penalty`, `seed` no son
   OpenAI estándar; verificar qué acepta cada motor. Detectar el contexto real
   del modelo y advertir si es menor al configurado.
4. **Timeout y cancelación**: alinear `LLM_TIMEOUT` con los 90 s del cliente;
   cancelar la inferencia al desconectar; el semáforo no puede quedar retenido
   por una request abandonada.
5. **`finish_reason` y `usage`**: detectar `length`, reintentar o fallar claro;
   registrar tokens, latencia y modelo.
6. **Benchmark de modelos**: 7B vs 14B, Q4 vs Q5, Coder vs Instruct para
   narrativa; medir accuracy y latencia en el hardware real. No cambiar por
   intuición.

---

## 6. Fase 4 — Evaluación, retrieval y aprendizaje

1. **Golden set** de 50–100 preguntas por esquema, con resultado esperado:
   análisis, seguimiento, ambiguas, fuera de alcance, adversariales.
2. **Métricas**: SQL válido, execution accuracy, columnas/tablas correctas,
   timeout, p95, cifras no fundamentadas, clarificación, RBAC.
3. **Retrieval de esquema**: reemplazar/complementar el ranking léxico por
   sinónimos + FK + embeddings locales; medir recall de tablas.
4. **Few-shot**: filtrar por tablas relevantes, TTL y validación contra el
   esquema; feedback del usuario para marcar SQL bueno/malo.
5. **Verificación numérica** de la narrativa: contrastar cifras con el perfil;
   no bloquear por regex sin evidencia.
6. **Clarification loop** para preguntas ambiguas, antes de generar SQL.
7. **Procedencia en `QueryResponse`**: `narrative_source`, `analysis_source` y
   `prompt_version` para que la UI y la auditoría sepan qué generó la prosa.

---

## 7. Criterios de aceptación

No se fijan números de accuracy antes de medir la línea base. Fase 1 y 2:

- `pytest` completo en verde, con la salida real pegada.
- `test_sql_executor_honesty.py`, `test_risk_deterministic.py`,
  `test_prompt_honesty.py`, `test_prompt_isolation.py`,
  `test_data_profile.py`, `test_prompt_contract.py` en verde.
- `Select-String -Pattern "Fallback de Emergencia"` sin resultados.
- `Select-String -Pattern "causa raíz|What-If|business_impact"` en
  `prompts.py` sin resultados.
- El prompt de síntesis contiene `<perfil_datos>` y `<user_question>`.
- Una pregunta con `</user_question>` no cierra el fence.
- La narrativa de 25 filas cita `325` (suma) y no `55` (suma de 10).
- Proyecciones: `id_cliente` no se acepta como métrica; auditoría obligatoria;
  `"Monto ($)"` no se mutila.

Fase 3 y 4: fijar objetivos después del baseline. Sugeridos: SQL válido ≥95%,
timeout <2%, cifras no fundamentadas <2%, p95 end-to-end <60 s, RBAC 100%.

---

## 8. Orden de ejecución

1. F1-1 fallback.
2. F1-2/F1-3 riesgo y What-If.
3. F1-4 fence.
4. F1-5/F1-6 proyecciones.
5. F2-1 perfil.
6. F2-2 tablas.
7. F2-3/F2-4 prompts.
8. Fase 3 y 4 con medición.

Cada paso: test dirigido → suite completa → `tsc`/`vitest`/`build` si toca
frontend. No se commitea sin indicación del responsable.

---

## 9. Resultado de la Fase 1 (05/11/2026)

Implementado y verificado:

- Eliminado el fallback de emergencia (`sql_executor.py`); SQL fallido no
  devuelve filas de otra tabla.
- Riesgo determinista y `None` cuando no hay concentración medible; el
  `risk_level`/`business_impact` del modelo se descarta.
- Prompt de síntesis sin What-If, "causa raíz" ni meta numérica inventada.
- Fences de pregunta, few-shots y metadatos de catálogo neutralizados.
- Proyecciones: el LLM no puede elegir un ID como métrica; auditoría
  `required=True`; excepción inesperada deja `ERROR_EJECUCION`.
- `_quote` preserva identificadores y escapa comillas; el AST sigue rechazando
  `;`.

Verificación real:

```
cd backend; venv\Scripts\python.exe -m pytest -q
888 passed, 1 warning, 244 subtests passed in 425.41s (0:07:05)
```

```
npm test        -> 18 archivos, 136 tests, 0 failed
npx tsc --noEmit -> exit 0
npm run build   -> exit 0 (warning esperado de echarts 704,15 kB)
```

Tests nuevos de la fase:
`test_sql_executor_honesty.py`, `test_risk_deterministic.py`,
`test_ml_projection_audit.py`; actualizados `test_cls_select_star.py`,
`test_dynamic_prompts.py`, `test_prompt_isolation.py`,
`test_prediction_honesty.py`, `test_ml_honesty.py`.

Revisión independiente: **APROBADO CON CAMBIOS**; los dos bloqueantes eran de
cobertura de tests (fallback no discriminante y pérdida del assert de `;`) y
quedaron corregidos. Advertencias restantes: sanitización de nombres crudos del
DDL y el badge cualitativo `[RIESGO]` de la rama conversacional; no bloquean.

---

## 10. Fuera de alcance

- Cambiar AST, RBAC, CLS, auditoría o remediación en memoria.
- Rediseñar la UI del informe.
- Soportar motores distintos de PostgreSQL.
- Garantizar cero alucinación sin evaluación; el plan reduce superficie, no
  promete un resultado que no se midió.
- Agregar pandas.
