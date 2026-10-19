import { describe, it, expect } from 'vitest';
import { buildExtraChartOption } from '../features/dashboard/components/charts/extraChartConfig';
import { THEME_COLORS } from '../features/dashboard/components/charts/theme';

// Portado de `scripts/check-extra-charts.mjs` (esbuild + assert manual) a vitest:
// asi corre en `npm test`, que es lo que CI gatea, en vez de un script que
// ningun job ejecutaba. El gauge es el unico con aritmetica no trivial (max cae
// a 1 para no dividir por cero y el punto se ancla en el promedio).
const rows = [
  { region: 'Norte', monto: 120 },
  { region: 'Sur', monto: 340 },
  { region: 'Este', monto: 90 },
  { region: 'Oeste', monto: 250 },
];

const base = {
  processedRows: rows,
  catCol: 'region',
  numCol: 'monto',
  currentTheme: THEME_COLORS.indigo,
  isCurrency: true,
  totalVal: rows.reduce((s, r) => s + r.monto, 0),
};

describe('extraChartConfig: builders radial/scatter/treemap/gauge', () => {
  it('conserva serie y una entrada de datos por fila', () => {
    // gauge colapsa N filas a 1 cifra (el promedio), asi que se excluye del conteo.
    for (const kind of ['radial', 'scatter', 'treemap'] as const) {
      const opt: any = buildExtraChartOption({ ...base, activeChartType: kind });
      expect(opt.series[0].type.length, `${kind}: sin serie`).toBeGreaterThan(0);
      expect(opt.series[0].data.length, `${kind}: pierde filas`).toBe(rows.length);
    }
  });

  it('radial rotula el eje con las 4 categorias', () => {
    const opt: any = buildExtraChartOption({ ...base, activeChartType: 'radial' });
    expect(opt.angleAxis.data.length).toBe(4);
  });

  it('treemap mapea el valor de la fila', () => {
    const opt: any = buildExtraChartOption({ ...base, activeChartType: 'treemap' });
    expect(opt.series[0].data[1].value).toBe(340);
  });

  it('gauge cae a max 1 con datos en cero (sin eje degenerado)', () => {
    const zero: any = buildExtraChartOption({
      ...base,
      processedRows: [{ region: 'A', monto: 0 }],
      totalVal: 0,
      activeChartType: 'gauge',
    });
    expect(zero.series[0].max).toBe(1);
  });

  it('gauge muestra el promedio contra el max: la aguja nunca satura', () => {
    const g: any = buildExtraChartOption({ ...base, activeChartType: 'gauge' });
    expect(g.series[0].max).toBe(340);
    expect(g.series[0].data.length).toBe(1);
    expect(g.series[0].data[0].value).toBe(200);
    expect(g.series[0].data[0].value).toBeLessThanOrEqual(g.series[0].max);
  });
});
