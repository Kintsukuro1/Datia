import { describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import { SVGRenderer } from 'echarts/renderers';
import echarts from '../shared/echarts';

// SSR no puede instanciar canvas: el registro de CanvasRenderer (el renderer
// real de producción) no se ejercita acá. Solo lo cubre la app en navegador o un E2E.
echarts.use([SVGRenderer]);

const renderSvg = (option: EChartsOption): string => {
  const chart = echarts.init(null, null, {
    renderer: 'svg',
    ssr: true,
    width: 480,
    height: 320,
  });
  chart.setOption(option);
  const svg = chart.renderToSVGString();
  chart.dispose();
  return svg;
};

const cases: Record<string, EChartsOption> = {
  bar: {
    tooltip: { trigger: 'axis' },
    grid: {},
    xAxis: { type: 'category', data: ['A', 'B'] },
    yAxis: { type: 'value' },
    dataZoom: [{ type: 'slider' }],
    series: [{
      type: 'bar',
      data: [1, 2],
      markLine: { data: [{ type: 'average', label: { show: true, formatter: 'Promedio' } }] },
      markPoint: { data: [{ type: 'max', name: 'Máximo', label: { show: true, formatter: 'Máximo' } }] },
    }],
  },
  line: {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['A', 'B'] },
    yAxis: { type: 'value' },
    series: [{ type: 'line', data: [1, 2], areaStyle: {} }],
  },
  pie: {
    tooltip: { trigger: 'item' },
    legend: { type: 'scroll', bottom: 0 },
    series: [{
      type: 'pie',
      data: [{ name: 'A', value: 1 }, { name: 'B', value: 2 }],
    }],
  },
  scatter: {
    xAxis: { type: 'value' },
    yAxis: { type: 'value' },
    series: [{
      type: 'scatter',
      data: [[1, 2], [2, 3]],
      markLine: { data: [{ type: 'average' }] },
    }],
  },
  gauge: {
    series: [{ type: 'gauge', data: [{ value: 50, name: 'Promedio' }] }],
  },
  treemap: {
    series: [{
      type: 'treemap',
      data: [{ name: 'A', value: 1 }, { name: 'B', value: 2 }],
    }],
  },
  radial: {
    polar: {},
    angleAxis: { type: 'category', data: ['A', 'B'] },
    radiusAxis: {},
    series: [{
      type: 'bar',
      coordinateSystem: 'polar',
      data: [1, 2],
    }],
  },
};

// Mínimo de paths por caso: ejes y grid también dibujan paths, así que un
// simple `toContain('path')` pasaba aunque faltara la serie. Estos umbrales
// están por debajo del conteo real medido con 5.6.0 y por encima del conteo
// que queda si se elimina el chart correspondiente.
const minPaths: Record<string, number> = {
  bar: 20,
  line: 10,
  pie: 5,
  scatter: 20,
  gauge: 10,
  treemap: 1,
  radial: 5,
};

describe('ECharts modular', () => {
  it.each(Object.entries(cases))('renderiza %s', (name, option) => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const svg = renderSvg(option);
      expect(svg.startsWith('<svg')).toBe(true);
      expect((svg.match(/<path/g) ?? []).length).toBeGreaterThanOrEqual(minPaths[name]);
      expect(errorSpy.mock.calls.flat().join(' ')).not.toContain('is used but not imported');
    } finally {
      errorSpy.mockRestore();
    }
  });

  it('dibuja las etiquetas de markLine y markPoint', () => {
    const svg = renderSvg(cases.bar);
    expect(svg).toContain('Promedio');
    expect(svg).toContain('Máximo');
  });
});
