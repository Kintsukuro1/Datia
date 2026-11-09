# 007 — Proyecciones genéricas: resolvedor de datos, granularidad y calidad

**Fecha:** 07/11/2026
**HEAD de referencia:** `4a14801` más los cambios sin commitear del árbol actual
**Estado:** ✅ Fases 1–5 implementadas y verificadas (07/11/2026).
**Alcance:** G1–G6 + G10 del listado acordado. G7–G9 quedan fuera.

> **Regla de diseño:** el código de producción no puede nombrar `datos`,
> `fact_ventas`, `campo_2` ni ninguna tabla/columna concreta. Los casos reales
> son tests de aceptación, no ramas del código. Si una mejora solo funciona
> porque la tabla se llama de una forma, no es una mejora: es un parche.

---

## 1. Problema, verificado contra el código actual

| Hecho | Dónde |
|---|---|
| La tabla de hechos se elige por `COUNT(*)` y se queda la mayor | `chat_engine/forecast_service.py:359-432` |
| La clasificación de columnas mira solo tipo físico y nombre | `chat_engine/forecast_service.py:54-90`, `ml_service.py:217-245` |
| `build_series_sql` fija mes, `SUM(valor)` y `WHERE valor > 0` | `chat_engine/forecast_service.py:144-182` |
| `normalize_period`, `next_period` y `period_distance` son mensuales | `chat_engine/forecast_calculator.py:60-96` |
| `MIN_PERIODS = 6` | `chat_engine/forecast_calculator.py:45` |
| `SemanticCatalog` existe con `friendly_name`, `description`, `synonyms` y `business_formula` | `admin_catalog/models.py:33-60` |
| Nadie en `ml_forecast/` ni en `forecast_service.py` consulta `SemanticCatalog` | búsqueda sin resultados |

Consecuencias medidas el 07/11/2026 sobre la conexión activa (caso de prueba,
no diseño):

- `campo_2` y `campo_5` son `BIGINT` con fechas `DDMMYYYY`; `campo_7` es
  `TEXT` con mayoría numérica. La proyección responde `available=False`
  porque no hay columna física de fecha.
- El heurístico ofrece `campo_1` (folio) como métrica, y `campo_2`/`campo_5`
  (fechas) también como métricas.
- La tabla tiene 31 días de diciembre de 2011: **31 periodos diarios, 5
  semanales y 1 mensual**. Con `MIN_PERIODS=6`, solo la granularidad diaria
  es viable. La mensual no es un bug del motor: no hay historia.
- El perfil de datos ya disponible (`SchemaInspector._profile_column`,
  `catalog/services/schema_inspector.py:115-241`) tarda 0,64 s en perfilar
  las 7 columnas de 38.180 filas y distingue bien los casos: `campo_1` tiene
  36.370 valores distintos (95,2% de las filas, identificador); `campo_2` tiene
  31 distintos y rango `01/12/2011`–`31/12/2011`; `campo_3` tiene 69 distintos
  (1–69, código numérico); `campo_4` tiene 35.075 distintos (identificador de
  documento).
- Las fórmulas del catálogo de esa conexión pasan el `ASTValidator` en las
  tres formas que necesita este plan: fecha, métrica y `COUNT(DISTINCT)`.

---

## 2. Decisiones tomadas (07/11/2026)

| # | Decisión |
|---|---|
| 1 | Orden de confianza: **catálogo semántico > perfil de datos > tipo físico > LLM**. |
| 2 | Granularidad **manual**: día, semana o mes. Sin auto. |
| 3 | Identificadores: **los decide el usuario**. Se ofrecen como `COUNT(DISTINCT col)` o se excluyen. |
| 4 | Tabla: **auto por pregunta** y **override manual**. |
| 5 | Calidad: **reglas** con MAPE, cobertura, naive, backtests y huecos; sin score compuesto. |

### 2.1 Qué significa "auto por pregunta" en este plan

La tabla se elige con una coincidencia determinista entre la pregunta y el
catálogo semántico de la conexión: nombre de tabla, `friendly_name`,
`synonyms` y `description` de la tabla (entradas con `column_name = NULL`).
Si no hay coincidencia, se usa la tabla autorizada con más filas, como hoy.

**El LLM no elige la tabla en este plan.** El orden de confianza acordado pone
al LLM último, y meter un segundo llamado con resúmenes de todas las tablas
multiplica el prompt y la latencia. Si en uso real la coincidencia determinista
no alcanza, se agrega el LLM como desempate en un plan posterior, no antes.

---

## 3. Resolvedor genérico (G1, G2, G4)

### 3.1 Módulo nuevo

`backend/app/modules/ml_forecast/resolver.py`, con funciones puras y un único
cargador de perfiles. No es una clase ni un framework: entra metadatos, sale
una clasificación.

```python
def resolve_columns(
    cols: List[Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],   # columna_lower -> entrada del catálogo
    profiles: Dict[str, Dict[str, Any]],  # columna_lower -> perfil de datos
    row_count: int,
) -> Dict[str, Any]:
    """Clasifica columnas sin tocar la base. Devuelve opciones con evidencia."""
```

Devuelve:

```python
{
    "date_options":       [{"column", "label", "source", "confidence", "evidence", "min", "max"}],
    "metric_options":     [{"column", "agg", "label", "source", "confidence", "evidence"}],
    "identifier_options": [{"column", "agg": "count_distinct", "label", "source", "confidence", "evidence"}],
    "dimension_columns":  ["..."],
    "count_option":       {"agg": "count", "label": "Cantidad de registros", ...},
    "reviewed":           ["columna", ...],
}
```

Las expresiones SQL (`business_formula`, cast seguro, `COUNT(*)`) se quedan
**del lado del backend**. El cliente solo manda nombres de columna, `agg` y
granularidad; nunca manda SQL ni expresiones. La expresión se vuelve a resolver
en la corrida y el SQL final pasa por `ASTValidator` + enmascarado.

Cada opción lleva, además de lo público, dos claves internas que no viajan al
cliente: `value_expr` (la expresión SQL) y `sql_agg` (cómo agregarla).
`sql_agg="formula"` significa que la fórmula del catálogo ya es la agregación
(por ejemplo `SUM(...)` o `COUNT(DISTINCT ...)`) y no se vuelve a envolver.
`run_projection` resuelve la opción elegida y usa esas claves; el pedido HTTP
solo nombra columna y `agg` pública.

### 3.2 Orden de clasificación

El orden es el acordado: **catálogo > perfil > tipo físico > LLM**. Se evalúa
en ese orden y el primero que resuelve la columna gana.

