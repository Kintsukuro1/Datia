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
  projectionService: { estimate: vi.fn(), run: vi.fn() },
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

vi.mock('echarts-for-react', async () => {
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
  income_only: true,
  table: 'movimientos',
  metric_column: 'monto',
  date_column: 'fecha',
  analysis_source: 'heuristica',
  narration: 'La serie sube de forma sostenida; el paso 2 no dejó residuos que medir.',
  narration_source: 'plantilla',
  audit_log_id: 55,
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

    expect(projectionService.estimate).toHaveBeenCalledWith(QUESTION, 6);
    expect(projectionService.run).toHaveBeenCalledWith(QUESTION, 9, 6);

    const text = container.textContent ?? '';
    // Metodo y honestidad del backtest.
    expect(text).toContain('Naive (último valor)');
    expect(text).toContain('No le ganó al naive');
    // Banda y pasos (formato es-CL: la misma convención que money()).
    expect(text).toContain('±15,4%');
    expect(text).toContain('2026-10');
    // Paso sin backtest propio: marcado, y su punto/MAPE en "—", no en 0.
    expect(text).toContain('sin backtest propio');
    expect(text).toContain('—');
    expect(text).not.toContain('$0');
    // Narrativa con su origen.
    expect(text).toContain('La serie sube de forma sostenida');
    expect(text).toContain('Plantilla determinista');
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
