import type { EChartsOption } from 'echarts';
import type { AnalyticsChart } from '../types';

/** Map the server's declarative ChartConfig, never executable chart code, to ECharts. */
export function toChartOption(chart: AnalyticsChart | null | undefined): EChartsOption | undefined {
  if (!chart) return undefined;
  const base = { tooltip: {}, color: chart.style?.palette?.length ? chart.style.palette : undefined,
    legend: { show: chart.style?.show_legend ?? true }, grid: { left: 55, right: 30, top: 45, bottom: 50, containLabel: true } };
  if (chart.type === 'bar' || chart.type === 'line') {
    return { ...base, xAxis: { type: 'category', data: chart.x_labels || [] }, yAxis: { type: 'value' },
      series: (chart.series || []).map(s => ({ type: chart.type as 'bar' | 'line', name: s.name, data: s.data.map(p => p.value) })) };
  }
  if (chart.type === 'pie') return { ...base, series: [{ type: 'pie', radius: '65%', data: (chart.series?.[0]?.data || []).map(p => ({ ...p, value: p.value ?? undefined })) }] };
  if (chart.type === 'radar') return { ...base, radar: { indicator: chart.indicators || [] },
    series: [{ type: 'radar', data: [{ name: chart.series?.[0]?.name || '', value: (chart.series?.[0]?.data || []).map(p => p.value) }] }] };
  if (chart.type === 'heatmap') {
    const values = (chart.values || []).map(row => row[2]);
    return { ...base, xAxis: { type: 'category', data: chart.x_labels || [] }, yAxis: { type: 'category', data: chart.y_labels || [] },
      visualMap: { min: Math.min(0, ...values), max: Math.max(1, ...values), calculable: true, orient: 'horizontal', bottom: 0 },
      series: [{ type: 'heatmap', data: chart.values || [] }] };
  }
  return undefined;
}
