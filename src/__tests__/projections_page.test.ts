// @vitest-environment jsdom
//
// Flujo completo de /projections con deps sustituidas:
// - la pregunta redirigida por el chat llega precargada y NO se corre sola;
// - un Enter lanza estimate y despues run, con la conexion activa y el horizonte;
// - los resultados muestran la evidencia (metodo, "no le gano al naive", banda,
//   pasos sin backtest) y "—" donde el backend publica null, nunca ceros;
// - available=false se muestra con su motivo, no como error ni con numeros;
// - el export llama a los endpoints con el `audit_log_id` de la corrida, y sin
//   registro de auditoria no hay boton (no uno que siempre falle).
//
// Sin JSX a proposito (vite.config.ts solo globbea `src/**/*.test.ts`).
// ECharts se sustituye: necesita un canvas real que jsdom no tiene, y la forma
// del grafico no es lo que este test cuida.
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { MemoryRouter } from 'react-router-dom';
import type { ProjectionResult } from '../features/projections/services/projection_service';

const { projectionService, reportService } = vi.hoisted(() => ({
  projectionService: {
    estimate: vi.fn(),
    run: vi.fn(),
    context: vi.fn(),
    history: vi.fn(),
    getSaved: vi.fn(),
    verify: vi.fn(),
  },
  reportService: {
    captureChartAsBase64: vi.fn(),
    exportExecutiveReportPdf: vi.fn(),
    exportExecutiveReportExcel: vi.fn(),
  },
}));

vi.mock('../features/projections/services/projection_service', () => ({ projectionService }));

vi.mock('../features/dashboard/services/report_service', () => ({ reportService }));

vi.mock('../features/chat/hooks/useChatEngine', () => ({
  useChatEngine: () => ({
    connectors: [
      { id: 9, name: 'MOVIMIENTOS MAXI3D SQL', db_type: 'postgresql', is_active: true },
    ],
    activeConnectionId: 9,
    activeDatabaseName: 'MOVIMIENTOS MAXI3D SQL (POSTGRESQL)',
    handleSelectConnection: () => {},
  }),
}));

vi.mock('echarts-for-react/lib/core', async () => {
  const react = await import('react');
  return {
    default: () =>
      react.createElement('div', { 'data-testid': 'projection-chart' }),
  };
});

import { ProjectionsPage } from '../pages/ProjectionsPage';
import { NotificationProvider } from '../context/NotificationContext';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const QUESTION = '¿Cuánto voy a vender en los próximos 6 meses?';

const availableFixture: ProjectionResult = {
  available: true,
  question: QUESTION,
  horizon: 6,
  horizon_source: 'pedido',
  series: [
    { periodo: '2026-08', valor: 100 },
    { periodo: '2026-09', valor: 120 },
  ],
  steps: [
    {
      step: 1,
      period: '2026-10',
      point: 130,
      lower: 110,
      upper: 150,
      band_pct: 15.4,
      mape: 8.1,
      n_backtests: 3,
      reliable: false,
      extrapolated: false,
    },
    {
      step: 2,
      period: '2026-11',
      point: null,
      lower: null,
      upper: null,
      band_pct: null,
      mape: null,
      n_backtests: 0,
      reliable: false,
      extrapolated: true,
    },
  ],
  method: 'naive',
  beats_naive: false,
  mape: 8.1,
  mape_by_method: { naive: 8.1, mean: 12.3, ols_trend: null, holt_damped: null },
  n_periods: 9,
  has_gaps: false,
  partial_period_excluded: '2026-10',
  future_periods_excluded: ['2026-11'],
  income_only: true,
  table: 'movimientos',
  metric_column: 'monto',
  date_column: 'fecha',
  analysis_source: 'heuristica',
  narration: 'La serie sube de forma sostenida; el paso 2 no dejó residuos que medir.',
  narration_source: 'plantilla',
  audit_log_id: 55,
};

const totalFixture: ProjectionResult = {
  ...availableFixture,
  horizon: 3,
  horizon_source: 'llm',
  partial_period_excluded: null,
  future_periods_excluded: [],
  steps: [
    {
      step: 1, period: '2026-10', point: 110, lower: 100, upper: 120,
      band_pct: 9.1, mape: 5.0, n_backtests: 4, reliable: true, extrapolated: false,
    },
    {
      step: 2, period: '2026-11', point: 120, lower: 105, upper: 135,
      band_pct: 12.5, mape: 6.0, n_backtests: 3, reliable: false, extrapolated: false,
    },
    {
      step: 3, period: '2026-12', point: 130, lower: 110, upper: 150,
      band_pct: 15.4, mape: null, n_backtests: 0, reliable: false, extrapolated: true,
    },
  ],
  total: {
    point: 360,
    lower: 330,
    upper: 390,
    band_pct: 10.2,
    mape: null,
    n_backtests: 1,
    reliable: false,
    extrapolated: true,
    measured_steps: 1,
  },
};

