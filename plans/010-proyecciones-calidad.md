# Plan 010 — Proyecciones de calidad

**Fecha:** 12/11/2026
**Estado:** ✅ Fases 1 y 2 verificadas; fase 3 parcial (narrativa determinista y política única, 12/11/2026)
**Objetivo:** subir la calidad real del módulo Proyecciones y quitar el hardcodeo
que hoy dispersa umbrales, horizontes y supuestos. Debe funcionar con cualquier
esquema y con series cortas, sin prometer precisión que la historia no sostiene.

## Alcance de la fase 1

1. **Política única** (`ml_forecast/policy.py`): horizontes, períodos mínimos,
   umbrales de calidad, ciclos estacionales y límites de escenario en un solo
   lugar. El backend publica la política para que la UI no repita "36" ni "6".
2. **Backtest robusto y comparación de modelos:** MAE, RMSE y MASE además de
   MAPE; selección por MASE con desempate por MAPE/simplicidad; tabla de
   comparación por método. La banda sigue siendo conservadora y declarada.
3. **Diagnóstico multi-granularidad y calidad de datos:** estacionalidad para
   día/semana/mes/trimestre, tendencia con significancia, outliers robustos,
   cambios de nivel, huecos y horizontes recomendado/confiable derivados del
   largo de la serie.
4. **Series cortas:** la respuesta nunca es un 500 ni un dead end. Si no alcanza
   para un pronóstico estadístico, se publica el diagnóstico, el mínimo de
   historia y una guía para usar un escenario de crecimiento.
5. **Evaluación y tests:** matriz determinista de preguntas/escenarios por
   granularidad; tests de propiedades para métricas; suite completa al cierre.

**Fuera de alcance de esta fase:** pandas o dependencias nuevas, modelos
neuronales, migración a `<dialog>`, cambios de frontend no relacionados.

## Contrato nuevo (compatible hacia atrás)

Todos los campos nuevos son opcionales o tienen default para no romper clientes.

- `ProjectionResponse.policy`: `min_horizon`, `max_horizon`,
  `max_scenario_horizon`, `default_horizon`, `min_periods`,
  `reliable_backtests`, `exploratory_mape`, `unreliable_mape`.
- `ProjectionResponse.metrics_by_method`: `{metodo: {mape, mae, rmse, mase}}`.
- `ProjectionResponse.model_comparison`: lista de
  `{method, mape, mae, rmse, mase, selected}`.
- `ProjectionResponse.mase`, `mae`, `rmse` del método publicado.
- `ProjectionResponse.max_horizon`, `min_periods`, `reliable_horizon`,
  `can_forecast`, `guidance`.
- `ProjectionRequest.scenario_growth_pct`: escenario explícito del cliente,
  validado contra la política (sin depender de parsear la pregunta).
- `ProjectionDiagnosis`: `season_length`, `seasonality_strength`,
  `trend_pct_per_step`, `level_shift`, `level_shift_pct`, `outlier_count`,
  `missing_periods`, `usable_periods`, `reliable_horizon`.
- Se mantienen `mape`, `mape_by_method`, `method`, `beats_naive`, `quality` y
  `max_recommended_horizon` por compatibilidad.

## Frentes y propiedad de archivos

### Frente A — Modelos y métricas
**Archivos:** `backend/app/modules/ml_forecast/ml_models.py`,
`backend/tests/test_ml_models_quality.py` (nuevo).
**No tocar:** `policy.py`, `diagnosis.py`, `ml_service.py`, `router.py`,
`schemas.py`, frontend.

- Importar `POLICY`; eliminar umbrales duplicados.
- Calcular MAE/RMSE/MASE por método y por paso; MASE escala con el error naive
  in-sample (media de |diferencias|). Si la escala es 0, MASE = None.
- Seleccionar por MASE cuando esté disponible; si no, MAPE. `beats_naive` solo
  si el método distinto de naive mejora ESTRICTAMENTE la misma métrica.
- Agregar `metrics_by_method`, `model_comparison`, `mase`, `mae`, `rmse` al
  payload. Mantener `mape_by_method` y `mape`.
- Tests: recta, plana, alternante, ceros, Decimal/int/float, serie corta,
  selección determinista, comparación con exactly un `selected`.

### Frente B — Diagnóstico y calidad de datos
**Archivos:** `backend/app/modules/ml_forecast/diagnosis.py`,
`backend/tests/test_ml_diagnosis_quality.py` (nuevo).
**No tocar:** `policy.py`, `ml_models.py`, `ml_service.py`, `schemas.py`.