1. **Catálogo semántico** (si hay una entrada utilizable para la columna):
   - fórmula con salida fecha (`TO_DATE`, `TO_TIMESTAMP`, `::date`,
     `CAST(... AS DATE)`) → fecha, confianza alta, fuente `catalogo`;
   - `COUNT(DISTINCT ...)` → identificador (`count_distinct`); se usa la
     fórmula tal cual, no se envuelve otra vez;
   - `SUM(...)`/`AVG(...)`/`COUNT(...)` → métrica con `agg="formula"` y
     `sql_agg="formula"`; la fórmula ya es la agregación;
   - fórmula escalar numérica (`CASE ... THEN ...::numeric`,
     `CAST(... AS NUMERIC)`) → métrica, confianza alta, fuente `catalogo`.
   - Una entrada sin fórmula solo aporta `friendly_name`/`description` como
     etiqueta; no cambia la clasificación.
   - Una fórmula incompatible con el tipo físico se descarta: una columna
     `DATE` con `SUM(fecha)` no se convierte en métrica, gana el tipo físico.
   - `SUM(<la misma columna>)` se trata como **suma escalar**, no como fórmula
     pre-agregada: conserva el filtro `> 0` y el significado histórico de la
     serie de ingresos.

2. **Perfil de datos** (solo si el catálogo no resolvió la columna y no es un
   `DATE`/`TIMESTAMP` físico, que no necesita perfil):
   - patrón de fecha **y** rango válido (1900–2100) → fecha, confianza media;
   - PK o nombre de identificador (`id`, `id_`, `_id`, `folio`, `codigo`,
     `documento`, `clave`, `numero`) → identificador;
   - nombre de métrica (`monto`, `valor`, `total`, `cantidad`, `importe`,
     `saldo`, `precio`, `costo`, `margen`, ...) → métrica; **la cardinalidad
     alta sola no convierte una métrica en ID** (una tabla de 9 filas puede
     tener 9 valores distintos y seguir siendo una serie de montos);
   - una numérica sin nombre de métrica con `distintos / row_count >= 0.95` →
     identificador;
   - proporción numérica `>= 0.95` en una columna de texto → métrica con cast
     seguro, confianza media;
   - numérico con `distintos <= 100` y proporción `< 1%` → **código numérico**:
     métrica de confianza **baja**, con la evidencia "N valores distintos;
     puede ser un código, no una cantidad". No entra al camino automático;
   - texto que no es identificador → dimensión (se conserva la conducta actual
     de `_is_dimension_col`, que no impone un tope de cardinalidad);
   - resto → `reviewed`.

3. **Tipo físico** (si el perfil no está disponible o no concluye):
   - `DATE`/`TIMESTAMP`/`TIME` → fecha, confianza alta, fuente `tipo`;
   - numérico con PK/nombre de identificador → identificador;
   - otro numérico → métrica, confianza media, `agg="sum"`;
   - texto → dimensión.

4. **LLM**: no crea tipos ni expresiones. Solo elige entre las opciones que
   produjeron los pasos 1–3.

5. **`COUNT(*)`**: métrica sintética, siempre disponible si hay al menos una
   opción de fecha. No depende de que exista una columna numérica.

Marcadores de identificador: `is_pk`, nombre `id`, `id_`, `_id`, `folio`,
`codigo`, `cod_`, `documento`, `clave`, `numero`, o cardinalidad ≥ 95% de las
filas. Los identificadores **no** se ofrecen para `SUM`; se ofrecen para
`COUNT(DISTINCT)` si el usuario lo elige.

> La ambigüedad no se resuelve inventando: se publica. Un código de 8 dígitos
> puede parecer una fecha; por eso la detección por perfil exige patrón **y**
> rango válido, y aun así queda en confianza media. El selector manual es la
> salida para el caso que el algoritmo no puede decidir.

### 3.3 Seguridad de las fórmulas del catálogo

`business_formula` es metadata administrada, no SQL del usuario. Aun así, no se
inyecta a ciegas:

- Al resolver, se valida la fórmula con
  `ASTValidator.validate_and_secure_sql("SELECT <formula> AS v FROM <tabla> LIMIT 1", ...)`
  contra las tablas/columnas permitidas. Si no pasa, se descarta y se cae a
  perfil/tipo; la opción no se ofrece.
- La fórmula solo se usa si TODAS las columnas que nombra están en
  `candidate_cols` (sin `BLOCKED` ni `MASKED`). Las columnas se extraen con el
  parser de `ASTValidator`; el chequeo de `blocked_columns` no alcanza para
  `MASKED`, y una métrica enmascarada no se suma.
- El SQL final de la serie vuelve a pasar por el mismo gate. Una fórmula que
  cambió después de validar no llega al motor.

**Límite:** `ASTValidator` valida seguridad, tablas y sintaxis, **no tipos**. Una
fórmula que pasa el AST pero el motor rechaza por tipos incompatibles se reporta
como error de ejecución auditado, no se "repara" en runtime. La mitigación es el
perfil de datos y la revisión del catálogo.

### 3.4 Perfil de datos

Se reutiliza `SchemaInspector._profile_column` (`schema_inspector.py:115-241`):

- Se renombra a `profile_column` (público) y se actualiza su único llamador
  interno. No se duplica el perfilador.
- `_infer_pattern` se extiende con `DD/MM/YYYY`, `YYYY/MM/DD` y fecha-hora ISO
  (`YYYY-MM-DD HH:MM:SS`). Sin eso, una fecha textual con barras se clasifica
  como dimensión y no como fecha.
- Solo se perfilan las columnas que no resolvió el catálogo ni el tipo físico
  fecha. En el caso medido se perfilaron las 7 columnas en 0,64 s; el
  resolvedor solo perfila las 2 que el catálogo no resuelve (`campo_3` y
  `campo_6`), así que el costo real es menor.
- Se abre **un** `connector_engine` por contexto y se cierra al salir. Nada de
  `SELECT *`: el perfilador pide columnas explícitas.
- El resultado se cachea en `budget["profiles:<tabla>"]` durante la request.
  No hay caché persistente (eso es G9, fuera de alcance).
- Columnas `BLOCKED`/`MASKED` ya no llegan acá: `load_fact_context` filtra
  `candidate_cols` (`ml_service.py:204-208`).

**Techo conocido:** el perfil hace agregados de tabla completa (y
`ORDER BY random()` para muestras en tablas ≤ 50.000 filas). Es aceptable para
el panel de ajustes; si una tabla grande vuelve lento el contexto, la salida es
un perfil persistido, no muestrear menos y adivinar.

---

## 4. Granularidad manual (G3)

### 4.1 Aritmética de periodos

En `chat_engine/forecast_calculator.py`, agregar `granularity` con default
`"month"` para no tocar el forecast del chat:

```python
normalize_period(value, granularity="month")  # day: YYYY-MM-DD, week: YYYY-Www, month: YYYY-MM
next_period(period, granularity="month")
period_distance(a, b, granularity="month")
```

