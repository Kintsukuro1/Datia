import { describe, expect, it } from 'vitest';
import {
  arrowFor,
  buildPowerBiUrl,
  buildReportParams,
  buildReportSearch,
  crossCategoryTotals,
  dayLabel,
  describeMeasure,
  flowLabel,
  formatCompact,
  formatNumber,
  formatPeriod,
  matrixDayTotals,
  monthFromLabel,
  participationPct,
  readReportState,
  toggleFilterValue,
} from '../features/bi/report_logic';
import { BICross, BIMatrix } from '../features/bi/types';

describe('estado del informe en la URL', () => {
  it('lee los parámetros del enlace del chat y descarta una pestaña inválida', () => {
    const state = readReportState(
      '?conn=7&table=fact_ventas&month=all&gv=Ana&tab=inventada',
    );
    expect(state.conn).toBe('7');
    expect(state.table).toBe('fact_ventas');
    expect(state.month).toBe('all');
    expect(state.gv).toBe('Ana');
    expect(state.tab).toBe('resumen');
  });

  it('traduce el estado a los parámetros del backend', () => {
    const params = buildReportParams({
      conn: '7',
      table: 'fact_ventas',
      date: 'fecha',
      group: 'ejecutivo',
      cat: 'categoria',
      measure: 'monto',
      year: '2026',
      month: '2',
      gv: 'Ana',
      cv: 'Software',
      tab: 'diario',
    });
    expect(params).toEqual({
      connection_id: 7,
      table: 'fact_ventas',
      date_column: 'fecha',
      group_column: 'ejecutivo',
      category_column: 'categoria',
      measure: 'monto',
      year: 2026,
      month: '2',
      group_value: 'Ana',
      category_value: 'Software',
    });
  });

  it('no ensucia la URL con los valores por defecto', () => {
    const search = buildReportSearch({
      conn: '7',
      table: 'fact_ventas',
      date: '',
      group: '',
      cat: '',
      measure: 'count',
      year: '',
      month: '',
      gv: '',
      cv: '',
      tab: 'resumen',
    });
    expect(search).toBe('?conn=7&table=fact_ventas');
  });

  it('construye el enlace del botón del chat sin token', () => {
    expect(buildPowerBiUrl(7, 'fact_ventas')).toBe('/powerbi?conn=7&table=fact_ventas');
    expect(buildPowerBiUrl(undefined, undefined)).toBe('/powerbi');
  });
});

describe('formato y lectura del informe', () => {
  it('formatea el período con el mes cuando existe', () => {
    expect(formatPeriod({ years: [2026], months: [2], year: 2026, month: 2 })).toBe(
      'febrero 2026',
    );
    expect(formatPeriod({ years: [2026], months: [], year: 2026, month: null })).toBe(
      'Año 2026',
    );
    expect(formatPeriod({ years: [], months: [], year: null, month: null })).toBe(
      'Todo el período',
    );
  });

  it('formatea números y los deja vacíos como raya', () => {
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(1234.5, 1)).toContain('234');
    expect(formatNumber('texto')).toBe('texto');
  });

  it('etiqueta los días y los meses sin depender de la zona horaria', () => {
    expect(dayLabel('2026-02-10')).toMatch(/^10 /);
    expect(flowLabel('2026-02', false)).toBe('febrero');
    expect(flowLabel('2026-02-10', true)).toMatch(/^10 /);
  });

  it('describe la medida del contrato', () => {
    expect(describeMeasure('count')).toBe('Registros');
    expect(describeMeasure('monto')).toBe('Suma de monto');
  });

  it('compacta los ejes sin perder el signo', () => {
    expect(formatCompact(1_500_000)).toMatch(/1,5 M/);
    expect(formatCompact(4_500)).toMatch(/4,5 mil/);
    expect(formatCompact(950)).toBe('950');
    expect(formatCompact(-2_500)).toMatch(/-2,5 mil/);
    expect(formatCompact(null)).toBe('—');
  });

  it('convierte etiquetas de mes en número para el drill', () => {
    expect(monthFromLabel('febrero')).toBe(2);
    expect(monthFromLabel('2026-02')).toBe(2);
    expect(monthFromLabel('2026-02-10')).toBeNull();
    expect(monthFromLabel('lunes')).toBeNull();
  });

  it('el cross-filtro reemplaza el valor o lo limpia con el mismo clic', () => {
    expect(toggleFilterValue('', 'Ana')).toBe('Ana');
    expect(toggleFilterValue('Ana', 'Ana')).toBe('');
    expect(toggleFilterValue('Ana', 'Luis')).toBe('Luis');
  });
});

describe('cálculos de participación y totales', () => {
  it('calcula el porcentaje y las flechas con los límites del panel', () => {
    expect(participationPct(25, 100)).toBe(25);
    expect(participationPct(1, 0)).toBe(0);
    expect(arrowFor(30, 20)).toBe('↑');
    expect(arrowFor(20, 20)).toBe('→');
    expect(arrowFor(10, 20)).toBe('↓');
  });

  it('suma los totales por día sin inventar celdas faltantes', () => {
    const matrix: BIMatrix = {
      days: ['2026-01-01', '2026-01-02'],
      rows: [
        { label: 'Ana', cells: { '2026-01-02': 2 }, total: 2 },
        { label: 'Luis', cells: { '2026-01-01': 4 }, total: 4 },
      ],
    };
    expect(matrixDayTotals(matrix)).toEqual({
      '2026-01-01': 4,
      '2026-01-02': 2,
    });
  });

  it('suma los totales por categoría del cruce', () => {
    const cross: BICross = {
      categories: ['A', 'B'],
      rows: [
        { label: 'Ana', cells: { A: 2, B: 1 }, total: 3 },
        { label: 'Luis', cells: { A: 4 }, total: 4 },
      ],
    };
    expect(crossCategoryTotals(cross)).toEqual({ A: 6, B: 1 });
  });
});
