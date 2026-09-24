import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export default function Chart({ option, height = 240 }: { option: any; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  // инстанс живёт всё время жизни блока; при смене опций — только setOption.
  // ResizeObserver держит канвас в синхроне с контейнером при любом рефлоу
  // (сворачивание сайдбара и т.п. — без события window.resize).
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