- Día: suma/resta 1 día; distancia en días.
- Semana: ISO (`%G-W%V`), suma/resta 7 días, distancia en semanas.
- Mes: la implementación actual.
- `forecast_next_period` sigue siendo mensual: el chat no cambia.

`MIN_PERIODS` se queda en 6 para las tres granularidades. El gate real es la
cantidad de backtests, y la evidencia por paso ya marca `measured_steps <
horizon`. Inventar umbrales por frecuencia sin datos sería otra creencia.

### 4.2 `build_series_sql`

Firma nueva, con parámetros keyword-only y defaults que preservan el contrato:

```python
build_series_sql(
    table, date_col, value_col, dialect="postgres",
    diagnostic_periods=False, *,
    granularity="month", agg="sum",
    date_expr=None, value_expr=None,
)
```

- Expresión de periodo: día `TO_CHAR(expr, 'YYYY-MM-DD')`, semana
  `TO_CHAR(expr, 'IYYY-"W"IW')`, mes `TO_CHAR(expr, 'YYYY-MM')`.
- Agregación: `SUM`, `COUNT(*)`, `COUNT(DISTINCT expr)` y `formula`
  (la expresión del catálogo ya viene agregada; se usa tal cual).
- `income_only = (agg == "sum")`; el filtro `> 0` solo aplica ahí.
  `COUNT(*)`, `COUNT(DISTINCT)` y `formula` no filtran por valor.
- El diagnóstico (`MAX(fecha)`, `COUNT(DISTINCT TO_CHAR(fecha,'YYYY-MM-DD'))`)
  no cambia.

Los builders del desglose (`build_group_totals_sql`,
`build_group_series_sql`) reciben las mismas `date_expr`/`value_expr`/`agg`/
`granularity`. `_build_breakdown` y `_group_values` reciben la resolución y la
granularidad. Sin esto, un desglose sobre una métrica de catálogo TEXT o una
fecha BIGINT fallaría aunque la proyección principal funcione.

### 4.3 Servicio

`ml_service` pasa la granularidad a `_prepare_period_rows`,
`_partial_period`, `_has_gaps`, `_with_periods`, `run_projection` y
`verify_projection`.

- `_partial_period` se generaliza: calcula el periodo en curso según la
  granularidad y compara `dias_con_datos` del periodo actual contra la mediana
  histórica, conservando las reglas 70%/10%. Para día la mediana es 1 y no se
  excluye nada (limitación declarada: no se detecta un día cargado a medias).
- `future_periods_excluded` compara contra el periodo actual de la
  granularidad, no contra `YYYY-MM`.
- La dimensión del desglose se valida contra
  `resolution["dimension_columns"]`; `_is_dimension_col` queda como fallback
  cuando no hay resolución.
- `run_projection` trata `metric_column="*"` como `COUNT(*)`: no lo busca entre
  las columnas físicas ni aplica el motivo de "sin métrica" cuando la tabla
  tiene fecha pero no numéricas.
- `verify_projection` usa `snapshot.granularity or "month"` y
  `snapshot.metric_agg or "sum"`. Un snapshot viejo se verifica mensual/suma,
  como se guardó. Si el snapshot es `count`, relee `COUNT(*)` y no busca `*`
  entre las columnas físicas.

---

## 5. Tabla auto por pregunta y override manual (G5)

### 5.1 Backend

- `forecast_service._list_fact_tables(...)` devuelve **todas** las tablas
  autorizadas con su conteo, cacheado en `budget["fact_tables"]`.
  `_load_fact_table` lo reutiliza y sigue eligiendo la mayor para el forecast
  del chat: el comportamiento actual no cambia.
- `load_fact_context(..., table_name=None, question=None)`:
  - **Manual:** valida `table_name` contra las tablas autorizadas (canónico,
    sin distinguir mayúsculas). Si no está autorizada, `ForecastUnavailable`;
    no hay fallback silencioso a otra tabla.
  - **Auto:** `resolver.pick_table(question, tables, catalog)`. Coincidencia
    por nombre/friendly name/synonyms/descripción; si no hay, la mayor.
  - Devuelve `table_source`: `"manual" | "catalogo" | "mayor"`.
- `load_fact_context` sigue devolviendo `heuristic` (lo usa el forecast del
  chat), pero la proyección usa `resolution`. Para el fallback sin LLM,
  `analyze_question` prefiere los nombres de `DATE_CANDIDATES`/
  `VALUE_CANDIDATES` y, si no, la primera opción de confianza alta de
  `resolution`; no `resolve_columns(candidate_cols)`. Si la pregunta pide
  cantidad/registros/trámites/volumen, elige `COUNT(*)`; no lo elige para
  "proyecta los ingresos".
- `verify_projection` llama a `load_fact_context(..., table_name=snapshot.table)`
  y valida la métrica/fecha del snapshot contra `resolution`
  (`date_options`/`metric_options`/`identifier_options`), **no** contra
  `_is_num_col`/`_is_date_col`. Sin esto, una proyección válida sobre una
  métrica TEXT de catálogo o una fecha BIGINT no se podría verificar.
- Además, `verify_projection` fija la tabla del snapshot. Sin esto, una
  proyección sobre una tabla que no es la mayor dejaría de verificarse en cuanto
  exista el override manual. Es un efecto diferido real, no una mejora opcional.

### 5.2 Regla de UI

- Tabla en **Auto** → los selectores de métrica/fecha/dimensión quedan
  deshabilitados con la leyenda "Elegí una tabla para fijar columnas".
- Tabla **manual** → el contexto se recarga para esa tabla y los selectores se
  habilitan.
- Así nunca se manda una métrica de una tabla junto con otra tabla.

### 5.3 Selectores de métrica y granularidad

- Métrica: `metric_options` + `count_option` + `identifier_options`. La UI
  ofrece suma, conteo y conteo distinto como opciones; una métrica de catálogo
  pre-agregada se ofrece con `formula` ya fijada, sin selector de agregación.
- Granularidad: día/semana/mes, default mes. Es manual: no se infiere de los
  datos. El resultado publica la granularidad usada para que la etiqueta del
  eje no mienta.
- La evidencia traduce `metric_column="*"` a "Cantidad de registros" y muestra
  la agregación usada, no el nombre técnico.

---

## 6. Calidad por reglas (G6)

Se eligió el modelo **B**: un semáforo con reglas sobre evidencia que ya existe,
no un score compuesto. Un score con pesos parecería más preciso sin serlo.

- Constantes en `ml_models.py`, junto a `MIN_BACKTESTS_RELIABLE`
  (`ml_models.py:54`): `UNRELIABLE_MAPE = 50.0` y
  `EXPLORATORY_MAPE = 30.0`.
