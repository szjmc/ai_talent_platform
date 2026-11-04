import { useMemo } from 'react';
import { Button, Tag } from 'antd';
import { CloseOutlined, EnvironmentOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';
import chinaGeoJson from './chinaGeo.json';
import type { ProvinceTalentStat } from './types';

// 仅打包全国轮廓 GeoJSON（源自 ISC 许可的 china-map-geojson），不请求第三方地图服务。
echarts.registerMap('talent-china', chinaGeoJson as never);

const provinceStats: ProvinceTalentStat[] = [
  { name: '北京', value: 268, highPotential: 76 }, { name: '上海', value: 236, highPotential: 68 },
  { name: '广东', value: 218, highPotential: 54 }, { name: '浙江', value: 136, highPotential: 42 },
  { name: '江苏', value: 112, highPotential: 32 }, { name: '四川', value: 86, highPotential: 21 },
  { name: '湖北', value: 72, highPotential: 18 }, { name: '山东', value: 48, highPotential: 12 },
  { name: '福建', value: 36, highPotential: 9 }, { name: '陕西', value: 28, highPotential: 7 },
  { name: '湖南', value: 22, highPotential: 5 }, { name: '安徽', value: 16, highPotential: 4 },
  { name: '重庆', value: 8, highPotential: 2 },
];
const provinceNames = chinaGeoJson.features.map((feature) => feature.properties?.name).filter(Boolean) as string[];
const completeStats = [...provinceNames.map((name) => ({ name, value: 0, highPotential: 0 })).map((item) => provinceStats.find((stat) => stat.name === item.name) || item)];

interface Props { selectedProvince?: string; onProvinceChange: (province?: string) => void }

/** 国内人才分布热力地图。点击同一省份再次取消筛选。 */
export default function TalentMap({ selectedProvince, onProvinceChange }: Props) {
  const ranking = useMemo(() => [...provinceStats].sort((a, b) => b.value - a.value).slice(0, 6), []);
  const option = useMemo<EChartsOption>(() => ({
    animationDuration: 350,
    tooltip: {
      trigger: 'item', backgroundColor: 'rgba(255,255,255,.98)', borderColor: '#e1e7f0', borderWidth: 1,
      padding: [12, 15], textStyle: { color: '#344054', fontSize: 12 },
      formatter: (value: unknown) => {
        const params = value as { name: string };
        const stat = completeStats.find((item) => item.name === params.name);
        return `<div style="min-width:150px"><b style="font-size:14px">${params.name}</b><div style="height:1px;background:#edf0f4;margin:9px 0"></div><div style="display:flex;justify-content:space-between;line-height:24px"><span style="color:#7a8597">人才数量</span><b>${stat?.value || 0} 人</b></div><div style="display:flex;justify-content:space-between;line-height:24px"><span style="color:#7a8597">高潜人才</span><b style="color:#315efb">${stat?.highPotential || 0} 人</b></div><div style="font-size:10px;color:#98a2b3;margin-top:6px">点击筛选该地区人才简历</div></div>`;
      },
    },
    visualMap: {
      min: 0, max: 280, left: 8, bottom: 6, orient: 'horizontal', itemWidth: 12, itemHeight: 90,
      text: ['人才密度高', '低'], calculable: false, textStyle: { color: '#8a95a6', fontSize: 10 },
      inRange: { color: ['#f2f4f8', '#e6ecf8', '#c4d1ee', '#8da7df', '#5279ce', '#3157ae'] },
    },
    series: [{
      name: '人才数量', type: 'map', map: 'talent-china', roam: false, selectedMode: 'single',
      layoutCenter: ['50%', '49%'], layoutSize: '94%', aspectScale: 0.82,
      itemStyle: { areaColor: '#f3f5f8', borderColor: '#ffffff', borderWidth: 0.8 },
      emphasis: { label: { show: true, color: '#26364f', fontSize: 11, fontWeight: 500 }, itemStyle: { areaColor: '#8ba9e2', borderColor: '#5273cf', borderWidth: 1 } },
      select: { label: { show: true, color: '#234694', fontWeight: 600 }, itemStyle: { borderColor: '#315efb', borderWidth: 1.4 } },
      label: { show: false }, data: completeStats.map((stat) => ({ ...stat, selected: stat.name === selectedProvince })),
    }],
  }), [selectedProvince]);

  return (
    <section className="card talent-map-card">
      <div className="coverflow-header">
        <div><h3 className="section-card-title"><EnvironmentOutlined className="mr-2 text-[#5273df]" />人才地图</h3><p>按工作地统计 · 点击省份联动筛选人才简历与列表</p></div>
        <div className="flex items-center gap-2">{selectedProvince && <Tag closable closeIcon={<CloseOutlined />} onClose={(event) => { event.preventDefault(); onProvinceChange(); }} color="blue">{selectedProvince}</Tag>}<Button size="small" disabled={!selectedProvince} onClick={() => onProvinceChange()}>查看全国</Button></div>
      </div>
      <div className="talent-map-content">
        <div className="talent-map-chart"><ReactECharts option={option} style={{ height: 390, width: '100%' }} notMerge lazyUpdate onEvents={{ click: (params: { name: string }) => onProvinceChange(selectedProvince === params.name ? undefined : params.name) }} /></div>
        <aside className="province-ranking">
          <div className="province-ranking-title">人才分布 TOP 6<span>人数</span></div>
          {ranking.map((item, index) => <button type="button" key={item.name} className={selectedProvince === item.name ? 'selected' : ''} onClick={() => onProvinceChange(selectedProvince === item.name ? undefined : item.name)}>
            <span className="ranking-index">{String(index + 1).padStart(2, '0')}</span><span className="ranking-name">{item.name}</span><b>{item.value}</b>
            <span className="ranking-bar"><i style={{ width: `${item.value / ranking[0].value * 100}%` }} /></span>
            <small>高潜 {item.highPotential} 人</small>
          </button>)}
          <div className="province-summary"><span>覆盖省级区域</span><b>{provinceStats.length}<small> 个</small></b><p>人才数据已完成地域标准化与隐私脱敏</p></div>
        </aside>
      </div>
    </section>
  );
}
