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
import { toChartOption } from '../utils/analyticsChart';
import './AnalyticsPage.css';

const emptyDistribution = (type: string): DistributionResult => ({ type, source: '', items: [], truncated: false, warnings: [] });

export default function AnalyticsPage() {
  const [question, setQuestion] = useState(''); const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<NL2SQLResult | null>(null); const [org, setOrg] = useState<DistributionResult>(emptyDistribution('org'));
  const [age, setAge] = useState<DistributionResult>(emptyDistribution('age')); const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState(''); const [reportsOpen, setReportsOpen] = useState(false); const [reports, setReports] = useState<ReportItem[]>([]);
  const [queryError, setQueryError] = useState(''); const [reportError, setReportError] = useState('');
  const [reportLoading, setReportLoading] = useState(false); const [reportListLoading, setReportListLoading] = useState(false);
  const [reportPage, setReportPage] = useState(1); const [reportTotal, setReportTotal] = useState(0);
  const [department, setDepartment] = useState<string>();
  const { message } = App.useApp();

  const loadStats = useCallback(async () => {
    setStatsLoading(true); setStatsError('');
    try {
      const [o, a] = await Promise.allSettled([analyticsApi.distribution('org'), analyticsApi.distribution('age')]);
      if (o.status === 'fulfilled') setOrg(o.value); else setOrg(emptyDistribution('org'));
      if (a.status === 'fulfilled') setAge(a.value); else setAge(emptyDistribution('age'));
      const errors = [o.status === 'rejected' ? `组织分布：${formatApiError(o.reason)}` : '', a.status === 'rejected' ? `年龄分布：${formatApiError(a.reason)}` : ''].filter(Boolean);
      setStatsError(errors.join('；'));
    } finally { setStatsLoading(false); }
  }, []);
  useEffect(() => { void loadStats(); }, [loadStats]);

  const ask = async () => {
    if (!question.trim()) { setQueryError('请输入要分析的问题'); return; }
    if (loading) return;
    setLoading(true); setQueryError(''); setResult(null);
    try { setResult(await analyticsApi.nl2sql({ question: question.trim(), max_rows: 200, with_insight: true })); }
    catch (e) { setQueryError(formatApiError(e)); } finally { setLoading(false); }
  };
  const loadReports = useCallback(async (page: number) => {
    setReportListLoading(true); setReportError('');
    try { const res = await analyticsApi.reports({ page, page_size: 10 }); setReports(res.items || []); setReportTotal(res.total); }
    catch (error) { setReports([]); setReportError(formatApiError(error)); }
    finally { setReportListLoading(false); }
  }, []);
  const openReports = () => { setReportsOpen(true); setReportPage(1); void loadReports(1); };
  const generateReport = () => {
    if (!result) { setQueryError('请先输入问题并完成分析，再生成报告'); return; }
    if (reportLoading) return;
    const generate = async () => {
      setReportLoading(true); setQueryError('');
      try {
        const source = result.query_id ? { query_id: result.query_id } : { question: result.question };
        const item = await analyticsApi.createReport({ ...source, title: result.question.slice(0, 100), format: 'pdf' });
        message.success(`报告已生成：${item.title}`); openReports();
      } catch (error) { setQueryError(`报告生成失败：${formatApiError(error)}`); }
      finally { setReportLoading(false); }
    };
    if (result.query_id) void generate();
    else Modal.confirm({ title: '无法复用当前查询结果', content: '当前结果没有可用快照，生成报告需要重新查询，数据和图表可能与页面不同。是否继续？', okText: '重新查询并生成', cancelText: '取消', onOk: generate });
  };
  const downloadReport = async (item: ReportItem) => {
    try { await analyticsApi.downloadReport(item); }
    catch (error) { setReportError(`下载失败：${formatApiError(error)}`); }
  };
  const exportCurrent = () => {
    const lines = ['人才数据洞察导出', `生成时间：${dayjs().format('YYYY-MM-DD HH:mm')}`, `组织分布：${org.items.map((i) => `${i.label} ${i.count}`).join('；') || '—'}`, `年龄分布：${age.items.map((i) => `${i.label} ${i.count}`).join('；') || '—'}`];
    if (result) lines.push(`问数：${result.question}`, `SQL：${result.sql}`, `解读：${result.insight || '—'}`, result.columns.join('\t'), ...result.rows.map(row => row.map(value => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)).join('\t')));
    downloadText('人才数据分析报告.txt', lines.join('\n')); message.success('当前真实分析结果已导出');
  };

  const visibleOrg = department === undefined ? org.items : org.items.filter(item => item.label === department);
  const total = org.items.reduce((acc, item) => acc + item.count, 0);
  const deptOption: EChartsOption = { tooltip: chartTooltip, grid: baseGrid, xAxis: { type: 'category', data: visibleOrg.map((i) => i.label), ...chartAxis }, yAxis: { type: 'value', ...chartAxis, splitLine: { lineStyle: { color: '#edf0f4' } } }, series: [{ name: '人才总数', type: 'bar', data: visibleOrg.map((i) => i.count), barWidth: 20, itemStyle: { color: '#5375e4', borderRadius: [4, 4, 0, 0] } }] };
  const ageOption: EChartsOption = { tooltip: chartTooltip, grid: { left: 70, right: 30, top: 20, bottom: 30 }, xAxis: { type: 'value', ...chartAxis, splitLine: { lineStyle: { color: '#edf0f4' } } }, yAxis: { type: 'category', data: age.items.map((i) => i.label), ...chartAxis }, series: [{ type: 'bar', data: age.items.map((i) => i.count), barWidth: 14, itemStyle: { color: '#69aaa5', borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', color: '#7b8798', fontSize: 10 } }] };
  const resultColumns = useMemo(() => (result?.columns || []).map((col, index) => ({ title: col, key: String(index), render: (_: unknown, row: unknown[]) => typeof row[index] === 'object' && row[index] !== null ? JSON.stringify(row[index]) : String(row[index] ?? '—') })), [result]);
  const chartOption = toChartOption(result?.chart);

  return <div className="analytics-page">
    <PageHeader title="人才数据洞察" description="用自然语言自由问数，让数据自己说话，让每一次组织决策更有依据" actions={<><Button icon={<FileTextOutlined/>} onClick={openReports}>报告中心</Button><Button type="primary" icon={<DownloadOutlined/>} loading={reportLoading} onClick={generateReport}>生成分析报告</Button></>}/>
    <section className="analytics-hero"><h2><RobotOutlined/> 您好，我是人才数据决策助手</h2><p>无需编写 SQL，直接描述您想了解的问题，AI 自动查询、出图并解读</p><div className="analytics-query"><Input value={question} onChange={(e) => { setQuestion(e.target.value); setQueryError(''); }} onPressEnter={ask} prefix={<ThunderboltOutlined style={{ color: '#5474db' }}/>} placeholder="例如：统计各部门人才数量与高潜人才占比，并分析梯队风险"/><Button type="primary" loading={loading} icon={<SendOutlined/>} onClick={ask}>开始分析</Button></div><div className="flex gap-8 mt-16" style={{ flexWrap: 'wrap', position: 'relative', zIndex: 1 }}>{['各部门高潜人才分布','近 12 个月人才存量趋势','技术序列技能短板分析'].map((q) => <Tag key={q} style={{ background: 'rgba(255,255,255,.1)', color: '#d6e0f5', borderColor: 'rgba(255,255,255,.16)', cursor: 'pointer' }} onClick={() => { setQuestion(q); setQueryError(''); }}>{q}</Tag>)}</div></section>
    {queryError && <Alert type="error" showIcon message="操作未完成" description={queryError} closable onClose={() => setQueryError('')} style={{ marginBottom: 16 }}/> }
    {result && <section className="card chart-card mb-16"><div className="card-header"><div><h3 className="section-card-title"><BarChartOutlined className="text-primary"/> AI 问数结果</h3><div className="section-card-subtitle">{result.question} · 查询耗时 {result.elapsed_ms} ms {result.cached ? '· 缓存' : ''}</div></div><Tag icon={<SafetyCertificateOutlined/>} color="green">只读安全查询</Tag></div><div className="flex gap-16" style={{ flexWrap: 'wrap' }}><div style={{ flex: 1, minWidth: 0, width: '100%' }}>{chartOption && <Chart option={chartOption} height={260}/>}<Table rowKey={(_, index) => String(index)} size="small" columns={resultColumns} dataSource={result.rows} pagination={{ pageSize: 5, hideOnSinglePage: true }} scroll={{ x: 'max-content' }}/></div><div className="portrait-summary" style={{ flex: 1, minWidth: 0 }}><b><BulbOutlined/> 智能解读</b><br/><br/>{result.insight || '后端未返回解读'}</div></div>{result.warnings?.length > 0 && <Alert type="warning" showIcon message={result.warnings.join('；')} style={{ marginTop: 12 }}/>}<details style={{ marginTop: 16, fontSize: 11, color: '#7c8798' }}><summary>查看安全查询语句</summary><pre className="audit-detail">{result.sql}</pre></details><div style={{ marginTop: 12 }}><Button size="small" onClick={exportCurrent}>导出当前结果</Button></div></section>}
    {(org.warnings?.length > 0 || age.warnings?.length > 0 || org.truncated || age.truncated) && <Alert type="warning" showIcon message={[...org.warnings, ...age.warnings, ...(org.truncated || age.truncated ? ['分类结果已截断，统计可能不完整'] : [])].join('；')} style={{ marginBottom: 16 }}/> }
    {statsError && <Alert type="warning" showIcon message="分布统计加载失败" description={statsError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">人才总量</div><div className="metric-strip-value">{!org.source && statsError ? '—' : total}<span className="metric-strip-extra">组织分布合计</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">组织分类数</div><div className="metric-strip-value">{org.items.length}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">年龄分类数</div><div className="metric-strip-value">{age.items.length}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">数据月份</div><div className="metric-strip-value">{org.month ? <span className="analytics-month">{dayjs(org.month).format('YYYY-MM')}</span> : '最新'}<span className="metric-strip-extra">{org.source || '—'}</span></div></div></div>
    <Spin spinning={statsLoading}><div className="analytics-grid"><section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">各部门人才分布</h3><div className="section-card-subtitle">{org.month ? `截至 ${dayjs(org.month).format('YYYY 年 M 月')}` : '授权范围最新月份'}</div></div><Select size="small" value={department} placeholder="全部部门" allowClear onChange={setDepartment} style={{ minWidth: 120, maxWidth: 200 }} options={org.items.map(item => ({ value: item.label, label: item.label }))} /></div>{visibleOrg.length ? <Chart option={deptOption} height={300}/> : <Empty description="暂无组织分布数据" style={{ padding: 60 }}/>}</section><section className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">人才年龄结构</h3><div className="section-card-subtitle">后端年龄分组口径</div></div></div>{age.items.length ? <Chart option={ageOption} height={300}/> : <Empty description="暂无年龄分布数据" style={{ padding: 60 }}/>}</section></div></Spin>
    <section className="card content-card mt-16"><div className="table-toolbar"><div className="table-title">重点部门人才分布</div><Button type="link" size="small" onClick={() => void loadStats()}>刷新</Button></div><Table rowKey="label" scroll={{ x: 560 }} dataSource={visibleOrg} pagination={{ pageSize: 10, hideOnSinglePage: true }} columns={[{ title: '部门', dataIndex: 'label' },{ title: '人才总数', dataIndex: 'count', render: (v) => `${v} 人` },{ title: '占比', render: (_, r) => total ? `${(r.count / total * 100).toFixed(1)}%` : '—' },{ title: '数据来源', render: () => org.source || '—' }]}/></section>
    <Modal title="分析报告中心" open={reportsOpen} onCancel={() => setReportsOpen(false)} footer={null} width="min(720px, calc(100vw - 32px))">
      {reportError && <Alert type="error" showIcon message={reportError} style={{ marginBottom: 12 }} />}
      <Table rowKey="report_id" dataSource={reports} loading={reportListLoading} scroll={{ x: 550 }} pagination={{ current: reportPage, pageSize: 10, total: reportTotal, showSizeChanger: false, onChange: p => { setReportPage(p); void loadReports(p); } }} columns={[{ title: '标题', dataIndex: 'title' },{ title: '格式', dataIndex: 'format', width: 80 },{ title: '创建时间', dataIndex: 'created_at', width: 160, render: v => dayjs(v).format('YYYY-MM-DD HH:mm') },{ title: '操作', width: 100, render: (_, r) => <Button type="link" size="small" onClick={() => void downloadReport(r)}>下载</Button> }]}/>
    </Modal>
  </div>;
}