- Función pura `evaluate_quality(projection, *, horizon, has_gaps)` en
  `ml_models.py`. Recibe lo que ya devuelve `build_projection` y no toca DB ni
  LLM.
- `ProjectionQuality`:

```python
class ProjectionQuality(BaseModel):
    status: str  # "confiable" | "exploratoria" | "no_confiable" | "sin_medicion"
    mape: Optional[float] = None
    unreliable_threshold: Optional[float] = None
    exploratory_threshold: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)
```

Reglas, en orden:

```text
sin_medicion  → mape is None
no_confiable  → mape > UNRELIABLE_MAPE (50)
                OR beats_naive is False
                OR total is None
                OR measured_steps is None
                OR measured_steps < horizon
exploratoria  → mape > EXPLORATORY_MAPE (30)
                OR min_backtests < MIN_BACKTESTS_RELIABLE (4)
                OR beats_naive is None
                OR has_gaps
confiable     → el resto
```

`min_backtests` es el mínimo `n_backtests` entre los pasos publicados con
punto. Un paso extrapolado sin backtest propio cuenta como 0 y baja el estado:
es exactamente la evidencia que falta.

Si `total is None`, la razón es "no se pudo calcular el total del horizonte"; no
se agrega además la de `measured_steps` para no repetir la misma causa.

`reasons` publica todas las condiciones que dispararon el estado ganador. Si el
estado es `no_confiable`, no se listan las de `exploratoria` (el veredicto ya es
el peor); si es `exploratoria`, se listan sus condiciones.

Ejemplos:

| Caso | Estado | Razones |
|---|---|---|
| Demo: MAPE 67,7%, 3/6 pasos, gana al naive | `no_confiable` | MAPE > 50; la banda cubre 3 de 6 pasos |
| MAPE 40%, cobertura 6/6, gana al naive, 5 backtests, sin huecos | `exploratoria` | MAPE entre 30% y 50% |
| MAPE 20%, cobertura 6/6, gana al naive, 5 backtests, sin huecos | `confiable` | — |
| `mape is None` | `sin_medicion` | No hay MAPE medible |

- **Umbrales:** 50/30 son decisiones de producto, no hallazgos; son constantes
  nombradas y se calibran con datos reales después. No se segmentan por
  granularidad todavía (modelo D): no hay evidencia para elegir 40/50/60.
- El backend publica `quality`; el frontend muestra el badge del estado y sus
  razones, no solo un color.
- **Banda del total:** la UI deja de mostrar el rango numérico cuando
  `total.extrapolated` o (`total.measured_steps !== null` y
  `total.measured_steps < horizon`); muestra "Sin banda para el horizonte
  completo". El backend sigue publicando `lower`/`upper` (es dato, no
  presentación) y la verificación no cambia.

---

## 7. Fases

### Fase 1 — Resolvedor (G1, G2, G4)

**Cambios**
- Nuevo `backend/app/modules/ml_forecast/resolver.py`.
- `schema_inspector.py`: `_profile_column` → `profile_column` (mecánico).
- `ml_service.py`: `load_fact_context` y `projection_context` usan el
  resolvedor; `analyze_question` valida overrides contra las opciones
  resueltas, no contra `_is_num_col`/`_is_date_col`.
  - `analyze_question(..., resolution=None)` conserva la clasificación física
    actual cuando no se pasa resolución. Es la ruta de compatibilidad para
    llamadas directas y tests; el router siempre pasa la resolución.
  - `COUNT(*)` viaja como `metric_column="*"` + `metric_agg="count"`;
    `metric_column=None` también se acepta por compatibilidad, pero el
    canónico es `"*"`.
  - `count_distinct` es **manual**: el LLM no lo propone en este plan.
- `schemas.py`: opciones nuevas del contexto y `metric_agg` en el pedido.
- **Adelanto necesario de Fase 2/3:** `build_series_sql` y los builders de
  desglose aceptan `agg`, `date_expr` y `value_expr`; `run_projection` y
  `verify_projection` consumen la resolución. Sin esto, el contexto ofrecería
  columnas de catálogo que la corrida todavía no sabe usar. La granularidad,
  la tabla y la calidad siguen en sus fases.

**Tests**
- `backend/tests/test_ml_resolver_generic.py` (nuevo, 17 tests): clasificación
  pura con cinco esquemas fabricados (fecha DATE + numérica; BIGINT DDMMYYYY +
  TEXT numérico + ID; fechas ISO en TEXT; nombres tipo SAP; sin fecha / sin
  métrica). Sin base de datos.
- Fórmulas del catálogo: válida pasa, inválida se descarta; una fórmula
  agregada sobre una fecha física se descarta; `SUM(misma columna)` es suma
  escalar.
- `COUNT(DISTINCT)` se ofrece como identificador, nunca como `SUM`.
- `test_ml_honesty.py`: SQL de `count`, `count_distinct` y expresiones
  resueltas; los cuatro pasan el AST.
- Los mocks de `CONTEXT`/`ANALYSIS` en `test_ml_projection_audit.py` se
  amplían con `resolution`/`metric_agg`; el router lee con `.get()` para no
  romper un mock viejo.

**Aceptación (verificada el 07/11/2026)**
- En el caso `datos`, `projection_context` devuelve `campo_2`/`campo_5` como
  fechas de confianza alta, `campo_7` como métrica de confianza alta, `campo_3`
  como código numérico de confianza baja, `campo_1`/`campo_4` como
  identificadores, `campo_6` como dimensión y `COUNT(*)`. `campo_2` no aparece
  en métricas.
- En el caso `fact_ventas`, `fecha_venta` vuelve a ser fecha (aunque el
  catálogo tenga un `SUM(fecha_venta)` inválido) y `monto_total`/`cantidad`/
  `costo_total` se resuelven como suma; la corrida mensual reproduce la línea
  base: serie de 9 meses, método `mean`, MAPE 67,7%, total 1.470.466,67 y
  `income_only=True`.
- Contrato HTTP: `ProjectionContextResponse` serializa las opciones nuevas;
  `ProjectionRequest` acepta `metric_agg`; `ProjectionResponse` y la
  verificación publican `metric_agg`.

### Fase 2 — Granularidad manual (G3)

**Cambios**
- `forecast_calculator.py`: aritmética por granularidad.
- `forecast_service.py`: `build_series_sql` y los builders de desglose con
  `granularity` (las expresiones y `agg` ya quedaron en Fase 1).
- `ml_service.py`: propagación de la granularidad a todo el ciclo y a la
  verificación.

**Tests**
- `backend/tests/test_ml_period_granularity.py` (nuevo): día/semana/mes,
  cruces de año, `next_period`, `period_distance`, `normalize_period`.
