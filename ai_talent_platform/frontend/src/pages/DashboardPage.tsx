import { useState } from 'react';
import { App, Button, Progress, Select, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ArrowDownOutlined, ArrowRightOutlined, ArrowUpOutlined, BookOutlined, CalendarOutlined, CheckCircleOutlined, DownloadOutlined, FileSearchOutlined, NodeIndexOutlined, RobotOutlined, SolutionOutlined, TeamOutlined, ThunderboltOutlined, UserAddOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import { Chart, PersonAvatar, PotentialTag, SkillTags, baseGrid, chartAxis, chartTooltip } from '../components/Common';
import { demoTalents, monthlyTrend } from '../services/mockData';
import { downloadText } from '../utils';
import { useAuth } from '../store/AuthContext';

const statCards = [
  { label: '在职人才总数', value: '1,286', unit: '人', icon: <TeamOutlined/>, color: '#4269df', bg: '#edf2ff', trend: '+8.2%', note: '较上月净增 14 人' },
  { label: '在招岗位', value: '86', unit: '个', icon: <SolutionOutlined/>, color: '#815ec7', bg: '#f3effd', trend: '+6', note: '其中 12 个紧急岗位' },
  { label: '高潜人才占比', value: '23.8', unit: '%', icon: <NodeIndexOutlined/>, color: '#1c9b85', bg: '#eaf8f4', trend: '+2.4%', note: 'A 级高潜人才 306 人' },
  { label: '培训完成率', value: '78.6', unit: '%', icon: <BookOutlined/>, color: '#d58a32', bg: '#fff5e9', trend: '+5.4%', note: '本月新增学时 3,842h' },
];
const activities = [
  { icon: <FileSearchOutlined/>, bg: '#edf2ff', color: '#4b6ddd', title: 'AI 完成 12 份简历解析，平均准确率 98.2%', tag: '简历解析', time: '10 分钟前' },
  { icon: <NodeIndexOutlined/>, bg: '#eaf8f4', color: '#19947d', title: '「高级后端工程师」匹配任务已完成', tag: '智能匹配', time: '32 分钟前' },
  { icon: <UserAddOutlined/>, bg: '#f3effd', color: '#8062bf', title: '新增 8 位 A 级高潜人才至继任者梯队', tag: '人才盘点', time: '1 小时前' },
  { icon: <CheckCircleOutlined/>, bg: '#fff5e9', color: '#d38a31', title: '10 月人才结构分析报告已生成', tag: '数据洞察', time: '2 小时前' },
];

export default function DashboardPage() {
  const [metric, setMetric] = useState('total'); const { user, demo } = useAuth(); const { message } = App.useApp(); const navigate = useNavigate();
  const trendOption: EChartsOption = {
    tooltip: chartTooltip, grid: { ...baseGrid, left: 48, top: 25 },
    xAxis: { type: 'category' as const, boundaryGap: false, data: monthlyTrend.months, ...chartAxis },
    yAxis: { type: 'value' as const, min: metric === 'total' ? 1000 : 0, ...chartAxis, splitLine: { lineStyle: { color: '#eef1f5', type: 'dashed' as const } } },
    series: metric === 'total' ? [{ name: '人才总数', type: 'line', data: monthlyTrend.total, smooth: .35, symbol: 'circle', symbolSize: 6, showSymbol: false, lineStyle: { width: 2.5, color: '#4168e8' }, itemStyle: { color: '#4168e8', borderColor: '#fff', borderWidth: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(65,104,232,.23)' }, { offset: 1, color: 'rgba(65,104,232,.015)' }] } } }]
      : [
        { name: '新入职', type: 'bar', data: monthlyTrend.joined, barWidth: 12, itemStyle: { color: '#5275e7', borderRadius: [3,3,0,0] } },
        { name: '离职', type: 'bar', data: monthlyTrend.left, barWidth: 12, itemStyle: { color: '#e4b565', borderRadius: [3,3,0,0] } },
      ],
  };
  const educationOption: EChartsOption = {
    tooltip: { ...chartTooltip, trigger: 'item', formatter: '{b}<br/>{c} 人 · {d}%' },
    series: [{ type: 'pie', radius: ['64%', '83%'], center: ['50%', '45%'], avoidLabelOverlap: true, label: { show: false }, itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 }, data: [
      { value: 548, name: '本科', itemStyle: { color: '#476ce5' } }, { value: 392, name: '硕士', itemStyle: { color: '#7693ee' } },
      { value: 128, name: '博士', itemStyle: { color: '#61b4ac' } }, { value: 218, name: '大专及以下', itemStyle: { color: '#e2e7f1' } },
    ] }],
  };
  const columns: ColumnsType<(typeof demoTalents)[number]> = [
    { title: '人才', dataIndex: 'name', render: (_, r) => <div className="talent-cell"><PersonAvatar name={r.name}/><div><div className="talent-cell-name">{r.name}</div><div className="talent-cell-role">{r.current_position}</div></div></div> },
    { title: '所属部门', dataIndex: 'organization_name' },
    { title: '核心技能', dataIndex: 'skill_tags', render: (v) => <SkillTags tags={v} max={2}/> },
    { title: '潜力', dataIndex: 'potential_level', width: 60, render: (v) => <PotentialTag level={v}/> },
    { title: '综合评分', dataIndex: 'match_score', width: 85, render: (v) => <span className="score-text">{v}</span> },
    { title: '', width: 45, render: () => <Button type="text" size="small" icon={<ArrowRightOutlined/>} onClick={() => navigate('/talents')}/> },
  ];
  const exportReport = () => { downloadText('2026年度人才驾驶舱报告.txt', `智才云 TalentOS 人才驾驶舱报告\n生成时间：2026-11-04\n在职人才：1,286 人\n在招岗位：86 个\n高潜人才占比：23.8%\n培训完成率：78.6%`); message.success('驾驶舱报告已导出'); };
  return <>
    <div className="welcome-row"><div><h1 className="welcome-title">上午好，{user?.real_name || '林经理'} <span style={{ fontSize: 20 }}>👋</span>{demo && <span className="demo-badge">演示环境</span>}</h1><p className="welcome-sub">这里是您的人才工作简报，今天也一起发现组织的无限可能。</p></div><div className="flex items-center gap-12"><div className="date-display"><CalendarOutlined/>2026 年 11 月 4 日 · 星期三</div><Button icon={<DownloadOutlined/>} onClick={exportReport}>导出报告</Button></div></div>
    <div className="insight-strip"><div className="insight-icon"><ThunderboltOutlined/></div><div className="insight-content"><div className="insight-title">AI 组织洞察</div><div className="insight-text">技术研发部人才规模环比增长 <b>12.6%</b>，但高级算法岗位人才储备低于安全线。建议优先启动 <b>AI 算法专家</b> 岗位匹配，并关注 3 位核心人才的发展诉求。</div></div><Button className="insight-action" type="link" onClick={() => navigate('/analytics')}>查看完整分析 <ArrowRightOutlined/></Button></div>
    <div className="stats-grid">{statCards.map((s) => <div className="card stat-card" key={s.label}><div className="stat-top"><span className="stat-label">{s.label}</span><span className="stat-icon" style={{ color: s.color, background: s.bg }}>{s.icon}</span></div><div className="stat-value">{s.value}<small>{s.unit}</small></div><div className="stat-bottom"><span className="trend-chip"><ArrowUpOutlined/>{s.trend}</span><span>{s.note}</span></div></div>)}</div>
    <div className="dashboard-grid">
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才规模趋势</h3><div className="section-card-subtitle">组织人才数量与流动变化</div></div><Select size="small" value={metric} onChange={setMetric} options={[{ value: 'total', label: '人才总量' }, { value: 'flow', label: '人才流动' }]} style={{ width: 100 }}/></div><Chart option={trendOption} height={278}/></section>
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才学历结构</h3><div className="section-card-subtitle">在职人才学历分布</div></div><Button type="text" size="small" icon={<RobotOutlined/>}/></div><Chart option={educationOption} height={220}/><div className="structure-total"><strong>1,286</strong><span>人才总数</span></div><div className="structure-legend">{[['本科','548','#476ce5'],['硕士','392','#7693ee'],['博士','128','#61b4ac'],['大专及以下','218','#e2e7f1']].map(([n,v,c]) => <div className="structure-legend-item" key={n}><span className="legend-dot" style={{ background: c, marginRight: 7 }}/><span className="muted">{n}</span><b>{v}</b></div>)}</div></section>
    </div>
    <div className="bottom-grid">
      <section className="card table-card"><div className="card-header" style={{ marginBottom: 4 }}><div><h3 className="section-card-title">高潜人才关注</h3><div className="section-card-subtitle">基于 AI 画像与最新测评综合推荐</div></div><Button type="link" size="small" onClick={() => navigate('/talents')}>查看全部 <ArrowRightOutlined/></Button></div><Table rowKey="id" columns={columns} dataSource={demoTalents.slice(0, 5)} pagination={false} size="middle"/></section>
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">AI 智能动态</h3><div className="section-card-subtitle">智能体实时工作进展</div></div><span className="status-badge success">运行中</span></div><div className="activity-list">{activities.map((a) => <div className="activity-item" key={a.title}><span className="activity-icon" style={{ background: a.bg, color: a.color }}>{a.icon}</span><div className="activity-body"><div className="activity-title">{a.title}<span className="activity-tag">{a.tag}</span></div><div className="activity-meta">{a.time}</div></div></div>)}</div></section>
    </div>
    <div className="bottom-grid mt-16">
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">关键岗位招聘进展</h3><div className="section-card-subtitle">编制到位与人才储备情况</div></div><Button type="link" size="small" onClick={() => navigate('/positions')}>岗位管理 <ArrowRightOutlined/></Button></div><div className="flex gap-16" style={{ flexWrap: 'wrap' }}>{[['AI 算法专家',35, '0 / 2 人', '#6f63d8'],['高级后端工程师',67,'2 / 3 人','#4269df'],['组织发展经理',50,'1 / 2 人','#23a186'],['数据产品经理',75,'3 / 4 人','#d59339']].map(([name,p,count,color]) => <div key={name as string} style={{ flex: 1, minWidth: 210 }}><div className="mini-task-top"><span>{name}</span><b>{count}</b></div><Progress percent={p as number} showInfo={false} strokeColor={color as string} trailColor="#edf0f5" size="small"/></div>)}</div></section>
      <section className="card chart-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'linear-gradient(120deg,#f4f7ff,#fff)' }}><div><h3 className="section-card-title">需要更深入的洞察？</h3><div className="section-card-subtitle" style={{ marginTop: 8 }}>用自然语言，向 AI 询问任何人才问题</div></div><Button type="primary" icon={<RobotOutlined/>} onClick={() => navigate('/assistant')}>立即提问</Button></section>
    </div>
  </>;
}
