import React from 'react';
import WidgetChart from '../../../components/dashboard/WidgetChart';

interface BIChartProps {
  option: Record<string, unknown>;
  height?: number;
  ariaLabel?: string;
  /**
   * Clic en un punto/barra/segmento del gráfico. Recibe la etiqueta visible
   * (`params.name`), que el informe usa para cross-filtrar o hacer drill.
   * El camino accesible por teclado sigue siendo el selector del encabezado;
   * ECharts solo expone el clic con mouse.
   */
  onPointClick?: (label: string) => void;
}

/** ECharts modular del producto: no importa `echarts-for-react` completo. */
export const BIChart: React.FC<BIChartProps> = ({
  option,
  height = 280,
  ariaLabel,
  onPointClick,
}) => (
  <div
    style={{ height }}
    className="w-full"
    role="img"
    aria-label={ariaLabel || 'Gráfico del informe'}
  >
    <WidgetChart
      option={option}
      onEvents={
        onPointClick
          ? {
              click: (params: Record<string, unknown>) =>
                onPointClick(String(params.name ?? '')),
            }
          : undefined
      }
    />
  </div>
);