- Importar `POLICY`; eliminar constantes duplicadas.
- Estacionalidad por granularidad: día = día de semana; semana = mes del año;
  mes = mes; trimestre = trimestre. Mínimo = `2 * ciclo`. Fuerza con eta²
  (`seasonality_min_eta2`) y publicar `seasonality_strength`.
- Tendencia: pendiente OLS + error estándar/t; `trend_min_t` y
  `trend_threshold_pct`; publicar `trend_pct_per_step`.
- Outliers: MAD/robust z (`outlier_z`), `outlier_count`; no confundir NULL con 0.
- Cambio de nivel: últimos `level_shift_window` contra la ventana anterior,
  `level_shift` y `level_shift_pct`; solo si hay puntos suficientes.
- Huecos: `missing_periods` usando la aritmética de períodos existente.
- `max_recommended_horizon = POLICY.max_horizon_for(n)` y
  `reliable_horizon = POLICY.reliable_horizon_for(n)`; `usable_periods = n`.
- Resumen en lenguaje simple; no afirmar estacionalidad/tendencia sin evidencia.
- Tests: mensual, trimestral, semanal, diario, plana, tendencia, escalón,
  outlier, huecos, series de 1–2 puntos.

### Frente C — Servicio, router y contrato
**Archivos:** `backend/app/modules/ml_forecast/ml_service.py`,
`backend/app/modules/ml_forecast/router.py`,
`backend/app/modules/ml_forecast/schemas.py`,
`backend/tests/test_ml_projection_resilience.py` (nuevo).
**No tocar:** `policy.py`, `ml_models.py`, `diagnosis.py`, frontend.

- `schemas.py`: importar `POLICY`; límites/default desde la política; agregar
  los campos del contrato y `scenario_growth_pct` con validador de rango.
- `ml_service.py`: usar `POLICY` para `BREAKDOWN_TOP_N`, `steps_per_year`,
  período parcial, columnas de prompt y deduplicación de escenarios.
- `_growth_scenarios(..., granularity)`: el rezago interanual es
  `POLICY.steps_per_year(granularity)`, no 12 fijo. Actualizar llamadas y la
  docstring vieja de "5/10/15%".
- `run_projection`: publicar `policy`, `max_horizon`, `min_periods`,
  `reliable_horizon`, `can_forecast`, `guidance`, `model_comparison` y métricas.
  Si `n_periods < min_periods`, `guidance` debe explicar que no hay pronóstico
  estadístico y ofrecer un escenario; no inventar banda.
- Router: aceptar `scenario_growth_pct` explícito (si viene, manda sobre el texto)
  y pasarlo; mantener el modo escenario con horizonte hasta `max_scenario_horizon`.
- Tests: escenario trimestral/semanal con rezago correcto; respuesta con
  política; serie corta con guía; request con escenario explícito; compatibilidad
  con `mape_by_method`.

### Frente D — Frontend
**Archivos:** `src/features/projections/services/projection_service.ts`,
`src/features/projections/components/ProjectionResults.tsx`,
`src/pages/ProjectionsPage.tsx`,
`src/__tests__/projections_page.test.ts`.
**No tocar:** backend.

- Tipos nuevos opcionales.
- `horizonSourceLabel` usa `result.policy.max_horizon` (fallback 36), no el
  literal.
- Detalle técnico: tabla de comparación de modelos (MAPE/MAE/RMSE/MASE,
  seleccionado) y bloque de calidad de la serie (huecos, outliers, cambio de
  nivel, horizonte confiable).
- Estado sin pronóstico: mostrar `guidance` y una acción para probar un
  escenario; si el control de escenario es demasiado, al menos el texto y el
  ejemplo "10% anual".
- Tests: fixtures con política/métricas/diagnóstico; etiqueta de horizonte;
  comparación de modelos; guía en serie corta; compatibilidad sin campos nuevos.

### Frente E — Evaluación determinista y tester
**Archivos:** `backend/tests/test_projection_question_matrix.py` (nuevo),
`backend/tests/test_ml_projection_eval.py` (nuevo si aporta),
`backend/scripts/eval_projection_questions.py` (opcional, manual).
**No tocar producción.**

- Matriz de preguntas: horizonte ("6 meses", "un año", "10 años"), escenario
  ("10% anual"), granularidad y métrica; afirmar que el escenario usa el rezago
  correcto por granularidad.
