import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { feature } from 'topojson-client';
import worldTopology from 'world-atlas/countries-110m.json';
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
type GeoFeature = { properties?: { name?: string }; geometry: { type: string; coordinates: unknown } };
const countries = (feature(worldTopology as never, worldTopology.objects.countries as never) as unknown as { features: GeoFeature[] }).features;
const countryNames: Record<string, string> = { China: '中国', 'United States of America': '美国', Singapore: '新加坡', Germany: '德国', 'United Kingdom': '英国', Japan: '日本', France: '法国', Canada: '加拿大', Australia: '澳大利亚' };
const displayCountry = (name: string) => countryNames[name] || name;
// 反向映射：中文国名 -> GeoJSON 英文名（world-atlas 使用英文）
const countryToEnglish: Record<string, string> = { '中国': 'China', '美国': 'United States of America', '新加坡': 'Singapore', '德国': 'Germany', '英国': 'United Kingdom', '日本': 'Japan', '法国': 'France', '加拿大': 'Canada', '澳大利亚': 'Australia' };
const normalizeCountry = (name: string) => countryToEnglish[name] || name;

function eachPolygon(item: GeoFeature, callback: (polygon: number[][][]) => void) {
  if (item.geometry.type === 'Polygon') callback(item.geometry.coordinates as number[][][]);
  if (item.geometry.type === 'MultiPolygon') (item.geometry.coordinates as number[][][][]).forEach(callback);
}
function countryCenter(item: GeoFeature): [number, number] {
  const points: number[][] = []; eachPolygon(item, polygon => polygon.forEach(ring => ring.forEach(point => points.push(point))));
  if (!points.length) return [0, 0];
  const lng = points.map(point => point[0]); const lat = points.map(point => point[1]);
  return [(Math.min(...lng) + Math.max(...lng)) / 2, (Math.min(...lat) + Math.max(...lat)) / 2];
}
function ringContains(lng: number, lat: number, ring: number[][]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]; const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lng < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function countryAtPoint(lng: number, lat: number) {
  return countries.find(country => {
    let matched = false;
    eachPolygon(country, polygon => { if (!matched && ringContains(lng, lat, polygon[0]) && !polygon.slice(1).some(ring => ringContains(lng, lat, ring))) matched = true; });
    return matched;
  })?.properties?.name;
}
function latLngPosition(lng: number, lat: number, radius: number) {
  const longitude = THREE.MathUtils.degToRad(lng); const latitude = THREE.MathUtils.degToRad(lat);
  return new THREE.Vector3(-radius * Math.cos(latitude) * Math.cos(longitude), radius * Math.sin(latitude), radius * Math.cos(latitude) * Math.sin(longitude));
}
function makeEarthTexture(counts: Record<string, number>, selected?: string) {
  const canvas = document.createElement('canvas'); canvas.width = 2048; canvas.height = 1024;
  const context = canvas.getContext('2d')!; context.fillStyle = '#e8f0f1'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = 'rgba(112,145,151,.08)'; context.lineWidth = 1;
  for (let lng = -150; lng <= 150; lng += 30) { const x = (lng + 180) / 360 * canvas.width; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, canvas.height); context.stroke(); }
  for (let lat = -60; lat <= 60; lat += 30) { const y = (90 - lat) / 180 * canvas.height; context.beginPath(); context.moveTo(0, y); context.lineTo(canvas.width, y); context.stroke(); }
  countries.forEach(country => {
    const name = country.properties?.name || ''; const count = counts[name] || 0;
    context.fillStyle = selected === name ? '#c4a065' : count >= 3 ? '#4b897e' : count > 0 ? '#8db5ac' : '#dce5e3';
    context.strokeStyle = '#f7f9f8'; context.lineWidth = 1.25;
    eachPolygon(country, polygon => {
      context.beginPath(); polygon.forEach(ring => ring.forEach(([lng, lat], index) => { const x = (lng + 180) / 360 * canvas.width; const y = (90 - lat) / 180 * canvas.height; if (index === 0) context.moveTo(x, y); else context.lineTo(x, y); }));
      context.closePath(); context.fill('evenodd'); context.stroke();
    });
  });
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; return texture;
}