describe('ProjectionsPage', () => {
  let container: HTMLDivElement;
  let root: Root;

  const renderPage = (state?: { question: string }) => {
    root.render(
      React.createElement(
        MemoryRouter,
        { initialEntries: [{ pathname: '/projections', state }] },
        React.createElement(
          NotificationProvider,
          null,
          React.createElement(ProjectionsPage)
        )
      )
    );
  };

  const getTextarea = () =>
    container.querySelector<HTMLTextAreaElement>('#projections-question')!;

  const findButton = (label: string) =>
    Array.from(container.querySelectorAll('button')).find(
      (b) => b.getAttribute('aria-label') === label
    ) ?? null;

  const openTechnicalDetail = async () => {
    const toggle = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Ver detalle técnico')
    );
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  const pressEnter = async () => {
    await act(async () => {
      getTextarea().dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
      );
      // Dos vueltas de macrotarea: la cadena estimate -> run completa y React
      // aplica los estados resultantes.
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  beforeEach(() => {
    projectionService.estimate.mockReset();
    projectionService.run.mockReset();
    projectionService.context.mockReset();
    projectionService.history.mockReset();
    projectionService.getSaved.mockReset();
    projectionService.verify.mockReset();
    reportService.captureChartAsBase64.mockReset();
    reportService.exportExecutiveReportPdf.mockReset();
    reportService.exportExecutiveReportExcel.mockReset();
    reportService.captureChartAsBase64.mockResolvedValue(undefined);
    reportService.exportExecutiveReportPdf.mockResolvedValue(undefined);
    reportService.exportExecutiveReportExcel.mockResolvedValue(undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('precarga la pregunta redirigida por el chat y no la corre sola', () => {
    act(() => {
      renderPage({ question: QUESTION });
    });

    expect(getTextarea().value).toBe(QUESTION);
    expect(projectionService.estimate).not.toHaveBeenCalled();
    expect(projectionService.run).not.toHaveBeenCalled();
  });

  it('un Enter lanza estimate y run con la conexión activa, y publica la evidencia sin ceros', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue(availableFixture);

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    expect(projectionService.estimate).toHaveBeenCalledWith(QUESTION, null);
    expect(projectionService.run).toHaveBeenCalledWith(
      QUESTION, 9, null, undefined, expect.any(AbortSignal)
    );

    const text = container.textContent ?? '';
    // El detalle técnico nace cerrado: no está en el texto hasta abrirlo.
    expect(text).toContain('Ver detalle técnico');
    expect(text).not.toContain('Error y evidencia por paso');
    expect(text).not.toContain('Método y evidencia');
    await openTechnicalDetail();

    const detailed = container.textContent ?? '';
    // Metodo y honestidad del backtest.
    expect(detailed).toContain('Repetir el último valor');
    expect(detailed).toContain('No mejora a repetir el último valor');
    // Banda y pasos (formato es-CL: la misma convención que money()).
    expect(detailed).toContain('±15,4%');
    expect(detailed).toContain('2026-10');
    // Paso sin backtest propio: marcado, y su punto/MAPE en "—", no en 0.
    expect(detailed).toContain('sin backtest propio');
    expect(detailed).toContain('—');
    expect(detailed).not.toContain('$0');
    // Narrativa con su origen.
    expect(detailed).toContain('La serie sube de forma sostenida');
    expect(detailed).toContain('plantilla del servidor');
    // Horizonte resuelto y su origen, no solo el numero.
    expect(detailed).toContain('elegido en el pedido');
    // Periodos descartados por el backend, con su motivo.
    expect(detailed).toContain('Se excluyó');
    expect(detailed).toContain('2026-10');
    expect(detailed).toContain('2026-11');
    // La banda ya no se presenta como un "80%" fijo.
    expect(detailed).toContain('banda de error');
    expect(detailed).not.toContain('80%');
  });

  it('muestra tabla, métrica y eje con nombres legibles y hasta dónde llegan los datos', async () => {
    projectionService.run.mockResolvedValue(availableFixture);

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    // El encabezado de la página ya no habla de "Machine Learning".
    expect(container.querySelector('h1')?.textContent).toBe('Proyecciones');
    // Nombre legible a la vista, identificador crudo en el `title` (auditoría).
    expect(container.querySelector('[title="movimientos"]')?.textContent).toBe('Movimientos');
    expect(container.querySelector('[title="monto"]')?.textContent).toBe('Monto');
    expect(container.querySelector('[title="fecha"]')?.textContent).toBe('Fecha');
    const text = container.textContent ?? '';
    expect(text).toContain('Datos hasta: 2026-09');
  });

  it('no inventa "Datos hasta" cuando la serie viene vacía', async () => {
    projectionService.run.mockResolvedValue({ ...availableFixture, series: [] });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    expect(container.textContent ?? '').not.toContain('Datos hasta');
  });

  it('deja el detalle técnico cerrado y no lo renderiza hasta pedirlo', async () => {
    projectionService.run.mockResolvedValue(availableFixture);

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const closed = container.textContent ?? '';
    expect(closed).toContain('Ver detalle técnico');
    expect(closed).not.toContain('Error y evidencia por paso');
    expect(closed).not.toContain('Método y evidencia');
    // Cerrado no queda en el DOM (no alcanza con CSS hidden).
    expect(closed).not.toContain('Punto, banda, error y evidencia de cada paso proyectado');

    await openTechnicalDetail();

    const opened = container.textContent ?? '';
    expect(opened).toContain('Ocultar detalle técnico');
    expect(opened).toContain('Error y evidencia por paso');
    expect(opened).toContain('Método y evidencia');
    expect(opened).toContain('Punto, banda, error y evidencia de cada paso proyectado');
  });

  it('publica la lectura primero y deja el resto a un clic, no como muro de texto', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue({
      ...availableFixture,
      narration:
        'La conclusión va primero: el total proyectado ronda los 1.824 millones.\n\nSEGUNDO_PARRAFO: salvedades sobre la serie y el horizonte.',
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('La conclusión va primero');
    // La lectura se lee antes que el detalle técnico y el gráfico.
    expect(text.indexOf('Qué dicen los números')).toBeLessThan(text.indexOf('Serie y proyección'));
    // El segundo párrafo no se publica hasta pedirlo.
    expect(text).not.toContain('SEGUNDO_PARRAFO:');

    const expand = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Ver el resto del resumen')
    );
    expect(expand).toBeTruthy();
    await act(async () => {
      expand!.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    const expanded = container.textContent ?? '';
    expect(expanded).toContain('SEGUNDO_PARRAFO:');
    expect(expanded).toContain('Mostrar menos');
  });

  it('ya no ofrece horizonte, granularidad ni ajustes: la pregunta manda', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue({
      ...availableFixture, horizon: 12, horizon_source: 'pregunta',
    });

    act(() => {
      renderPage({ question: 'proyecta un año' });
    });

    // Los selectores manuales ya no existen y la pantalla no pide contexto.
    expect(container.querySelector('#projections-horizon-select')).toBeNull();
    expect(container.querySelector('#projections-granularity-select')).toBeNull();
    expect(container.querySelector('#projections-advanced-toggle')).toBeNull();
    expect(projectionService.context).not.toHaveBeenCalled();

    await pressEnter();

    expect(projectionService.estimate).toHaveBeenCalledWith('proyecta un año', null);
    expect(projectionService.run).toHaveBeenCalledWith(
      'proyecta un año', 9, null, undefined, expect.any(AbortSignal)
    );
    await openTechnicalDetail();
    const text = container.textContent ?? '';
    expect(text).toContain('Horizonte publicado: 12 meses');
    expect(text).toContain('según tu pregunta');
  });

  it('muestra un escenario de crecimiento sin semáforo de calidad ni backtest', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue({
      ...availableFixture,
      horizon: 120,
      granularity: 'month',
      method: 'scenario',
      beats_naive: null,
      mape: null,
      mape_by_method: {},
      quality: null,
      total: {
        point: 2_100,
        lower: null,
        upper: null,
        band_pct: null,
        mape: null,
        n_backtests: 0,
        reliable: false,
        extrapolated: false,
        measured_steps: null,
        lower_clamped: false,
      },
      steps: [
        { step: 1, period: '2026-10', point: 1_000, lower: null, upper: null, band_pct: null, mape: null, n_backtests: 0, reliable: false, extrapolated: false },
        { step: 2, period: '2026-11', point: 1_100, lower: null, upper: null, band_pct: null, mape: null, n_backtests: 0, reliable: false, extrapolated: false },
      ],
      series: [{ periodo: '2026-09', valor: 1_000 }],
      scenario: {
        growth_pct: 10,
        annual: true,
        base_value: 1_000,
        base_period: '2026-09',
        final_value: 1_100,
        gain: 100,
        label: 'Escenario de crecimiento',
      },
      narration: 'Escenario: partiendo de 1.000 en 2026-09, con un crecimiento anual del 10%...',
      narration_source: 'plantilla',
    });

    act(() => {
      renderPage({ question: 'Proyectame una ganancia con el 10% de las bases en 10 años' });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('Escenario de crecimiento');
    expect(text).toContain('10% anual');
    expect(text).toContain('Ganancia vs. base');
    expect(text).toContain('$1.000');
    expect(text).toContain('$1.100');
    expect(text).toContain('Escenario determinista');
    // Sin semáforo de calidad, sin detalle técnico y sin leyenda de banda.
    expect(text).not.toContain('Poco confiable');
    expect(text).not.toContain('Ver detalle técnico');
    expect(text).not.toContain('banda de error (backtest)');
  });

  it('publica el total del horizonte con su banda y su evidencia parcial', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue(totalFixture);

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('Total del horizonte');
    expect(text).toContain('$360');
    // La banda es de un prefijo: no se pinta un rango numérico como si
    // cubriera el horizonte completo.
    expect(text).not.toContain('$330 – $390');
    expect(text).toContain('Sin banda para el horizonte completo');
    expect(text).toContain('medición parcial');
    expect(text).toContain('Banda heredada del total de 1 paso');
    // El error del total no se inventa cuando la banda es de un prefijo.
    expect(text).toContain('Error promedio del total');
    expect(text).not.toContain('$0');
  });

  it('avisa cuando la banda de un conteo se acota a 0', async () => {
    projectionService.run.mockResolvedValue({
      ...totalFixture,
      steps: totalFixture.steps.map((step, index) =>
        index === 0 ? { ...step, lower: 0, lower_clamped: true } : step
      ),
      total: { ...totalFixture.total!, lower: 0, lower_clamped: true },
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    await openTechnicalDetail();
    const text = container.textContent ?? '';
    expect(text).toContain('Banda inferior acotada a 0');
    expect(text).toContain('no hay cantidades negativas');
  });

  it('muestra el estado de calidad y sus razones', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue({
      ...totalFixture,
      quality: {
        status: 'no_confiable',
        mape: 67.7,
        unreliable_threshold: 50,
        exploratory_threshold: 30,
        reasons: [
          'el error promedio (67,7%) supera el límite de 50%',
          'el margen del total cubre 1 de 3 pasos',
        ],
      },
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('Poco confiable');
    expect(text).toContain('el error promedio (67,7%) supera el límite de 50%');
    expect(text).toContain('el margen del total cubre 1 de 3 pasos');
    expect(text).toContain('Sin banda para el horizonte completo');

    // El motivo también está en la nota que abre el badge (hover/foco).
    const tooltipText = Array.from(container.querySelectorAll('[role="tooltip"]'))
      .map((node) => node.textContent ?? '')
      .join(' ');
    expect(tooltipText).toContain('el error promedio (67,7%) supera el límite de 50%');
  });

  it('explica en la nota por qué una proyección es confiable', async () => {
    projectionService.run.mockResolvedValue({
      ...totalFixture,
      steps: totalFixture.steps.map((step) => ({
        ...step,
        point: step.point ?? 130,
        n_backtests: 5,
        reliable: true,
        extrapolated: false,
      })),
      total: {
        ...totalFixture.total!,
        measured_steps: 3,
        extrapolated: false,
        reliable: true,
      },
      beats_naive: true,
      quality: {
        status: 'confiable',
        mape: 2.6,
        unreliable_threshold: 50,
        exploratory_threshold: 30,
        reasons: [],
      },
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const tooltipText = Array.from(container.querySelectorAll('[role="tooltip"]'))
      .map((node) => node.textContent ?? '')
      .join(' ');
    expect(tooltipText).toContain('Las reglas de calidad no encontraron alertas');
    expect(tooltipText).toContain('Error promedio 2,6%, por debajo del umbral de referencia 30%');
    expect(tooltipText).toContain('El paso con menos evidencia tiene 5 pruebas');
    expect(tooltipText).toContain('La serie no tiene períodos faltantes');
  });

  it('muestra la calidad aunque no se pueda sumar el total', async () => {
    projectionService.run.mockResolvedValue({
      ...availableFixture,
      total: null,
      quality: {
        status: 'no_confiable',
        mape: 67.7,
        unreliable_threshold: 50,
        exploratory_threshold: 30,
        reasons: ['no se pudo calcular el total del período'],
      },
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('Poco confiable');
    expect(text).toContain('no se pudo calcular el total del período');
  });

  it('el export manda el audit_log_id de la corrida y captura ESTE gráfico', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 25, source: 'medido' });
    projectionService.run.mockResolvedValue(availableFixture);

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const pdfButton = findButton('Descargar informe PDF de la proyección');
    expect(pdfButton).not.toBeNull();

    await act(async () => {
      pdfButton!.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(reportService.captureChartAsBase64).toHaveBeenCalledTimes(1);
    // El scope de la captura es el contenedor del chart, no el documento.
    expect(reportService.captureChartAsBase64.mock.calls[0][0]).toBeInstanceOf(HTMLElement);
    expect(reportService.exportExecutiveReportPdf).toHaveBeenCalledWith(
      { audit_log_id: 55 },
      { chartBase64: undefined }
    );

    await act(async () => {
      findButton('Descargar Excel de la proyección')!.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(reportService.exportExecutiveReportExcel).toHaveBeenCalledWith({ audit_log_id: 55 });
  });

  it('reabre una proyección guardada desde el historial y avisa que es histórica', async () => {
    projectionService.history.mockResolvedValue({
      items: [
        {
          audit_log_id: 77,
          timestamp: '2026-01-05T12:00:00',
          question: 'Proyección de enero',
          horizon: 6,
          table: 'movimientos',
          metric_column: 'monto',
          date_column: 'fecha',
          total_point: 1000,
          verifiable: true,
          readable: true,
        },
      ],
    });
    projectionService.getSaved.mockResolvedValue({
      ...availableFixture,
      audit_log_id: 77,
      question: 'Proyección de enero',
      narration: 'Lectura guardada de una corrida anterior.',
    });

    act(() => {
      renderPage();
    });

    const toggle = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Proyecciones recientes')
    );
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle!.click();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(projectionService.history).toHaveBeenCalledWith(10);
    const detail = Array.from(container.querySelectorAll('button')).find((b) =>
      b.getAttribute('aria-label')?.includes('Ver detalle de la proyección 77')
    );
    expect(detail).toBeTruthy();
    await act(async () => {
      (detail as HTMLButtonElement).click();
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(projectionService.getSaved).toHaveBeenCalledWith(77);
    const text = container.textContent ?? '';
    expect(text).toContain('Viendo una proyección guardada');
    expect(text).toContain('Proyección de enero');
    expect(text).toContain('Lectura guardada de una corrida anterior');

    const close = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'Cerrar'
    );
    expect(close).toBeTruthy();
    await act(async () => {
      close!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    const closed = container.textContent ?? '';
    expect(closed).not.toContain('Viendo una proyección guardada');
    expect(closed).not.toContain('Lectura guardada de una corrida anterior');
  });

  it('available=false muestra el motivo y la serie leída, sin pasos ni números inventados', async () => {
    projectionService.estimate.mockResolvedValue({ estimated_seconds: 10, source: 'medido' });
    projectionService.run.mockResolvedValue({
      available: false,
      reason: 'Se necesitan al menos 6 periodos para proyectar y hay 3.',
      question: QUESTION,
      horizon: 6,
      series: [{ periodo: '2026-01', valor: 5 }],
      steps: [],
      mape_by_method: {},
      n_periods: 3,
      has_gaps: false,
      income_only: true,
      table: 'movimientos',
      metric_column: 'monto',
      date_column: 'fecha',
    });

    act(() => {
      renderPage({ question: QUESTION });
    });
    await pressEnter();

    const text = container.textContent ?? '';
    expect(text).toContain('Proyección no disponible');
    expect(text).toContain('Se necesitan al menos 6 periodos para proyectar y hay 3.');
    expect(text).toContain('2026-01');
    expect(text).not.toContain('Error y evidencia por paso');
    // Este fixture no trae audit_log_id: sin registro de auditoría no hay botón
    // de export (no uno que siempre falle).
    expect(findButton('Descargar informe PDF de la proyección')).toBeNull();
  });
});
