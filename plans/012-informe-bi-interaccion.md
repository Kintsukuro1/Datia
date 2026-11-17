# Plan 012 — Informe BI: interacción tipo Power BI (Ola 1 + drill + exportación)

**Fecha:** 17/11/2026
**Estado:** ✅ Implementado y verificado (17/11/2026)
**Objetivo:** que el informe y los paneles "se sientan" Power BI sin dejar de ser
Dat.ia: que el clic haga algo (cross-filtering y drill), que los KPIs muestren
tendencia, que el usuario controle el layout del panel y que pueda llevarse los
datos que está viendo. Un solo programa, sin dependencias nuevas.

**Origen:** análisis del 17/11/2026 pedido por el responsable (características,
satisfacción y respuesta). Este plan ejecuta la Ola 1 completa más el drill de
fecha y la exportación del visual. Los filtros relativos, tipos extra
(gauge/scatter), compartir panel y auto-sugerencia quedan para una ola 2.

## Alcance

1. **Cross-filtering**: clic en una barra/torta del informe filtra la página
   (grupo o categoría); clic en el mismo valor lo quita. Camino accesible y
   testeable: el rótulo de cada fila de la matriz y del cruce también filtra.
2. **Drill de fecha**: en la evolución mensual, clic en un punto del gráfico
   fija ese mes (el "cable" ya existe: el estado vive en la URL).
3. **KPI con tendencia**: `total_anterior` y `delta_pct` contra el período
   anterior (mes anterior, o año anterior si se ve todo el año). Sin datos
   anteriores → `None`, nunca un número inventado.
4. **Tamaño del visual**: `size` en `BIVisualSpec` (`small` | `wide` | `full`),
   por defecto `small` para no romper visuales existentes.
5. **Exportación CSV del visual**: botón "Descargar CSV" en cada tarjeta de
   panel. Exporta EXACTAMENTE lo que ya está en pantalla (mismo endpoint
   auditado `/visuals/{id}/data`); se arma en el cliente y escapa los
   prefijos de fórmula (`=`, `+`, `-`, `@`) igual que el backend hace en los
   exports de Excel.
6. **Ejes legibles**: formato compacto (`1,2 M`, `4,5 mil`) en los ejes de los
   gráficos; el tooltip sigue mostrando el número completo.

## Fuera de alcance

- Filtros relativos ("últimos 30 días"), top-N: cambian el contrato de
  período y van con la ola 2.
- Gauge/scatter/apiladas: tipos nuevos, ola 2.
- Compartir panel por link, auto-sugerencia, modo presentación: ola 3.
- Modelado libre tipo DAX y publicación a la nube: no se copian a propósito.

## Criterios de aceptación

- Clic en "Ana" (barra o fila del cruce) filtra todo el informe por Ana; clic
  de nuevo limpia el filtro. Queda en la URL (`gv`) y por lo tanto es
  compartible.
- Clic en un mes de la evolución mensual fija ese mes; el informe muestra el
  detalle diario de ese mes.
- El KPI de la medida muestra delta contra el período anterior y el valor
  anterior; si no hay historial, no muestra delta (no inventa).
- Un visual puede ser ⅓, ⅔ o ancho completo; los guardados antes del cambio
  siguen siendo ⅓.
- "Descargar CSV" baja lo que se ve (label/value), con escape de fórmulas.
- `pytest`, `npm test`, `tsc --noEmit`, `npm run build` y `npm run test:e2e`
  verdes, con la salida real pegada al cierre.

## Resultado

Implementado y verificado el 17/11/2026.

**Backend**
- `schemas.py`: `BIVisualSpec.size` (`small`|`wide`|`full`, default `small`
  para no romper visuales existentes) y `BIKpis.total_anterior`/`delta_pct`.
- `report_service.py`: KPI del período anterior (mes previo, o año previo si se
  mira todo el año), respetando los filtros de página. Sin historial → `None`.

**Frontend**
- `WidgetChart` acepta `onEvents` (opcional, compatible hacia atrás) y `BIChart`
  expone `onPointClick`: clic en barra/torta/punto del gráfico.
- `ReportSections`: cross-filtering (barras, torta y rótulos de fila del cruce
  y de la matriz), drill de mes por clic en la evolución mensual, chip de delta
  en el KPI (▲/▼ con el valor anterior) y ejes con formato compacto.
- `PanelsPage`: control "Tamaño en el panel" (⅓/⅔/completo) con span de grilla,
  altura mayor para el completo, y botón "Descargar CSV" que baja exactamente
  lo que se ve (mismo endpoint auditado), escapando fórmulas como el backend.
- `report_logic.ts` / `panels_logic.ts`: `formatCompact`, `monthFromLabel`,
  `toggleFilterValue`, `csvCell`, `visualToCsv`, `csvFilename` (puros y
  testeados).

**Evidencia real**
- `pytest -q`: **1136 passed, 264 subtests (8:03)**.
- Tests BI enfocados: **28 passed**.
- `npx tsc --noEmit`: **exit 0**.
- `npm test`: **26 archivos / 211 tests**.
- `npm run build`: **exit 0**; chunk de ECharts **704,15 kB** (el esperado).
- `npm run test:e2e`: **12 passed + 1 skipped (2,2 min)**, incluido el
  cross-filtering activar/limpiar y la descarga CSV del visual.

Declarado: el drill por clic en el gráfico es interacción de mouse sobre canvas
de ECharts; su lógica (`monthFromLabel`) está cubierta por test unitario y el
camino accesible por teclado sigue siendo el selector de mes del encabezado.

No se commiteó; el cambio queda en el árbol para revisión del responsable.