function CountryDetailMap({ country, talents, filters, onFilterChange }: { country: string; talents: Talent[]; filters: TalentFilterState; onFilterChange: (patch: Partial<TalentFilterState>) => void }) {
  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!chartRef.current) return;
    const isChina = normalizeCountry(country) === 'China'; const mapName = isChina ? 'china' : `country-${country}`;
    if (!isChina) {
      const selected = countries.find(item => item.properties?.name === normalizeCountry(country));
      if (selected) echarts.registerMap(mapName, { type: 'FeatureCollection', features: [selected] } as never);
    }
    const data = isChina ? Object.entries(talents.filter(item => normalizeCountry(item.country || '') === 'China').reduce<Record<string, number>>((result, talent) => { if (talent.province) result[talent.province] = (result[talent.province] || 0) + 1; return result; }, {})).map(([name, value]) => ({ name, value })) : [{ name: country, value: talents.filter(item => normalizeCountry(item.country || '') === country).length }];
    const max = Math.max(1, ...data.map(item => item.value));
    const chart = echarts.init(chartRef.current);
    chart.setOption({
      backgroundColor: 'transparent', tooltip: { trigger: 'item', backgroundColor: '#173d48', borderWidth: 0, textStyle: { color: '#fff', fontSize: 12 }, formatter: (params: unknown) => { const item = params as { name: string; data?: { value?: number } }; return `<b>${item.name}</b><br/>${item.data?.value || 0} 位人才`; } },
      visualMap: { show: isChina, min: 0, max, left: 15, bottom: 15, text: ['多', '少'], textStyle: { color: '#789095', fontSize: 10 }, itemWidth: 10, itemHeight: 75, inRange: { color: ['#edf5f2', '#b8d9ce', '#68a795', '#236e61'] } },
      geo: { map: mapName, roam: true, zoom: isChina ? 1.08 : 1.2, scaleLimit: { min: .8, max: 10 }, selectedMode: 'single', label: { show: !isChina, color: '#426a6b', fontSize: 12 }, itemStyle: { areaColor: isChina ? '#edf2f1' : '#8db5ac', borderColor: '#fff', borderWidth: .8 }, emphasis: { label: { show: true, color: '#254d50', fontSize: 10 }, itemStyle: { areaColor: '#d8b577' } }, select: { itemStyle: { areaColor: '#d8b577' } }, regions: isChina && filters.province ? [{ name: filters.province, selected: true }] : [] },
      series: [{ type: 'map', geoIndex: 0, data }],
    } as EChartsOption);
    chart.on('click', (params: { name?: string }) => { if (isChina && params.name) onFilterChange({ province: filters.province === params.name ? undefined : params.name }); });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(chartRef.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [country, talents, filters.province, onFilterChange]);
  return <div className="country-detail-map" ref={chartRef} aria-label={`${displayCountry(country)}人才地图`} />;
}