- SQL exacto para las tres granularidades y las cuatro agregaciones; cada SQL
  pasa `ASTValidator` con la tabla permitida.
- Regresión: la llamada mensual con defaults produce **el mismo SQL** que hoy.
- `_partial_period`: mes (tests existentes), semana, día.
- Desglose con métrica/fecha de catálogo y granularidad diaria: los grupos usan
  la misma expresión y el mismo eje; un grupo sin historia sigue
  `available=False` sin tumbar la principal.

**Aceptación**
- `datos`, `granularity="day"`, `metric_agg="count"`: 31 periodos, 6 pasos,
  SQL con `COUNT(*)` y `TO_DATE`, AST válido.
- `datos`, `granularity="month"`: `available=False` con el motivo
  "Se necesitan al menos 6 periodos para proyectar y hay 1". Semana: hay 5,
  mismo tipo de motivo.
- `fact_ventas` mensual: serie, método, MAPE y pasos idénticos a la línea base
  capturada antes de tocar código.

### Fase 3 — Tabla auto/manual (G5)

**Cambios**
- `_list_fact_tables` + `pick_table` en el resolvedor.
- `load_fact_context(..., table_name, question)`, `projection_context` con
  `table_name`.
- `/projections/context` acepta `table_name`; `/run` pasa `payload.table_name`.
- `verify_projection` fija la tabla del snapshot.

**Tests**
- `test_ml_resolver_generic.py`: matcher de tabla con catálogo, sin catálogo,
  empate y sin coincidencia.
- `test_ml_projection_audit.py`: verificación de snapshot con tabla manual no
  mayor; snapshot viejo sin granularidad sigue verificando mensual/sum;
  verificación de un snapshot con métrica de catálogo (TEXT/BIGINT) usa el
  resolvedor, no los predicados físicos.
- RBAC: tabla no autorizada falla cerrada, sin fallback.

**Aceptación**
- Pregunta con nombre/friendly name de una tabla no mayor → se elige esa.
- Pregunta sin coincidencia → la mayor, declarada `table_source="mayor"`.
- Override inválido → `available=False` con motivo; no se proyecta otra tabla.

### Fase 4 — Calidad por reglas (G6)

**Cambios**
- `UNRELIABLE_MAPE`, `EXPLORATORY_MAPE`, `evaluate_quality` y `quality`
  en la respuesta.
- UI: badge con estado y razones; banda del total condicionada.

**Tests**
- `test_ml_models.py`: un caso por regla con `subTest` — MAPE > 50; MAPE
  30–50; `beats_naive is False`; `beats_naive is None`; `measured_steps <
  horizon`; `total is None`; `min_backtests < 4`; `has_gaps`; `mape is None`;
  y un caso `confiable` con toda la evidencia completa.
- `src/__tests__/projections_page.test.ts`: badge y razones visibles; rango
  del total oculto cuando `measured_steps < horizon`; sin cambios cuando la
  evidencia está completa.

**Aceptación**
- Los cuatro ejemplos de la sección 6 producen el estado esperado.
- La verificación y el snapshot no cambian por este estado.

### Fase 5 — Golden set multi-esquema (G10)

**Cambios**
- Consolidar los tests de las fases 1–4 en el archivo nuevo; no crear un
  framework de fixtures.
- Dejar un caso de regresión con la serie mensual de la demo y su MAPE.

**Aceptación**
- Los cinco esquemas fabricados producen una clasificación esperada y
  `available=False` con motivo en el caso sin fecha/métrica.
- No hay ninguna referencia a `campo_2`, `campo_7` ni `fact_ventas` en el
  código de producción del resolvedor.

---

## 8. Contrato HTTP

### `ProjectionRequest` (agregados, todos opcionales)

```python
table_name: Optional[str] = None
granularity: Optional[Literal["day", "week", "month"]] = None   # None -> "month"
metric_agg: Optional[Literal["sum", "count", "count_distinct", "formula"]] = None
```

Compatibilidad: un cliente viejo no manda nada; se comporta como hoy
(mensual, `sum`, tabla mayor). `metric_agg="count"` usa el canónico
`metric_column="*"` (`None` también se acepta por compatibilidad);
`count_distinct` exige una columna de `identifier_options` y es manual;
`formula` solo aplica a métricas pre-agregadas del catálogo. La `agg` pedida
se valida contra la opción resuelta: no se acepta una agregación distinta de
la que la opción declara.

### `ProjectionContextResponse` (agregados)

```python
tables: List[ProjectionTableOption]          # name, row_count, friendly_name
table_source: Optional[str]
date_options: List[ProjectionColumnOption]
metric_options: List[ProjectionMetricOption]
identifier_options: List[ProjectionMetricOption]
count_option: Optional[ProjectionMetricOption]
granularities: List[str] = ["day", "week", "month"]
default_granularity: str = "month"
min_periods: int = 6
```

Se conservan `metric_columns`, `date_columns` y `dimension_columns` con las
opciones de confianza alta, para no romper clientes ni tests existentes.

### `ProjectionResponse` (agregados)

```python
granularity: str = "month"
metric_agg: str = "sum"
table_source: Optional[str] = None
quality: Optional[ProjectionQuality] = None
```

`ProjectionVerificationResponse` agrega `granularity` y `metric_agg`
opcionales para etiquetar los periodos verificados.

---

## 9. Archivos

| Archivo | Cambio |
|---|---|
| `backend/app/modules/ml_forecast/resolver.py` | **Nuevo.** Clasificación, matcher de tabla, carga de catálogo/perfil. |
| `backend/app/modules/ml_forecast/ml_service.py` | Integración del resolvedor, granularidad, tabla, calidad y verificación. |
| `backend/app/modules/ml_forecast/schemas.py` | Pedido, contexto, respuesta y calidad. |
| `backend/app/modules/ml_forecast/router.py` | `table_name` en contexto; paso de tabla/granularidad/agg. |
| `backend/app/modules/chat_engine/forecast_service.py` | `_list_fact_tables`; `build_series_sql` con granularidad/agg/expresiones. |
| `backend/app/modules/chat_engine/forecast_calculator.py` | Aritmética de periodos por granularidad. |
| `backend/app/modules/catalog/services/schema_inspector.py` | Rename `_profile_column` → `profile_column`; patrones de fecha adicionales en `_infer_pattern`. |
| `src/features/projections/services/projection_service.ts` | Tipos y payload nuevos. |
| `src/pages/ProjectionsPage.tsx` | Selectores de tabla y granularidad; opciones del resolvedor. |
| `src/features/projections/components/ProjectionResults.tsx` | Calidad, granularidad/agg y banda del total condicionada. |
| `backend/tests/test_ml_resolver_generic.py` | **Nuevo.** Multi-esquema. |
| `backend/tests/test_ml_period_granularity.py` | **Nuevo.** Periodos y SQL. |
| `backend/tests/test_ml_honesty.py`, `test_ml_projection_audit.py`, `test_ml_service_rbac.py`, `test_ml_models.py` | Ajustes de contrato y regresión. |
| `src/__tests__/projections_page.test.ts` | Fixtures y conducta nueva. |

