import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Input, Modal, Select, Spin, Table, Tag } from 'antd';
import type { EChartsOption } from 'echarts';
import { BarChartOutlined, BulbOutlined, DownloadOutlined, FileTextOutlined, RobotOutlined, SafetyCertificateOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { Chart, PageHeader, baseGrid, chartAxis, chartTooltip } from '../components/Common';
import { analyticsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText } from '../utils';
import type { DistributionResult, NL2SQLResult, ReportItem } from '../types';

const emptyDistribution = (type: string): DistributionResult => ({ type, source: '', items: [], truncated: false, warnings: [] });

export default function AnalyticsPage() {
  const [question, setQuestion] = useState(''); const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NL2SQLResult | null>(null); const [org, setOrg] = useState<DistributionResult>(emptyDistribution('org'));
  const [age, setAge] = useState<DistributionResult>(emptyDistribution('age')); const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState(''); const [reportsOpen, setReportsOpen] = useState(false); const [reports, setReports] = useState<ReportItem[]>([]); const [downloadingId, setDownloadingId] = useState('');
  const { message } = App.useApp();

  const loadStats = useCallback(async () => {
    setStatsLoading(true); setStatsError('');
    try { const [o, a] = await Promise.all([analyticsApi.distribution('org'), analyticsApi.distribution('age')]); setOrg(o); setAge(a); }
    catch (error) { setStatsError(formatApiError(error)); }
    finally { setStatsLoading(false); }
  }, []);
  useEffect(() => { void loadStats(); }, [loadStats]);

  const ask = async () => {
    if (!question.trim()) return; setLoading(true);
    try { setResult(await analyticsApi.nl2sql({ question, max_rows: 200, with_insight: true })); }
    catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); }
  };
  const openReports = async () => {
    setReportsOpen(true);
    try { const res = await analyticsApi.reports({ page: 1, page_size: 20 }); setReports(res.items || []); }
    catch (error) { message.error(formatApiError(error)); }
  };
  const generateReport = async () => {
    try {
      const item = await analyticsApi.createReport({ question: question || '人才结构分析', title: question || '人才数据分析报告', format: 'pdf' });
      message.success(`报告已生成：${item.title}`); void openReports();
    } catch (error) { message.error(formatApiError(error)); }
  };
  const exportCurrent = () => {
    const lines = ['人才数据洞察导出', `生成时间：${dayjs().format('YYYY-MM-DD HH:mm')}`, `组织分布：${org.items.map((i) => `${i.label} ${i.count}`).join('；') || '—'}`, `年龄分布：${age.items.map((i) => `${i.label} ${i.count}`).join('；') || '—'}`];
    if (result) lines.push(`问数：${result.question}`, `SQL：${result.sql}`, `解读：${result.insight || '—'}`);
    downloadText('人才数据分析报告.txt', lines.join('\n')); message.success('当前真实分析结果已导出');
  };

  const total = org.items.reduce((acc, item) => acc + item.count, 0);
  const deptOption: EChartsOption = { tooltip: chartTooltip, grid: baseGrid, xAxis: { type: 'category', data: org.items.map((i) => i.label), ...chartAxis }, yAxis: { type: 'value', ...chartAxis, splitLine: { lineStyle: { color: '#edf0f4' } } }, series: [{ name: '人才总数', type: 'bar', data: org.items.map((i) => i.count), barWidth: 20, itemStyle: { color: '#5375e4', borderRadius: [4, 4, 0, 0] } }] };
  const ageOption: EChartsOption = { tooltip: chartTooltip, grid: { left: 70, right: 30, top: 20, bottom: 30 }, xAxis: { type: 'value', ...chartAxis, splitLine: { lineStyle: { color: '#edf0f4' } } }, yAxis: { type: 'category', data: age.items.map((i) => i.label), ...chartAxis }, series: [{ type: 'bar', data: age.items.map((i) => i.count), barWidth: 14, itemStyle: { color: '#69aaa5', borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', color: '#7b8798', fontSize: 10 } }] };
  const resultColumns = useMemo(() => (result?.columns || []).map((col) => ({ title: col, dataIndex: col, key: col, render: (v: unknown) => String(v ?? '—') })), [result]);
  const chartOption = result?.chart?.options as EChartsOption | undefined;

  return <>
    <PageHeader title="人才数据洞察" description="用自然语言自由问数，让数据自己说话，让每一次组织决策更有依据" actions={<><Button icon={<FileTextOutlined/>} onClick={openReports}>报告中心</Button><Button type="primary" icon={<DownloadOutlined/>} onClick={generateReport}>生成分析报告</Button></>}/>
    <section className="analytics-hero"><h2><RobotOutlined/> 您好，我是人才数据决策助手</h2><p>无需编写 SQL，直接描述您想了解的问题，AI 自动查询、出图并解读</p><div className="analytics-query"><Input value={question} onChange={(e) => setQuestion(e.target.value)} onPressEnter={ask} prefix={<ThunderboltOutlined style={{ color: '#5474db' }}/>} placeholder="例如：统计各部门人才数量与高潜人才占比，并分析梯队风险"/><Button type="primary" loading={loading} icon={<SendOutlined/>} onClick={ask}>开始分析</Button></div><div className="flex gap-8 mt-16" style={{ flexWrap: 'wrap', position: 'relative', zIndex: 1 }}>{['各部门高潜人才分布','近 12 个月人才存量趋势','技术序列技能短板分析'].map((q) => <Tag key={q} style={{ background: 'rgba(255,255,255,.1)', color: '#d6e0f5', borderColor: 'rgba(255,255,255,.16)', cursor: 'pointer' }} onClick={() => setQuestion(q)}>{q}</Tag>)}</div></section>
    {result && <section className="card chart-card mb-16"><div className="card-header"><div><h3 className="section-card-title"><BarChartOutlined className="text-primary"/> AI 问数结果</h3><div className="section-card-subtitle">{result.question} · 查询耗时 {result.elapsed_ms} ms {result.cached ? '· 缓存' : ''}</div></div><Tag icon={<SafetyCertificateOutlined/>} color="green">只读安全查询</Tag></div><div className="flex gap-16" style={{ flexWrap: 'wrap' }}><div style={{ flex: 1, minWidth: 450 }}>{chartOption ? <Chart option={chartOption} height={260}/> : <Table rowKey={(_, index) => String(index)} size="small" columns={resultColumns} dataSource={result.rows} pagination={{ pageSize: 5 }} scroll={{ x: 600 }}/>}</div><div className="portrait-summary" style={{ flex: 1, minWidth: 280 }}><b><BulbOutlined/> 智能解读</b><br/><br/>{result.insight || '后端未返回解读'}</div></div>{result.warnings?.length > 0 && <Alert type="warning" showIcon message={result.warnings.join('；')} style={{ marginTop: 12 }}/>}<details style={{ marginTop: 16, fontSize: 11, color: '#7c8798' }}><summary>查看安全查询语句</summary><pre className="audit-detail">{result.sql}</pre></details><div style={{ marginTop: 12 }}><Button size="small" onClick={exportCurrent}>导出当前结果</Button></div></section>}
    {statsError && <Alert type="warning" showIcon message="分布统计加载失败" description={statsError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">人才总量</div><div className="metric-strip-value">{total || '—'}<span className="metric-strip-extra">组织分布合计</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">组织分类数</div><div className="metric-strip-value">{org.items.length}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">年龄分类数</div><div className="metric-strip-value">{age.items.length}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">数据月份</div><div className="metric-strip-value">{org.month ? dayjs(org.month).format('YYYY-MM') : '最新'}<span className="metric-strip-extra">{org.source || '—'}</span></div></div></div>
    <Spin spinning={statsLoading}><div className="analytics-grid"><section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">各部门人才分布</h3><div className="section-card-subtitle">{org.month ? `截至 ${dayjs(org.month).format('YYYY 年 M 月')}` : '授权范围最新月份'}</div></div><Select size="small" value="all" options={[{ value: 'all', label: '全部部门' }]} /></div>{org.items.length ? <Chart option={deptOption} height={300}/> : <Empty description="暂无组织分布数据" style={{ padding: 60 }}/>}</section><section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才年龄结构</h3><div className="section-card-subtitle">后端年龄分组口径</div></div></div>{age.items.length ? <Chart option={ageOption} height={300}/> : <Empty description="暂无年龄分布数据" style={{ padding: 60 }}/>}</section></div></Spin>
    <section className="card content-card mt-16"><div className="table-toolbar"><div className="table-title">重点部门人才分布</div><Button type="link" size="small" onClick={() => void loadStats()}>刷新</Button></div><Table rowKey="label" dataSource={org.items} pagination={false} columns={[{ title: '部门', dataIndex: 'label' },{ title: '人才总数', dataIndex: 'count', render: (v) => `${v} 人` },{ title: '占比', render: (_, r) => total ? `${(r.count / total * 100).toFixed(1)}%` : '—' },{ title: '数据来源', render: () => org.source || '—' }]}/></section>
    <Modal title="分析报告中心" open={reportsOpen} onCancel={() => setReportsOpen(false)} footer={null} width={720}><Table rowKey="report_id" dataSource={reports} pagination={false} columns={[{ title: '标题', dataIndex: 'title' },{ title: '格式', dataIndex: 'format', width: 80 },{ title: '创建时间', dataIndex: 'created_at', width: 180 },{ title: '操作', width: 100, render: (_, r) => <Button type="link" size="small" loading={downloadingId === r.report_id} onClick={async () => { setDownloadingId(r.report_id); try { await analyticsApi.downloadReport(r); } catch (error) { message.error(formatApiError(error)); } finally { setDownloadingId(''); } }}>下载</Button> }]}/></Modal>
  </>;
}
