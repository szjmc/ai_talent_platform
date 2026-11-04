import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { MapChart } from 'echarts/charts';
import { GeoComponent, TitleComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { feature } from 'topojson-client';
import worldTopology from 'world-atlas/countries-110m.json';
import chinaGeo from './chinaGeo.json';
import type { Talent } from '../../types';
import type { TalentFilterState } from './types';

echarts.use([MapChart, GeoComponent, TitleComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);
type GeoFeature = { id?: string | number; properties?: { name?: string }; geometry: unknown };
const worldFeatures = (feature(worldTopology as never, worldTopology.objects.countries as never) as unknown as { features: GeoFeature[] }).features;
echarts.registerMap('world', { type: 'FeatureCollection', features: worldFeatures } as never);
echarts.registerMap('china', chinaGeo as never);

function geometryCenter(item?: GeoFeature): [number, number] | undefined {
  if (!item) return undefined;
  const points: number[][] = [];
  const walk = (value: unknown) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === 'number' && typeof value[1] === 'number') points.push(value as number[]);
    else value.forEach(walk);
  };
  walk((item.geometry as { coordinates?: unknown }).coordinates);
  if (!points.length) return undefined;
  const lng = points.map(p => p[0]); const lat = points.map(p => p[1]);
  return [(Math.min(...lng) + Math.max(...lng)) / 2, (Math.min(...lat) + Math.max(...lat)) / 2];
}

export default function TalentMap({ talents, resultCount, filters, onFilterChange }: { talents: Talent[]; resultCount: number; filters: TalentFilterState; onFilterChange: (patch: Partial<TalentFilterState>) => void }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const instance = useRef<echarts.ECharts | null>(null);
  const [level, setLevel] = useState<'world' | 'china'>(filters.country === 'China' ? 'china' : 'world');
  const selectedCountry = filters.country || '';
  const selectedFeature = worldFeatures.find(item => item.properties?.name === selectedCountry);
  const distribution = useMemo(() => {
    const result: Record<string, number> = {};
    talents.forEach(talent => {
      if (level === 'china' && talent.country && talent.country !== 'China') return;
      const name = level === 'china' ? talent.province : talent.country;
      if (name) result[name] = (result[name] || 0) + 1;
    });
    return Object.entries(result).map(([name, value]) => ({ name, value }));
  }, [talents, level]);
  const totalLocations = distribution.length;
  const max = Math.max(1, ...distribution.map(item => item.value));

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current);
    instance.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartRef.current);
    return () => { observer.disconnect(); chart.dispose(); instance.current = null; };
  }, []);

  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;
    const isChina = level === 'china';
    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item', backgroundColor: '#132f3e', borderWidth: 0, padding: [10, 13],
        textStyle: { color: '#fff', fontSize: 12 },
        formatter: (params: unknown) => {
          const item = params as { name: string; value?: number; data?: { value?: number } };
          const value = item.data?.value;
          return `<b>${item.name}</b><br/>${value ? `${value} 位人才` : '暂无已登记人才'}`;
        },
      },
      visualMap: {
        show: distribution.length > 0, min: 0, max, left: 14, bottom: 16, calculable: false,
        text: ['多', '少'], textStyle: { color: '#668087', fontSize: 10 }, itemWidth: 10, itemHeight: 76,
        inRange: { color: isChina ? ['#edf5f2', '#b9d8ce', '#67a795', '#236e61'] : ['#eaf1f3', '#b6d0d7', '#6095a6', '#24566f'] },
      },
      geo: {
        map: isChina ? 'china' : 'world', roam: true, zoom: isChina ? 1.08 : selectedCountry ? 2.4 : 1.12,
        center: !isChina && selectedCountry ? geometryCenter(selectedFeature) : undefined,
        scaleLimit: { min: 0.8, max: 12 }, selectedMode: 'single', silent: false,
        label: { show: false }, itemStyle: { areaColor: '#eef2f2', borderColor: '#fff', borderWidth: 0.7 },
        emphasis: { label: { show: true, color: '#193e4d', fontSize: 10 }, itemStyle: { areaColor: '#d4a65e' } },
        select: { label: { show: true, color: '#173e4c' }, itemStyle: { areaColor: '#e0b773' } },
        regions: isChina && filters.province ? [{ name: filters.province, selected: true }] : !isChina && selectedCountry ? [{ name: selectedCountry, selected: true }] : [],
      },
      series: [{ type: 'map', geoIndex: 0, data: distribution }],
    } as EChartsOption, true);
    chart.off('click');
    chart.on('click', (params: { name?: string }) => {
      if (!params.name) return;
      if (isChina) onFilterChange({ province: filters.province === params.name ? undefined : params.name });
      else {
        onFilterChange({ country: params.name, province: undefined });
        if (params.name === 'China') setLevel('china');
      }
    });
  }, [level, distribution, max, selectedCountry, selectedFeature, filters, onFilterChange]);

  const backToWorld = () => { setLevel('world'); onFilterChange({ country: undefined, province: undefined }); };
  return <section className="talent-map-panel">
    <div className="map-panel-heading"><div><div className="section-eyebrow">GLOBAL TALENT FOOTPRINT</div><h2>全球人才分布</h2></div><div className="map-location-stat"><b>{totalLocations}</b><span>{level === 'china' ? '省级地域' : '国家 / 地区'}</span></div></div>
    <div className="map-breadcrumb">
      <button className={level === 'world' ? 'active' : ''} onClick={backToWorld}>全球</button>
      {level === 'china' && <><span>/</span><button className="active" onClick={() => onFilterChange({ province: undefined })}>中国</button></>}
      {selectedCountry && level === 'world' && <><span>/</span><b>{selectedCountry}</b></>}
      <span className="map-breadcrumb-hint">点击国家放大 · 中国可下钻至省域 · 滚轮缩放</span>
    </div>
    <div className="talent-world-map" ref={chartRef} />
    {!distribution.length && <div className="map-data-empty">当前数据没有{level === 'china' ? '省份' : '国家'}字段，地图仅用于区域筛选</div>}
    <div className="map-filter-footer">
      <span>{filters.province || selectedCountry || '未限定地域'} · {resultCount} 位人才</span>
      {(selectedCountry || filters.province) && <button onClick={backToWorld}>清除地域筛选</button>}
    </div>
  </section>;
}