---

## 10. Criterios de aceptación

1. **Genérico:** `resolver.py` no contiene `campo_2`, `campo_7`, `datos` ni
   `fact_ventas`. Los nombres genéricos existentes (`DATE_CANDIDATES`,
   `VALUE_CANDIDATES`) siguen siendo heurísticas, no ramas.
2. **Tu base desbloqueada:** contexto de `datos` con fecha `campo_2`
   (catálogo), métrica `campo_7` (catálogo), identificadores `campo_1` y
   `campo_4`, código `campo_3` de confianza baja y `COUNT(*)`.
3. **Proyección diaria real:** `datos` + `campo_2` + `count` + `day` +
   horizonte 6 → `available=True`, `n_periods=31`, 6 pasos con banda y MAPE,
   SQL validado por AST.
4. **Motivo honesto:** `datos` mensual → 1 periodo, `available=False` con
   "se necesitan al menos 6 periodos"; semanal → 5, igual.
5. **Sin regresión en la demo:** `fact_ventas` mensual/suma reproduce la misma
   serie, método, MAPE y pasos que la línea base capturada antes de cambiar
   código.
6. **Tabla:** auto por pregunta con catálogo; fallback a la mayor; override
   manual validado; verificación sobre la tabla del snapshot.
7. **Snapshots viejos:** sin `granularity`/`metric_agg` se verifican como
   mensual/suma.
8. **Calidad:** reglas de la sección 6. Demo → `no_confiable` por MAPE y
   cobertura; MAPE 40% con cobertura completa → `exploratoria`; MAPE 20%,
   cobertura completa, gana al naive, ≥ 4 backtests y sin huecos → `confiable`;
   `None` → `sin_medicion`. La UI muestra el estado y las razones.
9. **Multi-esquema:** los cinco casos fabricados pasan sin base de datos.
10. **Definición de terminado:** `pytest`, `npm test`, `tsc --noEmit` y
    `npm run build` verdes, con la salida real pegada en el informe.

---

## 11. Verificación

Antes de tocar código, capturar la línea base real (no la de `AGENTS.md`, que
está desactualizada: declara 888 tests y la evaluación del 07/11/2026 midió
925 passed + 246 subtests):

```powershell
cd backend
venv\Scripts\python.exe -m pytest -q
cd ..
npx tsc --noEmit
npm test
npm run build
```

Después de cada fase, correr los tests de la fase. Al cerrar, correr los cuatro
comandos completos y pegar su salida. Si un comando no se corrió, decirlo; no
declararlo verde por inferencia.

Prueba manual de aceptación (con la conexión real):

```text
contexto: tabla datos, fecha campo_2, métrica COUNT(*), granularidad día
resultado: available=True, 31 periodos, 6 pasos, MAPE y banda publicados
```

---

## 12. Fuera de alcance

- **G7 — costos/márgenes:** el filtro `> 0` sigue aplicando a `sum` y
  `income_only` sigue declarándolo. No se clasifica la métrica como ingreso,
  costo o margen en este plan.
- **G8 — `seasonal_naive`:** no se agrega hasta tener ≥ 24 periodos reales.
- **G9 — caché persistente de permisos/conteos/perfiles:** solo el `budget`
  de la request.
- **LLM elige tabla:** se evalúa después de medir el matcher determinista.
- **MySQL/SQL Server:** Datia sigue hablando solo PostgreSQL.
- **Refactor del forecast del chat o de retención:** solo se preserva su
  comportamiento con los defaults mensuales.
- **Walk-forward, regla naive, bandas, auditoría, UI de evidencia:** no se
  tocan.
- **Commits:** no se commitea sin indicación explícita del responsable.

---

## 13. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Una fórmula de catálogo es incorrecta o nombra una columna bloqueada | Se valida con AST al resolver; si falla seguridad/sintaxis, se descarta. Si es incompatible con el motor, falla como error auditado: AST no valida tipos. |
| Un código de 8 dígitos se confunde con fecha | Identificador primero, rango válido obligatorio, confianza media y selector manual. |
| El perfil es caro en tablas grandes | Solo columnas no resueltas, caché por request y un solo engine. Techo declarado: perfil persistido (G9). |
| La generalización de `_partial_period` cambia el mes actual de la demo | Default mensual + comparación exacta contra la línea base y tests de las tres granularidades. |
| `COUNT(DISTINCT)` es caro en tablas enormes | Solo se ejecuta si el usuario lo elige; no entra al camino automático. |
| Los umbrales 30/50 son arbitrarios | Son constantes nombradas; cada estado publica sus razones y se calibran con datos reales, no se esconden. |
| El override manual de tabla rompe la verificación histórica | `verify_projection` usa la tabla del snapshot, no la mayor. |

---

## 14. Resultado de la Fase 1 (07/11/2026)

**Implementado**
- `backend/app/modules/ml_forecast/resolver.py`: clasificación catálogo >
  perfil > tipo físico, fórmulas validadas con AST, `COUNT(*)`, identificadores
  y opciones con evidencia.
- `SchemaInspector.profile_column` (antes privado) y patrones nuevos de fecha
  con barras y fecha-hora ISO.
- `load_fact_context`/`projection_context` publican `resolution` y las
  opciones; `analyze_question` valida contra ellas; `COUNT(*)` y
  `count_distinct` viajan con `metric_agg`.
- `build_series_sql` y los builders de desglose aceptan `agg`, `date_expr` y
  `value_expr`; `run_projection` y `verify_projection` los consumen.
- Se adelantó el consumo de expresiones de Fase 2 y la validación de
  verificación de Fase 3: sin eso, el contexto ofrecía columnas que la corrida
  todavía no sabía usar. La granularidad, la tabla y la calidad siguen en sus
  fases.

**Evidencia real (conexión activa y demo)**

`datos` (conexión 4):

```text
fechas:      campo_2 (catalogo, alta), campo_5 (catalogo, alta)
metricas:    campo_7 (catalogo, alta), campo_3 (perfil, baja; codigo 1..69)
identificadores: campo_1 (count_distinct), campo_4 (count_distinct)
dimensiones: campo_6
COUNT(*):    Cantidad de registros
```

`fact_ventas` (conexión 1):

```text
fechas:      fecha_venta (tipo, alta)   # aunque el catalogo tenga SUM(fecha_venta)
metricas:    cantidad, monto_total, costo_total (suma), margen_ganancia (perfil)
identificadores: id_venta, id_producto, id_cliente (count_distinct)
dimensiones: metodo_pago, estado
COUNT(*):    Cantidad de registros
```

