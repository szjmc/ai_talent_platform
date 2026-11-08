import { useState } from 'react';
import { Alert, App, Button, Empty, Input, Space, Spin, Tabs, Tag } from 'antd';
import { BarChartOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons';
import { Chart, PageHeader } from '../components/Common';
import { agentsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { toChartOption } from '../utils/analyticsChart';
import type { DecisionReport } from '../types';
import AnalyticsQueryPage from './AnalyticsQueryPage';
import './DecisionReportPage.css';

type Period = 'weekly' | 'monthly' | 'yearly';
const periodNames: Record<Period, string> = { weekly: '周报', monthly: '月报', yearly: '年报' };

export default function AnalyticsPage() {
  const [view, setView] = useState('single');
  const [question, setQuestion] = useState('帮我生成人才决策报告');
  const [period, setPeriod] = useState<Period>('weekly');
  const [report, setReport] = useState<DecisionReport | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [reportKind, setReportKind] = useState('');
  const { message } = App.useApp();
  const generate = async (kind: 'single' | Period, force = false) => {
    if (busy) return;
    if (kind === 'single' && !question.trim()) { setError('请输入分析需求'); return; }
    setBusy(true); setError(''); setReport(null);
    try {
      const data = kind === 'single' ? await agentsApi.decisionReport(question.trim()) : await agentsApi.periodReport(kind, force);
      if (!data.answered) { setError(data.message || data.answer || '当前范围没有可用于报告的数据，请检查数据源及权限。'); return; }
      setReport(data); setReportKind(kind === 'single' ? '单次分析报告' : periodNames[kind]);
      if (kind !== 'single') setPeriod(kind);
    } catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const list = (items?: string[]) => items?.length ? <ul className="decision-list">{items.map((item, i) => <li key={i}>{item}</li>)}</ul> : <div className="decision-muted">暂无可用内容</div>;
  return <div className="decision-page">
    <PageHeader title="人才数据洞察" description="单次人才决策分析与周期报告，基于授权范围内的真实数据" />
    <Tabs activeKey={view} onChange={key => { if (busy) return; setView(key); setError(''); setReport(null); }} items={[
      { key: 'single', label: '单次分析报告' }, { key: 'period', label: '周报 / 月报 / 年报' }, { key: 'query', label: '数据问数' },
    ]} />
    {view === 'query' ? <AnalyticsQueryPage /> : <>
      <div className="card decision-controls">
        {view === 'single' ? <><Input aria-label="分析需求" value={question} maxLength={500} onChange={e => setQuestion(e.target.value)} onPressEnter={() => void generate('single')} placeholder="输入人才决策分析需求"/><Button type="primary" icon={<FileTextOutlined />} loading={busy} onClick={() => void generate('single')}>生成报告</Button></> : <><Space wrap>{(['weekly', 'monthly', 'yearly'] as Period[]).map(p => <Button key={p} type={period === p ? 'primary' : 'default'} disabled={busy} onClick={() => { setPeriod(p); void generate(p); }}>生成{periodNames[p]}</Button>)}<Button icon={<ReloadOutlined />} loading={busy} onClick={() => void generate(period, true)}>强制重新计算</Button></Space><span className="decision-muted">周期报告使用固定模板，可能读取缓存</span></>}
      </div>
      {error && <Alert type="error" showIcon message="报告生成失败" description={error} style={{ marginBottom: 16 }} />}
      <Spin spinning={busy} tip="正在查询与分析，请稍候…">
        {!report ? <div className="card decision-placeholder"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择报告类型并生成，结果将在这里展示" /></div> : <>
          <div className="decision-status"><Tag color="blue">{reportKind}</Tag>{report.period_label && <Tag>{report.period_label}</Tag>}{reportKind !== '单次分析报告' && <Tag>{report.from_cache ? '缓存结果' : '本次生成'}</Tag>}{report.period_updated_at && <span>更新时间：{report.period_updated_at}</span>}</div>
          {report.warnings?.length ? <Alert type="warning" showIcon message={report.warnings.join('；')} style={{ marginBottom: 16 }}/> : null}
          <div className="decision-layout">
            <section className="card decision-pane"><div className="decision-pane-heading"><h3>① 结论 · 风险 · 待决策事项</h3><p>管理者优先阅读 · 文字判断与行动项</p></div>
              <div className="decision-section"><h4>报告元信息</h4>{report.meta && Object.keys(report.meta).length ? Object.entries(report.meta).map(([key, value]) => <div className="decision-meta" key={key}><span>{({ report_purpose: '报告用途', data_cutoff: '数据截止', data_scope: '数据范围', sample_tip: '样本提示', threshold_desc: '阈值说明', kpi_goal: '目标' } as Record<string,string>)[key] || key}</span><b>{typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—')}</b></div>) : <div className="decision-muted">暂无报告元信息</div>}</div>
              <div className="decision-section"><h4>核心结论</h4>{list(report.conclusions)}{!report.conclusions?.length && report.answer && <p>{report.answer}</p>}</div>
              <div className="decision-section"><h4>风险清单</h4>{report.risk_list?.length ? report.risk_list.map((r, i) => <div className="decision-risk" key={i}><b>{r.priority && <Tag color="orange">{r.priority}</Tag>}{r.name || '风险'}</b><p>{r.phenomenon || '暂无描述'}</p>{r.business_impact && <p>影响：{r.business_impact}</p>}{r.drill_hint && <p>下钻：{r.drill_hint}</p>}</div>) : report.risks?.length ? report.risks.map((r, i) => <div className="decision-risk" key={i}>{Object.entries(r).map(([k, v]) => <div key={k}>{k}：{typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—')}</div>)}</div>) : <div className="decision-muted">暂无风险清单</div>}</div>
              <div className="decision-section"><h4>建议行动项</h4><b>P0 · 优先行动</b>{list(report.suggestion_p0)}<b>P1 · 后续行动</b>{list(report.suggestion_p1)}{report.warn_if_no_action && <Alert type="warning" message={report.warn_if_no_action} />}</div>
              <div className="decision-section"><h4>重点人才</h4>{list(report.key_talent_top3)}</div>
              {report.missing?.length ? <Alert type="info" showIcon message="以下维度暂无可用数据" description={report.missing.join('；')} /> : null}
            </section>
            <section className="card decision-pane"><div className="decision-pane-heading"><h3>② 数据图表大盘</h3><p>用于佐证判断 · 仅展示后端返回的真实图表</p></div><div className="decision-chart-grid">{report.charts?.length ? report.charts.map((chart, i) => { const option = toChartOption(chart); return <div className="decision-chart" key={`${chart.title}-${i}`}><h4><BarChartOutlined /> {chart.title}</h4>{option ? <Chart option={option} height={240}/> : <div className="decision-muted">该图表类型暂不支持</div>}</div>; }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前报告没有可展示的图表" />}</div></section>
          </div>
        </>}
      </Spin>
    </>}
    <div className="decision-note">报告依赖已配置的只读分析库、问数服务和模型。无数据或无权限时不会生成虚构结论。<Button type="link" size="small" onClick={() => message.info('请联系管理员检查分析库、Vanna 和模型服务配置')}>需要帮助？</Button></div>
  </div>;
}
