import { describe, it, expect } from 'vitest';
import {
  buildBuilderPreview,
  buildBuilderPrompt,
  humanizeLabel,
  stripSuggestionEmoji,
} from '../features/chat/utils/query_builder';
import type { BuilderTable } from '../features/chat/services/query_service';

const table: BuilderTable = {
  name: 'ventas',
  label: 'Ventas',
  metrics: [
    { column: 'monto_total', label: 'Monto Total de Venta', aggregate: 'sum' },
    { column: null, label: 'Cantidad de registros', aggregate: 'count' },
  ],
  dimensions: [{ name: 'tipo_filamento', label: 'Tipo de filamento' }],
  dates: [{ name: 'fecha_compra', label: 'Fecha de compra' }],
};

describe('Smart Builder: prompt con identificadores reales', () => {
  it('nombra tabla, columna, agrupacion y periodo', () => {
    const prompt = buildBuilderPrompt({
      table,
      metric: table.metrics[0],
      dimension: table.dimensions[0],
      dateColumn: table.dates[0],
      period: 'year',
    });

    expect(prompt).toContain('tabla ventas');
    expect(prompt).toContain('monto_total');
    expect(prompt).toContain('tipo_filamento');
    expect(prompt).toContain(`año ${new Date().getFullYear()}`);
    expect(prompt).toContain('gráfico');
  });

  it('para el conteo no pide sumar una columna', () => {
    const prompt = buildBuilderPrompt({
      table,
      metric: table.metrics[1],
      dimension: null,
      dateColumn: null,
      period: 'all',
    });

    expect(prompt).toContain('cuenta los registros');
    expect(prompt).not.toContain('suma de null');
  });

  it('omite el filtro temporal si la tabla no tiene fecha', () => {
    const prompt = buildBuilderPrompt({
      table: { ...table, dates: [] },
      metric: table.metrics[0],
      dimension: null,
      dateColumn: null,
      period: 'year',
    });

    expect(prompt).toContain('todo el histórico');
    expect(prompt).not.toContain('año');
  });
});

describe('Smart Builder: preview y etiquetas', () => {
  it('humaniza nombres tecnicos sin romper etiquetas ya legibles', () => {
    expect(humanizeLabel('tipo_filamento')).toBe('Tipo filamento');
    expect(humanizeLabel('Monto Total de Venta')).toBe('Monto Total de Venta');
  });

  it('quita el emoji inicial sin comerse el signo de apertura', () => {
    expect(stripSuggestionEmoji('📊 ¿Cuál es el monto total?')).toBe('¿Cuál es el monto total?');
    expect(stripSuggestionEmoji('¿Cuántos registros hay?')).toBe('¿Cuántos registros hay?');
  });

  it('la previa refleja metrica, agrupacion y periodo', () => {
    const preview = buildBuilderPreview({
      metric: table.metrics[0],
      dimension: table.dimensions[0],
      period: 'last90',
    });

    expect(preview).toBe('Monto Total de Venta por Tipo de filamento · Últimos 90 días');
  });
});
