import { useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Progress, Select, Spin, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ArrowRightOutlined, BookOutlined, CalendarOutlined, DownloadOutlined, NodeIndexOutlined, RobotOutlined, SolutionOutlined, TeamOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import dayjs from 'dayjs';
import { Chart, PersonAvatar, PotentialTag, SkillTags, baseGrid, chartAxis, chartTooltip } from '../components/Common';
import { analyticsApi, talentsApi, trainingsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText, formatNumber } from '../utils';
import { useAuth } from '../store/AuthContext';
import type { AssessmentSummary, DistributionResult, Talent, TrainingPlan, TrendResult } from '../types';

const emptyDistribution: DistributionResult = { type: 'education', source: '', items: [], truncated: false, warnings: [] };
const emptyTrend: TrendResult = { metric: 'talent_total', granularity: 'month', source: '', comparison: 'none', items: [], warnings: [] };

export default function DashboardPage() {
  const [metric, setMetric] = useState('total');
  const { user } = useAuth(); const { message } = App.useApp(); const navigate = useNavigate();
  const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [education, setEducation] = useState<DistributionResult>(emptyDistribution);
  const [orgDistribution, setOrgDistribution] = useState<DistributionResult>({ ...emptyDistribution, type: 'org' });
  const [trend, setTrend] = useState<TrendResult>(emptyTrend);
  const [summary, setSummary] = useState<AssessmentSummary | null>(null);
  const [talents, setTalents] = useState<Talent[]>([]);
  const [plans, setPlans] = useState<TrainingPlan[]>([]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true); setError('');
      const end = dayjs().startOf('month'); const start = end.subtract(11, 'month');
      const results = await Promise.allSettled([
        analyticsApi.distribution('education'),
        analyticsApi.distribution('org'),
        analyticsApi.trend({ start_month: start.format('YYYY-MM-DD'), end_month: end.format('YYYY-MM-DD'), comparison: 'mom' }),
        analyticsApi.assessmentSummary(),
        talentsApi.list({ page: 1, page_size: 5 }),
        trainingsApi.plans({ page: 1, page_size: 100 }),
      ]);
      if (!active) return;
      const [edu, org, tr, sum, tal, pl] = results;
      if (edu.status === 'fulfilled') setEducation(edu.value);
      if (org.status === 'fulfilled') setOrgDistribution(org.value);
      if (tr.status === 'fulfilled') setTrend(tr.value);
      if (sum.status === 'fulfilled') setSummary(sum.value);
      if (tal.status === 'fulfilled') setTalents(tal.value.items || []);
      if (pl.status === 'fulfilled') setPlans(pl.value.items || []);
      const failed = results.find((r) => r.status === 'rejected') as PromiseRejectedResult | undefined;
      if (failed) setError(formatApiError(failed.reason));
      setLoading(false);
    };
    void load();
    return () => { active = false; };
  }, []);

  const totalTalents = useMemo(() => {
    const last = [...trend.items].reverse().find((item) => !item.missing && item.value != null);
    if (last?.value != null) return last.value;
    return orgDistribution.items.reduce((acc, item) => acc + item.count, 0);
  }, [trend, orgDistribution]);
  const trainingCompletion = useMemo(() => {
    if (!plans.length) return null;
    return Math.round(plans.reduce((acc, p) => acc + (p.completion_rate || 0), 0) / plans.length * 1000) / 10;
  }, [plans]);
  const statCards = [
    { label: '在职人才总数', value: totalTalents ? formatNumber(totalTalents) : '—', unit: '人', icon: <TeamOutlined/>, color: '#4269df', bg: '#edf2ff', note: trend.source ? `来源：${trend.source}` : '等待后端趋势数据' },
    { label: '在招岗位', value: '—', unit: '个', icon: <SolutionOutlined/>, color: '#815ec7', bg: '#f3effd', note: '岗位列表接口待后端实现' },
    { label: '测评平均分', value: summary?.average_score != null ? String(summary.average_score) : '—', unit: '分', icon: <NodeIndexOutlined/>, color: '#1c9b85', bg: '#eaf8f4', note: summary ? `已评分 ${summary.scored_count} 条` : '等待后端测评统计' },
    { label: '培训完成率', value: trainingCompletion != null ? String(trainingCompletion) : '—', unit: '%', icon: <BookOutlined/>, color: '#d58a32', bg: '#fff5e9', note: plans.length ? `样本 ${plans.length} 个计划` : '等待后端培训计划' },
  ];

  const trendOption: EChartsOption = {
    tooltip: chartTooltip, grid: { ...baseGrid, left: 48, top: 25 },
    xAxis: { type: 'category', boundaryGap: false, data: trend.items.map((item) => dayjs(item.month).format('M月')), ...chartAxis },
    yAxis: { type: 'value', ...chartAxis, splitLine: { lineStyle: { color: '#eef1f5', type: 'dashed' } } },
    series: [{ name: '人才总数', type: 'line', data: trend.items.map((item) => item.missing ? null : item.value), smooth: .35, symbol: 'circle', symbolSize: 6, showSymbol: false, lineStyle: { width: 2.5, color: '#4168e8' }, itemStyle: { color: '#4168e8', borderColor: '#fff', borderWidth: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(65,104,232,.23)' }, { offset: 1, color: 'rgba(65,104,232,.015)' }] } } }],
  };
  const educationOption: EChartsOption = {
    tooltip: { ...chartTooltip, trigger: 'item', formatter: '{b}<br/>{c} 人 · {d}%' },
    series: [{ type: 'pie', radius: ['64%', '83%'], center: ['50%', '45%'], avoidLabelOverlap: true, label: { show: false }, itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 }, data: education.items.map((item, index) => ({ value: item.count, name: item.label, itemStyle: { color: ['#476ce5', '#7693ee', '#61b4ac', '#e2e7f1', '#a7b8ee'][index % 5] } })) }],
  };
  const columns: ColumnsType<Talent> = [
    { title: '人才', dataIndex: 'name', render: (_, r) => <div className="talent-cell"><PersonAvatar name={r.name}/><div><div className="talent-cell-name">{r.name}</div><div className="talent-cell-role">{r.current_position || '—'}</div></div></div> },
    { title: '所属部门', dataIndex: 'organization_name', render: (v) => v || '—' },
    { title: '核心技能', dataIndex: 'skill_tags', render: (v) => <SkillTags tags={v || []} max={2}/> },
    { title: '潜力', dataIndex: 'potential_level', width: 60, render: (v) => v ? <PotentialTag level={v}/> : '—' },
    { title: '综合评分', dataIndex: 'match_score', width: 85, render: (v) => <span className="score-text">{v ?? '—'}</span> },
    { title: '', width: 45, render: () => <Button type="text" size="small" icon={<ArrowRightOutlined/>} onClick={() => navigate('/talents')}/> },
  ];
  const exportReport = () => {
    const lines = [
      '智才云 TalentOS 人才驾驶舱报告',
      `生成时间：${dayjs().format('YYYY-MM-DD HH:mm')}`,
      `在职人才：${totalTalents || 0} 人`,
      `测评平均分：${summary?.average_score ?? '—'} 分`,
      `培训完成率：${trainingCompletion ?? '—'}%`,
      `学历分布：${education.items.map((item) => `${item.label} ${item.count}`).join('；') || '—'}`,
    ];
    downloadText(`${dayjs().format('YYYY')}年度人才驾驶舱报告.txt`, lines.join('\n')); message.success('驾驶舱报告已导出');
  };
  const greeting = dayjs().hour() < 12 ? '上午好' : dayjs().hour() < 18 ? '下午好' : '晚上好';

  return <Spin spinning={loading}>
    {error && <Alert type="warning" showIcon closable message="部分驾驶舱数据加载失败" description={error} style={{ marginBottom: 16 }}/>}
    <div className="welcome-row"><div><h1 className="welcome-title">{greeting}，{user?.real_name || user?.username || '用户'} <span style={{ fontSize: 20 }}>👋</span></h1><p className="welcome-sub">这里是您的人才工作简报，今天也一起发现组织的无限可能。</p></div><div className="flex items-center gap-12"><div className="date-display"><CalendarOutlined/>{dayjs().format('YYYY 年 M 月 D 日')}</div><Button icon={<DownloadOutlined/>} onClick={exportReport}>导出报告</Button></div></div>
    <div className="insight-strip"><div className="insight-icon"><ThunderboltOutlined/></div><div className="insight-content"><div className="insight-title">AI 组织洞察</div><div className="insight-text">{orgDistribution.items.length ? `当前授权范围内人才最多的组织为「${orgDistribution.items[0].label}」（${orgDistribution.items[0].count} 人）。可前往数据洞察查看完整分布与趋势。` : '暂无组织分布数据，请先完成分析库同步或检查后端接口。'}</div></div><Button className="insight-action" type="link" onClick={() => navigate('/analytics')}>查看完整分析 <ArrowRightOutlined/></Button></div>
    <div className="stats-grid">{statCards.map((s) => <div className="card stat-card" key={s.label}><div className="stat-top"><span className="stat-label">{s.label}</span><span className="stat-icon" style={{ color: s.color, background: s.bg }}>{s.icon}</span></div><div className="stat-value">{s.value}<small>{s.unit}</small></div><div className="stat-bottom"><span>{s.note}</span></div></div>)}</div>
    <div className="dashboard-grid">
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才规模趋势</h3><div className="section-card-subtitle">组织人才数量变化（后端月度存量口径）</div></div><Select size="small" value={metric} onChange={setMetric} options={[{ value: 'total', label: '人才总量' }]} style={{ width: 100 }}/></div>{trend.items.length ? <Chart option={trendOption} height={278}/> : <Empty description="暂无趋势数据" style={{ padding: 60 }}/>}</section>
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才学历结构</h3><div className="section-card-subtitle">在职人才学历分布</div></div><Button type="text" size="small" icon={<RobotOutlined/>}/></div>{education.items.length ? <><Chart option={educationOption} height={220}/><div className="structure-total"><strong>{formatNumber(education.items.reduce((acc, item) => acc + item.count, 0))}</strong><span>人才总数</span></div><div className="structure-legend">{education.items.map((item, index) => <div className="structure-legend-item" key={item.label}><span className="legend-dot" style={{ background: ['#476ce5', '#7693ee', '#61b4ac', '#e2e7f1', '#a7b8ee'][index % 5], marginRight: 7 }}/><span className="muted">{item.label}</span><b>{item.count}</b></div>)}</div></> : <Empty description="暂无学历分布数据" style={{ padding: 60 }}/>}</section>
    </div>
    <div className="bottom-grid">
      <section className="card table-card"><div className="card-header" style={{ marginBottom: 4 }}><div><h3 className="section-card-title">高潜人才关注</h3><div className="section-card-subtitle">基于最新人才档案列表</div></div><Button type="link" size="small" onClick={() => navigate('/talents')}>查看全部 <ArrowRightOutlined/></Button></div><Table rowKey="id" columns={columns} dataSource={talents} pagination={false} size="middle" locale={{ emptyText: '暂无人才数据' }}/></section>
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">培训计划进展</h3><div className="section-card-subtitle">后端培养计划完成率</div></div></div>{plans.length ? <div className="activity-list">{plans.slice(0, 4).map((plan) => <div className="activity-item" key={plan.id}><span className="activity-icon" style={{ background: '#edf2ff', color: '#4b6ddd' }}><BookOutlined/></span><div className="activity-body"><div className="activity-title">{plan.name}</div><div className="activity-meta"><Progress percent={Math.round((plan.completion_rate || 0) * 100)} size="small" style={{ width: 160 }}/></div></div></div>)}</div> : <Empty description="暂无培训计划" style={{ padding: 40 }}/>}</section>
    </div>
    <div className="bottom-grid mt-16">
      <section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">组织人才分布</h3><div className="section-card-subtitle">授权范围内组织人数</div></div><Button type="link" size="small" onClick={() => navigate('/organizations')}>组织架构 <ArrowRightOutlined/></Button></div>{orgDistribution.items.length ? <div className="flex gap-16" style={{ flexWrap: 'wrap' }}>{orgDistribution.items.slice(0, 4).map((item) => <div key={item.label} style={{ flex: 1, minWidth: 210 }}><div className="mini-task-top"><span>{item.label}</span><b>{item.count} 人</b></div><Progress percent={totalTalents ? Math.round(item.count / totalTalents * 100) : 0} showInfo={false} strokeColor="#4269df" trailColor="#edf0f5" size="small"/></div>)}</div> : <Empty description="暂无组织分布数据" style={{ padding: 30 }}/>}</section>
      <section className="card chart-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'linear-gradient(120deg,#f4f7ff,#fff)' }}><div><h3 className="section-card-title">需要更深入的洞察？</h3><div className="section-card-subtitle" style={{ marginTop: 8 }}>用自然语言，向 AI 询问任何人才问题</div></div><Button type="primary" icon={<RobotOutlined/>} onClick={() => navigate('/assistant')}>立即提问</Button></section>
    </div>
  </Spin>;
}
