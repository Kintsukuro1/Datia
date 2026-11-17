import { describe, expect, it } from 'vitest';
import { csvCell, csvFilename, describeVisualSpec, visualToCsv } from '../features/bi/panels_logic';
import { BIRow } from '../features/bi/types';

describe('exportación CSV del visual', () => {
  it('escapa los prefijos de fórmula igual que el backend', () => {
    expect(csvCell('=CMD()')).toBe("'=CMD()");
    expect(csvCell('+1')).toBe("'+1");
    expect(csvCell('-2')).toBe("'-2");
    expect(csvCell('@X')).toBe("'@X");
    expect(csvCell('Ana')).toBe('Ana');
  });

  it('cita los valores con comillas o separador', () => {
    expect(csvCell('a"b')).toBe('"a""b"');
    expect(csvCell('a;b')).toBe('"a;b"');
    expect(csvCell('a\nb')).toBe('"a\nb"');
  });

  it('arma el CSV de exactamente lo que se ve', () => {
    const rows: BIRow[] = [
      { label: 'Software', value: 10 },
      { label: 'Servicios', value: 5 },
    ];
    expect(visualToCsv(rows)).toBe('Etiqueta;Valor\r\nSoftware;10\r\nServicios;5');
  });

  it('sin etiqueta arma una sola columna (KPI)', () => {
    expect(visualToCsv([{ value: 4 }])).toBe('Valor\r\n4');
  });

  it('limpia el nombre del archivo sin perder legibilidad', () => {
    expect(csvFilename('Ventas por método de pago')).toBe('ventas_por_metodo_de_pago.csv');
    expect(csvFilename('   ')).toBe('visual.csv');
    expect(csvFilename('=CMD()')).toBe('cmd.csv');
  });
});

describe('descripción del visual', () => {
  it('nombra la medida y la dimensión', () => {
    expect(
      describeVisualSpec({
        connection_id: 1,
        table: 'fact_ventas',
        chart_type: 'bar',
        dimension: 'categoria',
        aggregation: 'sum',
        measure: 'monto',
        filters: [],
        sort: 'value_desc',
        limit: 20,
        size: 'small',
      }),
    ).toBe('Suma de monto por categoria');
    expect(
      describeVisualSpec({
        connection_id: 1,
        table: 'fact_ventas',
        chart_type: 'kpi',
        dimension: null,
        aggregation: 'count',
        measure: null,
        filters: [],
        sort: 'value_desc',
        limit: 20,
        size: 'small',
      }),
    ).toBe('Cantidad de filas');
  });
});