Corrida mensual de la demo: 9 periodos, método `mean`, MAPE 67,7%,
`beats_naive=True`, total 1.470.466,67, `income_only=True` y el mismo SQL de
`SUM("monto_total") WHERE "monto_total" > 0` que la línea base.

Corrida `datos` con `COUNT(*)` y `campo_2` en la Fase 1 (todavía mensual):
`available=False` con "Se necesitan al menos 6 periodos para proyectar y hay
1."; la serie sí viaja (1 periodo, 38.180 registros), el SQL usa `COUNT(*)` y
`TO_DATE(LPAD(...))` y pasa el AST. La Fase 2 lo vuelve viable en diario.

**Verificación**

- `test_ml_resolver_generic.py`: 17 passed.
- Suite dirigida (`ml_honesty`, `ml_projection_audit`, `ml_service_rbac`,
  `ml_resolver_generic`, `catalog_enricher_profiles`, `prediction_honesty`):
  113 passed.
- Suite completa: **946 passed, 246 subtests, 405,10 s** (línea base previa:
  925 passed, 246 subtests, 394,11 s; los 21 tests nuevos son los 17 del
  resolvedor y los 4 de SQL de conteo/expresiones).
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / 136 tests;
  `npm run build` exit 0 (warning de echarts 704,15 kB esperado).

**Hallazgo de calidad del catálogo:** las fórmulas generadas por IA de la demo
incluyen `SUM(fecha_venta)` para una columna DATE y `friendly_name="Ingreso
Corporativo"` para `fecha_venta`. La fórmula se descarta por incompatibilidad
con el tipo físico; el nombre de negocio sigue siendo engañoso y conviene
revisarlo en Administración. No se corrige desde el código: el catálogo es
metadata administrada.

---

## 15. Resultado de la Fase 2 (07/11/2026)

**Implementado**
- `forecast_calculator.py`: `normalize_period`, `next_period` y
  `period_distance` con `granularity` (`day`/`week`/`month`); el default
  mensual conserva el contrato del chat y de retención.
- `build_series_sql` y `build_group_series_sql`: expresión de periodo por
  granularidad (`YYYY-MM-DD`, `IYYY-"W"IW`, `YYYY-MM`).
- `ml_service`: `_prepare_period_rows`, `_partial_period`, `_has_gaps`,
  `_with_periods`, `_group_values`, `run_projection`, `_build_breakdown` y
  `verify_projection` propagan la granularidad.
- `ProjectionRequest.granularity`; `ProjectionResponse.granularity` y la
  verificación lo publican. La UI agrega el selector Día/Semana/Mes y lo manda
  solo cuando no es el default mensual.

**Evidencia real**

`datos` diario, `COUNT(*)`, `campo_2`, horizonte 6:

```text
available=True · n_periods=31 · has_gaps=False
6 pasos: 2012-01-01 .. 2012-01-06
método mean · MAPE 471,3% · total 7.389,68 · banda del total 35,9%
measured_steps=6 · extrapolated=False
SQL: TO_CHAR(TO_DATE(LPAD(...)), 'YYYY-MM-DD') + COUNT(*)
```

El MAPE diario es altísimo: el conteo de trámites por día es ruidoso. El motor
lo publica con su banda; la Fase 4 lo marcará `no_confiable` por MAPE y evitará
presentar el rango como una medición fina.

`datos` semanal: 5 periodos → `available=False` con "hay 5". `datos` mensual:
1 periodo → "hay 1". La limitación es de historia, no del motor.

`fact_ventas` mensual: 9 periodos, `mean`, MAPE 67,7%, total 1.470.466,67,
`income_only=True`; misma línea base que la Fase 1.

**Verificación**

- `test_ml_period_granularity.py`: 13 passed (incluye la matriz 3
  granularidades × 4 agregaciones con 12 subtests).
- Suite dirigida de proyecciones: 113 passed.
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / **137 tests**
  (el test nuevo de granularidad); `npm run build` exit 0 (warning de echarts
  704,15 kB esperado).
- Suite completa: **959 passed, 258 subtests, 404,36 s** (línea base previa:
  946 passed, 246 subtests, 405,10 s; los 13 tests y 12 subtests nuevos son de
  `test_ml_period_granularity.py`).

---

## 16. Resultado de la Fase 3 (07/11/2026)

**Implementado**
- `forecast_service._list_fact_tables` (todas las tablas autorizadas con filas,
  cacheadas por request) y `_load_table_columns`; `_load_fact_table` conserva la
  tabla mayor para el forecast del chat.
- `resolver.pick_table`, `table_catalog` y `public_tables`: elección por nombre
  de tabla, `friendly_name`/synonyms/description y pregunta; sin coincidencia,
  la mayor. En empate prioriza la tabla de hechos sobre `dim_*`.
- `load_fact_context(..., table_name, question)`: override manual validado
  contra las autorizadas (sin fallback), auto por pregunta y `table_source`
  (`manual`/`catalogo`/`mayor`); devuelve `tables`.
- `verify_projection` carga la tabla del snapshot: una proyección sobre una
  tabla que no es la mayor ya no falla con "la tabla cambió".
- Contrato: `ProjectionRequest.table_name`, `ProjectionResponse.table_source`,
  `ProjectionContextResponse.tables`/`table_source`; `/projections/context`
  acepta `table_name`.
- UI: selector de tabla (Auto + lista con filas), contexto recargado al elegir,
  columnas de métrica/eje/desglose deshabilitadas en Auto y `table_name` en el
  pedido. La evidencia muestra tabla y origen.
- Robustez: una fórmula de catálogo que pasa el AST pero falla en el motor se
  convierte en `available=False` con motivo (no 500); el desglose degrada por
  grupo. Se reconoce `DATE(col)` como fórmula de fecha y se descarta sobre una
  numérica sin cast (`DATE(anio)`).

**Evidencia real (demo)**

```text
"Proyecta las ventas..."        → fact_ventas (catalogo)
"Proyecta los ingresos..."      → fact_ingresos_costos (catalogo, 8 filas; no la mayor)
"Proyecta la cantidad..."       → fact_ventas (mayor)
manual fact_ingresos_costos     → manual
tabla inexistente               → "no esta autorizada o no tiene filas"
```

`fact_ingresos_costos` es la tabla correcta para "ingresos", pero su columna
`mes` contiene "Enero", "Febrero"... y la fórmula de catálogo `DATE(mes)` no es
válida. La corrida devuelve `available=False` con el motivo del catálogo, no un
500. `DATE(anio)` se descarta por ser una numérica sin cast. Es un hallazgo de
calidad de catálogo: la tabla queda elegida y bloqueada hasta corregir la
fórmula, en vez de caer silenciosamente a otra tabla.

