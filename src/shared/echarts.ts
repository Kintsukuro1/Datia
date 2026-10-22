import * as echarts from 'echarts/core';
import {
  BarChart,
  GaugeChart,
  LineChart,
  PieChart,
  ScatterChart,
  TreemapChart,
} from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  PolarComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

// Solo lo que la app dibuja. El import completo de `echarts` metia todo el
// paquete (incluidos mapa, sankey y graficos 3D) en un chunk de ~1 MB.
// Se mantiene en 5.6.0 a proposito: 6.x cambia el tema por defecto, la
// posicion de leyendas/ejes y la herencia de estilos rich; el objetivo de
// bundle ya se cumplio con este import modular. Reevaluar solo si hace falta
// una feature concreta de 6.x.
echarts.use([
  BarChart,
  GaugeChart,
  LineChart,
  PieChart,
  ScatterChart,
  TreemapChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  PolarComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default echarts;
