import * as echarts from "echarts/core";
import { HeatmapChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

let registered = false;

export function createHeatmapChart(element) {
  if (!registered) {
    echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);
    registered = true;
  }
  return echarts.init(element, null, { renderer: "canvas" });
}
