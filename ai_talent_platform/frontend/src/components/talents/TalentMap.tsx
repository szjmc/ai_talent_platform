import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import { MapChart } from 'echarts/charts';
import { GeoComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import chinaGeo from './chinaGeo.json';
import type { Talent } from '../../types';
import type { TalentFilterState } from './types';

echarts.use([MapChart, GeoComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);
echarts.registerMap('china', chinaGeo as never);

export default function TalentMap({ talents, resultCount, filters, onFilterChange }: { talents: Talent[]; resultCount: number; filters: TalentFilterState; onFilterChange: (patch: Partial<TalentFilterState>) => void }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.ECharts | null>(null);
  const distribution = useMemo(() => {
    const counts: Record<string, number> = {};
    talents.forEach(talent => { if (talent.province) counts[talent.province] = (counts[talent.province] || 0) + 1; });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [talents]);
  const max = Math.max(1, ...distribution.map(item => item.value));

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current); instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(chartRef.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);
  useEffect(() => {
    const chart = instance.current; if (!chart) return;
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'item', backgroundColor: '#173d48', borderWidth: 0, textStyle: { color: '#fff', fontSize: 12 }, formatter: (params: unknown) => { const item = params as { name: string; data?: { value?: number } }; return `<b>${item.name}</b><br/>${item.data?.value || 0} 位人才`; } },
      visualMap: { min: 0, max, left: 15, bottom: 15, text: ['多', '少'], textStyle: { color: '#789095', fontSize: 10 }, itemWidth: 10, itemHeight: 80, inRange: { color: ['#edf5f2', '#b8d9ce', '#68a795', '#236e61'] } },
      geo: { map: 'china', roam: true, zoom: 1.08, scaleLimit: { min: .8, max: 8 }, selectedMode: 'single', label: { show: false }, itemStyle: { areaColor: '#edf2f1', borderColor: '#fff', borderWidth: .8 }, emphasis: { label: { show: true, color: '#254d50', fontSize: 10 }, itemStyle: { areaColor: '#d8b577' } }, select: { label: { show: true, color: '#254d50' }, itemStyle: { areaColor: '#d8b577' } }, regions: filters.province ? [{ name: filters.province, selected: true }] : [] },
      series: [{ type: 'map', geoIndex: 0, data: distribution }],
    } as EChartsOption, true);
    chart.off('click'); chart.on('click', (params: { name?: string }) => { if (params.name) onFilterChange({ province: filters.province === params.name ? undefined : params.name }); });
  }, [distribution, max, filters.province, onFilterChange]);

  return <section className="talent-map-panel">
    <div className="map-panel-heading"><div><div className="section-eyebrow">DOMESTIC TALENT FOOTPRINT</div><h2>国内人才热力地图</h2></div><div className="map-location-stat"><b>{distribution.length}</b><span>覆盖省级地域</span></div></div>
    <div className="map-breadcrumb"><button className="active" onClick={() => onFilterChange({ province: undefined })}>全国</button>{filters.province && <><span>/</span><b>{filters.province}</b></>}<span className="map-breadcrumb-hint">点击省份联动筛选 · 滚轮缩放</span></div>
    <div className="talent-world-map" ref={chartRef} />
    <div className="map-filter-footer"><span>{filters.province || '全国'} · {resultCount} 位人才</span>{filters.province && <button onClick={() => onFilterChange({ province: undefined })}>清除地域筛选</button>}</div>
  </section>;
}
