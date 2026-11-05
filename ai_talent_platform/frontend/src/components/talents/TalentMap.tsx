import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { feature } from 'topojson-client';
import worldTopology from 'world-atlas/countries-110m.json';
import type { Talent } from '../../types';
import type { TalentFilterState } from './types';

type GeoFeature = { properties?: { name?: string }; geometry: { type: string; coordinates: unknown } };
const countries = (feature(worldTopology as never, worldTopology.objects.countries as never) as unknown as { features: GeoFeature[] }).features;
const countryNames: Record<string, string> = { China: '中国', 'United States of America': '美国', Singapore: '新加坡', Germany: '德国', 'United Kingdom': '英国', Japan: '日本', France: '法国', Canada: '加拿大', Australia: '澳大利亚' };
const displayCountry = (name: string) => countryNames[name] || name;

function eachPolygon(item: GeoFeature, callback: (polygon: number[][][]) => void) {
  if (item.geometry.type === 'Polygon') callback(item.geometry.coordinates as number[][][]);
  if (item.geometry.type === 'MultiPolygon') (item.geometry.coordinates as number[][][][]).forEach(callback);
}
function countryCenter(item: GeoFeature): [number, number] {
  const points: number[][] = [];
  eachPolygon(item, polygon => polygon.forEach(ring => ring.forEach(point => points.push(point))));
  if (!points.length) return [0, 0];
  const lng = points.map(point => point[0]); const lat = points.map(point => point[1]);
  return [(Math.min(...lng) + Math.max(...lng)) / 2, (Math.min(...lat) + Math.max(...lat)) / 2];
}
function latLngPosition(lng: number, lat: number, radius: number) {
  const longitude = THREE.MathUtils.degToRad(lng); const latitude = THREE.MathUtils.degToRad(lat);
  return new THREE.Vector3(-radius * Math.cos(latitude) * Math.cos(longitude), radius * Math.sin(latitude), radius * Math.cos(latitude) * Math.sin(longitude));
}
function makeEarthTexture(counts: Record<string, number>, selected?: string) {
  const canvas = document.createElement('canvas'); canvas.width = 2048; canvas.height = 1024;
  const context = canvas.getContext('2d')!;
  context.fillStyle = '#e8f0f1'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = 'rgba(112,145,151,.08)'; context.lineWidth = 1;
  for (let lng = -150; lng <= 150; lng += 30) { const x = (lng + 180) / 360 * canvas.width; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, canvas.height); context.stroke(); }
  for (let lat = -60; lat <= 60; lat += 30) { const y = (90 - lat) / 180 * canvas.height; context.beginPath(); context.moveTo(0, y); context.lineTo(canvas.width, y); context.stroke(); }
  countries.forEach(country => {
    const name = country.properties?.name || ''; const count = counts[name] || 0;
    context.fillStyle = selected === name ? '#c4a065' : count >= 3 ? '#4b897e' : count > 0 ? '#8db5ac' : '#dce5e3';
    context.strokeStyle = '#f7f9f8'; context.lineWidth = 1.25;
    eachPolygon(country, polygon => {
      context.beginPath();
      polygon.forEach(ring => ring.forEach(([lng, lat], index) => {
        const x = (lng + 180) / 360 * canvas.width; const y = (90 - lat) / 180 * canvas.height;
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      }));
      context.closePath(); context.fill('evenodd'); context.stroke();
    });
  });
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export default function TalentMap({ talents, resultCount, filters, onFilterChange }: { talents: Talent[]; resultCount: number; filters: TalentFilterState; onFilterChange: (patch: Partial<TalentFilterState>) => void }) {
  const mountRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const filterCallback = useRef(onFilterChange); filterCallback.current = onFilterChange;
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    talents.forEach(talent => { if (talent.country) result[talent.country] = (result[talent.country] || 0) + 1; });
    return result;
  }, [talents]);
  const countryStats = useMemo(() => Object.entries(counts).sort((a, b) => b[1] - a[1]), [counts]);

  useEffect(() => {
    const mount = mountRef.current; if (!mount) return;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(37, 1, .1, 100); camera.position.set(0, 0, 3.35);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.setAttribute('aria-label', '可拖拽旋转的全球人才地图'); mount.appendChild(renderer.domElement);
    const globe = new THREE.Group(); globe.rotation.set(-.12, -2.8, 0); scene.add(globe);
    const texture = makeEarthTexture(counts, filters.country); texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const sphereGeometry = new THREE.SphereGeometry(1, 96, 64);
    const sphereMaterial = new THREE.MeshPhongMaterial({ map: texture, shininess: 5, specular: new THREE.Color('#b8c6c6') });
    const earth = new THREE.Mesh(sphereGeometry, sphereMaterial); globe.add(earth);
    scene.add(new THREE.AmbientLight('#ffffff', 1.7));
    const keyLight = new THREE.DirectionalLight('#ffffff', 2.15); keyLight.position.set(-3, 2, 4); scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight('#c5ddda', .65); fillLight.position.set(3, -1, -2); scene.add(fillLight);
    const markerGeometry = new THREE.SphereGeometry(.025, 16, 12); const hitGeometry = new THREE.SphereGeometry(.065, 12, 8);
    const markers: THREE.Mesh[] = [];
    Object.entries(counts).forEach(([name, count]) => {
      const country = countries.find(item => item.properties?.name === name); if (!country) return;
      const [lng, lat] = countryCenter(country); const position = latLngPosition(lng, lat, 1.025);
      const material = new THREE.MeshBasicMaterial({ color: filters.country === name ? '#b07a31' : '#225e58' });
      const dot = new THREE.Mesh(markerGeometry, material); dot.position.copy(position); dot.scale.setScalar(1 + Math.min(count, 5) * .09); globe.add(dot);
      const hit = new THREE.Mesh(hitGeometry, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })); hit.position.copy(position); hit.userData.country = name; globe.add(hit); markers.push(hit);
    });
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2();
    let dragging = false; let moved = 0; let lastX = 0; let lastY = 0; let resumeAt = 0; let frame = 0;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const hitAt = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect(); pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
      raycaster.setFromCamera(pointer, camera); const first = raycaster.intersectObjects([earth, ...markers], false)[0];
      return first?.object.userData.country ? first : undefined;
    };
    const pointerDown = (event: PointerEvent) => { dragging = true; moved = 0; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); renderer.domElement.style.cursor = 'grabbing'; if (tooltipRef.current) tooltipRef.current.style.opacity = '0'; };
    const pointerMove = (event: PointerEvent) => {
      if (dragging) {
        const dx = event.clientX - lastX; const dy = event.clientY - lastY; moved += Math.abs(dx) + Math.abs(dy);
        globe.rotation.y += dx * .005; globe.rotation.x = THREE.MathUtils.clamp(globe.rotation.x + dy * .003, -.65, .65);
        lastX = event.clientX; lastY = event.clientY; resumeAt = performance.now() + 3500;
      } else {
        const hit = hitAt(event); renderer.domElement.style.cursor = hit ? 'pointer' : 'grab';
        if (tooltipRef.current) { tooltipRef.current.style.opacity = hit ? '1' : '0'; if (hit) { const name = hit.object.userData.country as string; tooltipRef.current.textContent = `${displayCountry(name)} · ${counts[name]} 位人才`; const rect = mount.getBoundingClientRect(); tooltipRef.current.style.left = `${event.clientX - rect.left + 12}px`; tooltipRef.current.style.top = `${event.clientY - rect.top + 10}px`; } }
      }
    };
    const pointerUp = (event: PointerEvent) => {
      if (dragging && moved < 6) { const hit = hitAt(event); if (hit) { const name = hit.object.userData.country as string; filterCallback.current({ country: filters.country === name ? undefined : name, province: undefined }); } }
      dragging = false; renderer.domElement.style.cursor = 'grab'; if (renderer.domElement.hasPointerCapture(event.pointerId)) renderer.domElement.releasePointerCapture(event.pointerId);
    };
    renderer.domElement.style.cursor = 'grab'; renderer.domElement.style.touchAction = 'none';
    renderer.domElement.addEventListener('pointerdown', pointerDown); renderer.domElement.addEventListener('pointermove', pointerMove); renderer.domElement.addEventListener('pointerup', pointerUp);
    const resize = () => { const width = mount.clientWidth; const height = mount.clientHeight; if (!width || !height) return; renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(mount); resize();
    const animate = (time: number) => { if (!reduceMotion && !dragging && time > resumeAt) globe.rotation.y += .00065; renderer.render(scene, camera); frame = requestAnimationFrame(animate); };
    frame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); renderer.domElement.removeEventListener('pointerdown', pointerDown); renderer.domElement.removeEventListener('pointermove', pointerMove); renderer.domElement.removeEventListener('pointerup', pointerUp);
      sphereGeometry.dispose(); sphereMaterial.dispose(); texture.dispose(); markerGeometry.dispose(); hitGeometry.dispose(); globe.children.forEach(child => { if (child instanceof THREE.Mesh) (child.material as THREE.Material).dispose(); }); renderer.dispose(); renderer.domElement.remove();
    };
  }, [counts, filters.country]);

  return <section className="talent-map-panel talent-globe-panel">
    <div className="map-panel-heading"><div><div className="section-eyebrow">GLOBAL TALENT FOOTPRINT</div><h2>全球人才地图</h2></div><div className="map-location-stat"><b>{countryStats.length}</b><span>国家 / 地区</span></div></div>
    <div className="map-breadcrumb"><button className="active" onClick={() => onFilterChange({ country: undefined, province: undefined })}>全球</button>{filters.country && <><span>/</span><b>{displayCountry(filters.country)}</b></>}<span className="map-breadcrumb-hint">拖拽旋转球体 · 点击人才标记筛选</span></div>
    <div className="talent-globe-stage"><div className="talent-globe-canvas" ref={mountRef} /><div className="globe-tooltip" ref={tooltipRef} /><div className="globe-axis-label">GLOBAL VIEW · 360°</div></div>
    <div className="globe-country-chips">{countryStats.map(([name, count]) => <button key={name} className={filters.country === name ? 'active' : ''} onClick={() => onFilterChange({ country: filters.country === name ? undefined : name, province: undefined })}><i />{displayCountry(name)}<b>{count}</b></button>)}</div>
    <div className="map-filter-footer"><span>{filters.country ? displayCountry(filters.country) : '全球'} · {resultCount} 位人才</span>{filters.country && <button onClick={() => onFilterChange({ country: undefined })}>清除国家筛选</button>}</div>
  </section>;
}