- Tests de propiedades para métricas: MASE no negativo, MAE <= RMSE, banda
  contiene el punto, misma serie mismo payload.
- Correr suite completa, tsc, build; reportar salida real. Si un test falla por
  un defecto de producción, reportarlo; no arreglar producción desde este rol.

### Frente F — Revisión independiente
**Sin edición.** Revisar el diff contra los pilares (`datia-pilares`), el
hardcodeo restante y las regresiones. Veredicto y riesgos.

## Criterios de aceptación

- `pytest`, `npm test`, `tsc --noEmit` y `npm run build` verdes.
- La respuesta de una serie corta (1–5 períodos) no es 500 y explica qué falta.
- Una serie de 6+ períodos publica método, métricas y comparación deterministas.
- Trimestral y semanal usan su propio rezago estacional/interanual.
- La UI no muestra "36" hardcodeado si el backend publica otro máximo.
- No se agregaron dependencias.

## Resultado de la fase 1 (12/11/2026)

Se ejecutaron los cinco frentes con tests y verificación real:

- **Política única:** `policy.py` centraliza horizontes, mínimos, umbrales,
  ciclos estacionales y límites de escenario. `ProjectionResponse.policy` y
  `ProjectionContextResponse` los publican; la UI ya no usa "36" como fuente.
- **Backtest robusto:** `ml_models.py` publica MAE, RMSE y MASE además de MAPE,
  `metrics_by_method` y `model_comparison`; la selección usa MASE y conserva
  `beats_naive`. La banda sigue siendo el peor residuo por paso.
- **Diagnóstico multi-granularidad:** estacionalidad por día/semana/mes/trimestre,
  tendencia con t, outliers robustos (MAD), cambio de nivel, huecos, periodos
  usables y horizontes recomendado/confiable derivados del largo de la serie.
- **Series cortas:** 1–5 períodos devuelven diagnóstico, motivo y guía para usar
  un escenario; el escenario explícito (`scenario_growth_pct`) funciona con 1+
  períodos y no inventa banda, MAPE ni calidad.
- **Frontend:** muestra comparación de modelos, calidad de la serie y guía;
  `level_shift=false` ya no se pinta como “Cambio de nivel”.
- **Corrección del revisor:** `evaluate_quality` ya no marca `no_confiable` a
  `naive` por “no superarse a sí mismo”.

Evidencia real:

```text
pytest:   1091 passed, 264 subtests (8:27)
vitest:   23 archivos / 180 tests
tsc:      exit 0
build:    OK
e2e:      10 passed + 1 skipped (ranking sin desglose en datos demo)
```

**Sin dependencias nuevas.** No se agregó pandas, statsmodels ni aeon al runtime:
las skills de ML se usaron como guía y el cálculo sigue con numpy/stdlib.

## Fase 2 — Calibración de intervalos (12/11/2026)

La banda dejó de ser un único peor caso y pasó a un intervalo central
calibrado por cobertura, con el peor caso publicado aparte:

- **Política:** `policy.py` agrega `interval_coverage=0.80`,
  `interval_min_residuals=8`, `interval_coverage_tolerance=0.10` y
  `interval_use_worst_case_on_shift=True`. El objetivo viaja en la respuesta
  (`interval_target`): ni el backend ni la UI repiten el 80.
- **Intervalo por paso:** `_calibrated_interval` ordena los residuos con signo
  y elige un cuantil empírico con corrección finita (rangos sobre `n+1`),
  asimétrico. Con menos de `interval_min_residuals` o un cambio de nivel se
  publica el peor caso (`max_residual`); el intervalo siempre contiene el punto
  y, si hay sesgo total, el lado opuesto se colapsa al punto.
- **Cobertura honesta:** `_walk_forward_coverage` mide con intervalos
  calculados solo con residuos anteriores. La medición es incremental (prefijo
  ordenado por inserción): una serie diaria de 1500 puntos pasó de 9,3 s a
  1,1 s sin cambiar la cobertura publicada.
- **Contrato:** pasos y total publican `band_method`, `interval_lower_pct`,
  `interval_upper_pct`, `interval_coverage`, `interval_coverage_n`,
  `interval_target` y `worst_case_pct`. `band_pct` se conserva como envolvente
  simétrica para clientes viejos; los pasos extrapolados heredan el intervalo
  y se declaran `band_method: carried`.