export default function TalentMap({ talents, resultCount, filters, onFilterChange }: { talents: Talent[]; resultCount: number; filters: TalentFilterState; onFilterChange: (patch: Partial<TalentFilterState>) => void }) {
  const mountRef = useRef<HTMLDivElement>(null); const tooltipRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<'globe' | 'country'>(filters.country ? 'country' : 'globe');
  const filterCallback = useRef(onFilterChange); filterCallback.current = onFilterChange;
  const counts = useMemo(() => { const result: Record<string, number> = {}; talents.forEach(talent => { if (talent.country) { const en = normalizeCountry(talent.country); result[en] = (result[en] || 0) + 1; } }); return result; }, [talents]);
  const countryStats = useMemo(() => Object.entries(counts).sort((a, b) => b[1] - a[1]), [counts]);
  useEffect(() => { setView(filters.country ? 'country' : 'globe'); }, [filters.country]);
  const selectCountry = (name: string) => { onFilterChange({ country: displayCountry(name), province: undefined }); setView('country'); };
  const backToGlobe = () => { setView('globe'); onFilterChange({ country: undefined, province: undefined }); };

  useEffect(() => {
    const mount = mountRef.current; if (!mount || view !== 'globe') return;
    const scene = new THREE.Scene(); const camera = new THREE.PerspectiveCamera(37, 1, .1, 100); camera.position.set(0, 0, 3.35);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.setClearColor(0x000000, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.setAttribute('aria-label', '可拖拽旋转并点击国家下钻的全球人才地图'); mount.appendChild(renderer.domElement);
    const globe = new THREE.Group(); globe.rotation.set(-.12, -2.8, 0); scene.add(globe);
    const texture = makeEarthTexture(counts); texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const sphereGeometry = new THREE.SphereGeometry(1, 96, 64); const sphereMaterial = new THREE.MeshPhongMaterial({ map: texture, shininess: 5, specular: new THREE.Color('#b8c6c6') });
    const earth = new THREE.Mesh(sphereGeometry, sphereMaterial); globe.add(earth);
    scene.add(new THREE.AmbientLight('#ffffff', 1.7)); const keyLight = new THREE.DirectionalLight('#ffffff', 2.15); keyLight.position.set(-3, 2, 4); scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight('#c5ddda', .65); fillLight.position.set(3, -1, -2); scene.add(fillLight);
    const markerGeometry = new THREE.SphereGeometry(.025, 16, 12); const hitGeometry = new THREE.SphereGeometry(.065, 12, 8); const markers: THREE.Mesh[] = [];
    Object.entries(counts).forEach(([name, count]) => {
      const country = countries.find(item => item.properties?.name === name); if (!country) return;
      const [lng, lat] = countryCenter(country); const position = latLngPosition(lng, lat, 1.025);
      const dot = new THREE.Mesh(markerGeometry, new THREE.MeshBasicMaterial({ color: '#225e58' })); dot.position.copy(position); dot.scale.setScalar(1 + Math.min(count, 5) * .09); globe.add(dot);
      const hit = new THREE.Mesh(hitGeometry, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })); hit.position.copy(position); hit.userData.country = name; globe.add(hit); markers.push(hit);
    });
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2();
    let dragging = false; let moved = 0; let lastX = 0; let lastY = 0; let resumeAt = 0; let frame = 0;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const hitAt = (event: PointerEvent) => { const rect = renderer.domElement.getBoundingClientRect(); pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1); raycaster.setFromCamera(pointer, camera); return raycaster.intersectObjects([earth, ...markers], false)[0]; };
    const pointerDown = (event: PointerEvent) => { dragging = true; moved = 0; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); renderer.domElement.style.cursor = 'grabbing'; if (tooltipRef.current) tooltipRef.current.style.opacity = '0'; };
    const pointerMove = (event: PointerEvent) => {
      if (dragging) { const dx = event.clientX - lastX; const dy = event.clientY - lastY; moved += Math.abs(dx) + Math.abs(dy); globe.rotation.y += dx * .005; globe.rotation.x = THREE.MathUtils.clamp(globe.rotation.x + dy * .003, -.65, .65); lastX = event.clientX; lastY = event.clientY; resumeAt = performance.now() + 3500; }
      else { const hit = hitAt(event); const country = hit?.object.userData.country as string | undefined; renderer.domElement.style.cursor = hit ? 'pointer' : 'grab'; if (tooltipRef.current) { tooltipRef.current.style.opacity = hit ? '1' : '0'; if (hit) { tooltipRef.current.textContent = country ? `${displayCountry(country)} · ${counts[country]} 位人才 · 点击查看地图` : '点击查看国家地图'; const rect = mount.getBoundingClientRect(); tooltipRef.current.style.left = `${event.clientX - rect.left + 12}px`; tooltipRef.current.style.top = `${event.clientY - rect.top + 10}px`; } } }
    };
    const pointerUp = (event: PointerEvent) => {
      if (dragging && moved < 6) {
        const hit = hitAt(event); let name = hit?.object.userData.country as string | undefined;
        if (!name && hit?.object === earth && hit.uv) name = countryAtPoint(hit.uv.x * 360 - 180, hit.uv.y * 180 - 90);
        if (name) { filterCallback.current({ country: displayCountry(name), province: undefined }); setView('country'); }
      }
      dragging = false; renderer.domElement.style.cursor = 'grab'; if (renderer.domElement.hasPointerCapture(event.pointerId)) renderer.domElement.releasePointerCapture(event.pointerId);
    };
    renderer.domElement.style.cursor = 'grab'; renderer.domElement.style.touchAction = 'none'; renderer.domElement.addEventListener('pointerdown', pointerDown); renderer.domElement.addEventListener('pointermove', pointerMove); renderer.domElement.addEventListener('pointerup', pointerUp);
    const resize = () => { const width = mount.clientWidth; const height = mount.clientHeight; if (!width || !height) return; renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(mount); resize();
    const animate = (time: number) => { if (!reduceMotion && !dragging && time > resumeAt) globe.rotation.y += .00065; renderer.render(scene, camera); frame = requestAnimationFrame(animate); }; frame = requestAnimationFrame(animate);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); renderer.domElement.removeEventListener('pointerdown', pointerDown); renderer.domElement.removeEventListener('pointermove', pointerMove); renderer.domElement.removeEventListener('pointerup', pointerUp); sphereGeometry.dispose(); sphereMaterial.dispose(); texture.dispose(); markerGeometry.dispose(); hitGeometry.dispose(); globe.children.forEach(child => { if (child instanceof THREE.Mesh) (child.material as THREE.Material).dispose(); }); renderer.dispose(); renderer.domElement.remove(); };
  }, [counts, view]);

  return <section className="talent-map-panel talent-globe-panel">
    <div className="map-panel-heading"><div><div className="section-eyebrow">{view === 'globe' ? 'GLOBAL TALENT FOOTPRINT' : 'COUNTRY TALENT DETAIL'}</div><h2>{view === 'globe' ? '全球人才地图' : `${displayCountry(filters.country || '')}${normalizeCountry(filters.country || '') === 'China' ? '省级' : ''}人才地图`}</h2></div><div className="map-location-stat"><b>{view === 'globe' ? countryStats.length : resultCount}</b><span>{view === 'globe' ? '国家 / 地区' : '位匹配人才'}</span></div></div>
    <div className="map-breadcrumb"><button className={view === 'globe' ? 'active' : ''} onClick={backToGlobe}>全球球体</button>{view === 'country' && filters.country && <><span>/</span><b>{displayCountry(filters.country)}</b></>}<span className="map-breadcrumb-hint">{view === 'globe' ? '拖拽旋转 · 点击国家直接进入对应地图' : normalizeCountry(filters.country || '') === 'China' ? '点击省份联动筛选 · 滚轮缩放' : '拖拽移动 · 滚轮缩放'}</span></div>
    {view === 'globe' ? <div className="talent-globe-stage"><div className="talent-globe-canvas" ref={mountRef} /><div className="globe-tooltip" ref={tooltipRef} /><div className="globe-axis-label">GLOBAL VIEW · 360°</div></div> : filters.country && <CountryDetailMap country={filters.country} talents={talents} filters={filters} onFilterChange={onFilterChange} />}
    <div className="globe-country-chips">{countryStats.map(([name, count]) => <button key={name} className={filters.country === name ? 'active' : ''} onClick={() => filters.country === name ? backToGlobe() : selectCountry(name)}><i />{displayCountry(name)}<b>{count}</b></button>)}</div>
    <div className="map-filter-footer"><span>{filters.country ? displayCountry(filters.country) : '全球'} · {resultCount} 位人才</span>{filters.country && <button onClick={backToGlobe}>返回全球球体</button>}</div>
  </section>;
}
