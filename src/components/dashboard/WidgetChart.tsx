import React from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import echarts from '../../shared/echarts';

interface WidgetChartProps {
  /**
   * Opción ECharts ya armada y memoizada por quien renderiza (ver
   * `parsedWidgets` en ChatDashboardPage). No se construye acá a propósito:
   * un objeto nuevo por render hace que ReactECharts llame setOption y
   * repinte el widget en cada tick.
   */
  option: any;
}

/**
 * Gráfico de un widget del Tablero Ejecutivo.
 *
 * Vive en su propio módulo para que ECharts quede detrás de un import
 * dinámico: entrar a /chat sin abrir el tablero no descarga el chunk de
 * ECharts.
 */
const WidgetChart: React.FC<WidgetChartProps> = ({ option }) => (
  <ReactEChartsCore
    echarts={echarts}
    option={option}
    style={{ height: '100%', width: '100%' }}
    opts={{ renderer: 'canvas' }}
  />
);

export default WidgetChart;