`fact_ventas` sigue proyectando: 9 periodos, `mean`, MAPE 67,7%, total
1.470.466,67.

**Verificación**

- `test_ml_resolver_generic.py`: 22 passed (5 de selección de tabla).
- `test_ml_service_rbac.py`: 19 passed (tabla manual autorizada, no autorizada,
  contexto manual y verificación sobre una tabla no mayor).
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / **138 tests**;
  `npm run build` exit 0 (warning de echarts 704,15 kB esperado).
- Suite completa: **968 passed, 258 subtests, 459,51 s** (línea base previa:
  959 passed, 258 subtests, 404,36 s; los 9 tests nuevos son los de selección
  de tabla y tabla manual).

---

## 17. Resultado de la Fase 4 (07/11/2026)

**Implementado**
- `ml_models.evaluate_quality`: reglas explícitas, sin score ni pesos.
  `UNRELIABLE_MAPE = 50`, `EXPLORATORY_MAPE = 30`.
- `ProjectionQuality` en `schemas.py` y `ProjectionResponse.quality`:
  `status`, `mape`, umbrales y `reasons`.
- `run_projection` publica el estado; la verificación no cambia.
- UI: badge de calidad en el total, razones visibles y rango/banda del total
  ocultos cuando `extrapolated` o `measured_steps < horizon`. La evidencia
  sigue mostrando el punto del total y la explicación.
- La banda por paso y el total no se tocaron: el estado es una lectura de la
  evidencia, no un recorte del cálculo.

**Reglas verificadas**

| Caso | Estado | Razón principal |
|---|---|---|
| Demo `fact_ventas` (MAPE 67,7%, 3/6 pasos) | `no_confiable` | MAPE > 50 y cobertura incompleta |
| `datos` diario (MAPE 471,3%, 6/6, 20 backtests) | `no_confiable` | MAPE > 50 |
| MAPE 40%, cobertura completa, 5 backtests | `exploratoria` | MAPE entre 30 y 50 |
| MAPE 20%, cobertura completa, 5 backtests, sin huecos | `confiable` | — |
| MAPE `None` | `sin_medicion` | No hay MAPE medible |

También se cubren `beats_naive=False`, `beats_naive=None`, `total=None`,
`measured_steps=None`, `measured_steps < horizon`, `min_backtests < 4` y
`has_gaps`.

**Verificación**

- `test_ml_quality.py`: 12 passed.
- Suite dirigida de proyecciones: 156 passed.
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / **140 tests**
  (badge/razones y total ausente); `npm run build` exit 0 (warning de echarts
  704,15 kB esperado).
- Suite completa: **980 passed, 258 subtests, 415,49 s** (línea base previa:
  968 passed, 258 subtests, 459,51 s; los 12 tests nuevos son de
  `test_ml_quality.py`).

**Pendiente declarado y resuelto después:** la banda de un conteo ya no publica
un límite inferior negativo: `run_projection` acota `lower` a 0 para
`count`/`count_distinct`, marca `lower_clamped` y la UI lo explica. El
`band_pct` sigue siendo el residuo medido. Ver el cierre de pendientes (sección
19).

---

## 18. Resultado de la Fase 5 (07/11/2026)

**Implementado**
- Golden set en los archivos existentes, sin framework de fixtures nuevo:
  - `test_ml_resolver_generic.py`: esquema clásico DATE + NUMERIC, el caso
    BIGINT DDMMYYYY + TEXT numérico + ID, fechas ISO en TEXT, esquema tipo SAP
    (`BUDAT`/`WRBTR`/`BUKRS`/`BELNR`) y el caso sin fecha o sin métrica con su
    motivo.
  - Regresión de la serie mensual de la demo: `mean`, MAPE 67,7%, total
    1.470.466,67 y `measured_steps=3`.
  - Guarda estática: `resolver.py` no nombra `campo_2`, `campo_7` ni
    `fact_ventas`.
- Refinamiento descubierto por el caso SAP: una numérica **decimal**
  (`NUMERIC`/`DECIMAL`/`MONEY`/`FLOAT`) con 100% de valores distintos sigue
  siendo una métrica; un ID suele ser entero o texto. Un decimal con nombre de
  identificador o PK sigue siendo identificador.
- Los tests de granularidad y calidad siguen en sus archivos enfocados
  (`test_ml_period_granularity.py`, `test_ml_quality.py`); no se duplicaron.

**Aceptación**
- Los cinco esquemas fabricados producen la clasificación esperada.
- Sin fecha o sin métrica, la corrida falla con el motivo que nombra el eje
  faltante; el router lo publica como `available=False`.
- `resolver.py` no contiene columnas ni tablas concretas.

**Verificación**
- `test_ml_resolver_generic.py`: 29 passed.
- Suite dirigida de proyecciones: 163 passed.
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / **140 tests**;
  `npm run build` exit 0 (warning de echarts 704,15 kB esperado).
- Suite completa: **987 passed, 258 subtests, 431,96 s** (línea base previa:
  980 passed, 258 subtests, 415,49 s; los 7 tests nuevos son del golden set).

---

## 19. Cierre de pendientes declarados (07/11/2026)

**Banda de conteos**
- `run_projection` acota `lower` a 0 para `count`/`count_distinct` en pasos y
  total; publica `lower_clamped` en ambos.
- El `band_pct` no se altera: sigue siendo el residuo medido. La UI muestra
  “Banda inferior acotada a 0: una cantidad no puede ser negativa” y una nota
  equivalente en la tabla de pasos.
- Evidencia real: `datos` diario, 6 pasos con `lower=0` y `lower_clamped=True`;
  el total (7.389,68) conserva su banda positiva.

**UI de métricas**
- El selector expone `metric_options` (con `label`), `COUNT(*)` y
  `COUNT(DISTINCT col)` de los identificadores. El pedido viaja con
  `metric_column="*"`/`metric_agg="count"` o
  `metric_agg="count_distinct"`.
- Sin `metric_options` (backend viejo) se conserva el fallback a
  `metric_columns`.

**Tests**
- Backend: 3 tests de clamp (`count`, `count_distinct`, `sum` no se toca).
- Frontend: casos de nota de clamp y de selección `COUNT(*)` con
  `metric_agg="count"`.

**Verificación**
- Suite completa: **990 passed, 258 subtests, 439,39 s** (línea base previa:
  987 passed, 258 subtests, 431,96 s; los 3 tests nuevos son del clamp).
- Frontend: `npx tsc --noEmit` exit 0; `npm test` 18 archivos / **142 tests**;
  `npm run build` exit 0 (warning de echarts 704,15 kB esperado).