- **Calidad:** `evaluate_quality` agrega una razón exploratoria cuando la
  cobertura walk-forward queda más de la tolerancia debajo del objetivo.
- **UI:** cobertura “cob.” por paso en el detalle técnico; el total declara si
  la banda es calibrada (con objetivo, cobertura observada, número de
  comparaciones y peor caso) o conservadora. El escenario sigue sin semáforo.

Evidencia real:

```text
pytest:   1099 passed, 264 subtests (6:09)
vitest:   23 archivos / 183 tests
tsc:      exit 0
build:    OK (warning de chunk de echarts ~704 kB, esperado)
e2e:      11 passed (1,6 min; el ranking se ejecutó con los datos demo)
```

### Fase 3 — Lectura determinista y política única (parcial, 12/11/2026)

La narrativa libre del LLM era la última fuente de errores: inventó unidades
(“2,96 mil millones” por 296 millones), confundió cobertura con confianza y
describió la media como “promedio ponderado”. Se retiró esa superficie:

- **Narrativa determinista:** `narrate` publica la plantilla armada con el
  payload. Nombra el método con `method_label`/`method_description`, cuenta los
  métodos probados desde `mape_by_method`, usa la unidad de la granularidad y
  cita solo totales/errores publicados. No hay prosa libre que validar ni cifras
  que puedan reescalarse.
- **Registro de métodos:** `METHOD_LABELS`/`METHOD_DESCRIPTIONS` son la única
  fuente; el payload publica `method_label`, `method_description` y `label` por
  fila. La UI los consume y deja el mapa local solo como compatibilidad.
- **Política única:** se movieron a `policy.py` los tokens y tiempos de la
  estimación (que ahora cuenta UNA llamada al LLM), los topes de calibración, el
  ejemplo de escenario, los cuantiles histórico/conservador/optimista, los
  pesos y umbrales del resolvedor y los límites del historial. El parser de
  escenarios usa el rango de la política (hasta 1000%), no un 100 fijo. El
  fallback de métrica/fecha usa la primera opción del resolvedor por evidencia,
  no una lista de nombres de columna. La UI ya no tiene un `36` de respaldo: usa
  `policy.max_horizon` o `max_horizon` de la respuesta.
- **Lexicon de idioma:** el vocabulario de respaldo (conteos, ganancias, nombres
  de métrica/identificador y palabras de horizonte) vive en
  `ml_forecast/lexicon.py`. No son reglas de negocio; están separadas de
  `policy.py` para que el idioma se revise en un solo lugar.
- **Calidad:** `evaluate_quality` recibe el diagnóstico. Un último dato a más
  de `outlier_pct` de la media, un cambio de nivel o valores atípicos degradan
  el estado a “Revisar con cautela” con la razón explícita; un `no_confiable`
  previo no se tapa.
- **UI:** la explicación de “exploratoria” incluye alertas de datos, los
  umbrales se rotulan como “de error” y la nota de la narrativa ya no afirma
  “el asistente no respondió”: distingue LLM, plantilla y ausencia de lectura.

Evidencia real:

```text
pytest:   1108 passed, 264 subtests (6:13)
vitest:   23 archivos / 187 tests
tsc:      exit 0
build:    OK
e2e:      11 passed (1,4 min)
```

### Pendiente para fase 3

- Candidato estacional (naive estacional / descomposición) cuando la serie
  tenga dos ciclos completos y el backtest lo elija.
- Evaluación de preguntas contra el LLM local (script manual, no CI) usando
  `llm-evaluation`; matriz determinista ya cubierta en tests.
- Revisar las heurísticas compartidas con el chat que quedan como respaldo
  (`forecast_service`/`forecast_calculator`: nombres candidatos de fecha, valor
  y entidad) y los formatos de fecha por conexión. El vocabulario propio de
  proyecciones ya está en `ml_forecast/lexicon.py`; estas listas son del motor
  de forecast del chat y conviene decidir si se configuran por conexión.
- Decidir si el filtro `> 0` de las series SUM (`income_only`) debe exponerse
  como opción de catálogo/request para proyectar gastos o métricas netas sin
  una fórmula semántica. Hoy es una decisión de producto declarada en la
  respuesta, no un umbral accidental.
- No agregar pandas: el análisis de la fase 1 confirma que no hay operación
  tabular que justifique la dependencia; si aparece, se evalúa aislada.

## Verificación final del responsable

Re-ejecutar toda la suite y pegar la salida. Ningún subagente commitea.
