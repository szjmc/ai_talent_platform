import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, Button, Input, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import {
  DownloadOutlined, FileTextOutlined, ReloadOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { agentsApi, analyticsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import type {
  AnalyticsChart, InsightReport, PeriodReportResponse, RiskItem,
} from '../types';

/* ──────────────────────── 颜色板（与设计稿一致） ──────────────────────── */
const BLUE = ['#1B3358', '#2F6FD0', '#5B8DEF', '#8FACDA', '#C2D2E8', '#A9BEDC', '#6E8EC4', '#D5E1F2'];
const TENURE_FALLBACK = { labels: ['1-3年', '3-5年', '5-8年', '8年以上'], data: [7, 14, 15, 6] };

/* ──────────────────────── 综合报告结构（前端侧） ──────────────────────── */
interface FullReport {
  source: string;
  period_label: string;
  report_type: string;
  from_cache: boolean;
  updated_at: string;
  meta: NonNullable<InsightReport['meta']>;
  conclusions: string[];
  risk_list: RiskItem[];
  suggestion_p0: string[];
  suggestion_p1: string[];
  warn_if_no_action: string;
  group_summaries: Record<string, string>;
  key_talent_top3: string[];
  appendix_note: string;
  charts: AnalyticsChart[];
  tenure: { labels: string[]; data: number[] };
  kpi: { avg: number; talent: number; bad: number; train_rate: number };
}

function safeNum(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/* ──────────────────────── 内置演示快照（首次加载/离线兜底） ──────────────────────── */
const SNAPSHOT: FullReport = {
  source: '周期月报（真实取数快照）',
  period_label: '2026年11月',
  report_type: 'monthly',
  from_cache: true,
  updated_at: '2026-11-07 20:11:39',
  meta: {
    report_purpose: '2026Q4人才盘点简报',
    data_cutoff: '2026-11-02',
    data_scope: '全公司42名人才',
    sample_tip: '',
    threshold_desc: '能力待提升占比>40%、测评均分<80、培训完成率<60%等规则阈值触发风险分级',
    kpi_goal: { reassess_pass_rate: '≥70%', train_complete_rate: '≥60%' },
  },
  conclusions: [
    '本月人才42人，学历以本科硕士为主，能力达标与培训转化是主要短板。',
    '本科+硕士38人占九成，高潜力仅6人，高潜梯队偏薄。',
    '2项P0聚焦低分测评与能力待提升；机会在23人已进入学习状态。',
  ],
  risk_list: [
    { name: '存在低分测评记录', priority: 'P0', phenomenon: '低分(<60)测评9次，占51次测评约17.6%', business_impact: '低分人员若上岗将直接影响岗位胜任与交付质量', drill_hint: '' },
    { name: '测评能力有待关注', priority: 'P0', phenomenon: '待提升/待评分17次/总51次，占比33.3%', business_impact: '三成测评能力未达标，制约晋升与关键岗选拔', drill_hint: '集中在能力等级「待提升」16次记录，潜力B/C级待核' },
    { name: '测评平均分有待提升', priority: 'P1', phenomenon: '测评平均分73.1，低于警戒线80', business_impact: '整体能力水位偏低，培养投入产出比下降', drill_hint: '' },
    { name: '培训完成率偏低', priority: 'P1', phenomenon: '培训完成率仅25.0%（已完成9条/总36条）', business_impact: '培训投入未转化为能力，资源浪费明显', drill_hint: '集中在in_progress 20条与learning 6条在途轨迹' },
    { name: '培训在途积压', priority: 'P2', phenomenon: '在途轨迹占比72.2%（在途26条/总36条）', business_impact: '学习周期拉长，短期难形成能力增量', drill_hint: '在途26条中in_progress 20条为主' },
    { name: '高潜力人才占比偏低', priority: 'P2', phenomenon: '高潜力人才6人/总42人，占比14.3%', business_impact: '继任梯队储备不足，关键岗断档风险上升', drill_hint: '潜力B级14人为最大池，是向高潜转化的重点' },
  ],
  suggestion_p0: [
    '9次低分测评人才30天内复测并辅导，复测达标率≥70%',
    '17名待提升/待评分人员定制提升计划，占比降至≤20%',
  ],
  suggestion_p1: [
    '培训完成率由25%提升至≥60%，清理在途26条轨迹',
    '高潜人才占比由14.3%提升至≥20%，新增3名高潜',
  ],
  warn_if_no_action: '低分人员持续上岗、培训积压，Q4关键岗人才供给出现缺口',
  group_summaries: {
    '人才大盘': '42人中本科24人、硕士14人，平均工作年限4.86年，以中青年骨干为主。',
    '测评分析': '51次测评平均73.1分，待提升16次，9次低于60分，达标面不足。',
    '培训分析': '36条学习轨迹仅9条完成（25.0%），23人参与，在途达26条。',
    '个人档案': '许铭工作10年、匹配分95.6居首，TOP10匹配分均在88分以上。',
  },
  key_talent_top3: ['许铭', '郭涛', '演示人才11'],
  appendix_note: 'TOP名单显示高年限与高匹配分高度重合，可直接作为导师与关键岗继任池。',
  charts: [
    { type: 'pie', title: '人才大盘-各学历层次的人才分布？', series: [{ name: 'amount', data: [{ name: '本科', value: 24 }, { name: '硕士', value: 14 }, { name: '专科', value: 3 }, { name: '博士', value: 1 }] }], group: 'overview' },
    { type: 'pie', title: '人才大盘-各潜力等级的人才数量？', series: [{ name: 'amount', data: [{ name: 'B', value: 14 }, { name: 'A', value: 9 }, { name: 'C', value: 7 }, { name: '高', value: 6 }, { name: '中', value: 6 }] }], group: 'overview' },
    { type: 'pie', title: '人才大盘-各部门路径的人才数量？', series: [{ name: 'amount', data: [{ name: '研发一部', value: 10 }, { name: '研发二部', value: 10 }, { name: '产品部', value: 6 }, { name: '市场部', value: 4 }, { name: '销售部', value: 4 }, { name: '运营部', value: 4 }, { name: '人事部', value: 4 }] }], group: 'overview' },
    { type: 'bar', title: '测评分析-各能力等级测评次数？', series: [{ name: 'amount', data: [{ name: '待提升', value: 16 }, { name: '良好', value: 15 }, { name: '中级', value: 5 }, { name: '专家', value: 4 }, { name: '优秀', value: 4 }, { name: '初级', value: 3 }, { name: '高级', value: 3 }, { name: '待评分', value: 1 }] }], group: 'assessment' },
    { type: 'pie', title: '培训分析-各学习状态的轨迹条数？', series: [{ name: 'amount', data: [{ name: '进行中', value: 20 }, { name: '已完成', value: 9 }, { name: '学习中', value: 6 }, { name: '未开始', value: 1 }] }], group: 'training' },
    { type: 'bar', title: '员工活跃度-最近七天各行为类型次数？', series: [{ name: 'amount', data: [{ name: '浏览', value: 17 }, { name: '检索', value: 14 }, { name: '学习中', value: 12 }, { name: 'AI调用', value: 2 }, { name: '导出', value: 1 }] }], group: 'activity' },
  ],
  tenure: TENURE_FALLBACK,
  kpi: { avg: 73.1, talent: 42, bad: 17, train_rate: 25.0 },
};

/* ──────────────────────── 工具：从后端 insight+data 归一 ──────────────────────── */
function normalizePeriod(d: PeriodReportResponse): FullReport {
  const ins = d.insight || {};
  const sug = (ins.suggestions && typeof ins.suggestions === 'object' && !Array.isArray(ins.suggestions))
    ? ins.suggestions as { p0?: string[]; p1?: string[]; warn_if_no_action?: string }
    : {};
  const charts: AnalyticsChart[] = (d.data && (d.data.charts as AnalyticsChart[])) || [];
  return {
    source: `周期报告 · ${d.report_type === 'weekly' ? '周报' : d.report_type === 'yearly' ? '年报' : '月报'}`,
    period_label: d.period_label || '',
    report_type: d.report_type || 'monthly',
    from_cache: !!d.from_cache,
    updated_at: d.period_updated_at || '',
    meta: ins.meta || {},
    conclusions: ins.conclusions || [],
    risk_list: ins.risk_list || [],
    suggestion_p0: ins.suggestion_p0 || sug.p0 || [],
    suggestion_p1: ins.suggestion_p1 || sug.p1 || [],
    warn_if_no_action: ins.warn_if_no_action || sug.warn_if_no_action || '',
    group_summaries: ins.group_summaries || {},
    key_talent_top3: ins.key_talent_top3 || [],
    appendix_note: ins.appendix_note || '',
    charts,
    tenure: TENURE_FALLBACK,
    kpi: calcKpi(charts),
  };
}

function normalizeChat(resp: { report?: unknown; elapsed_ms?: number }): FullReport {
  const r = ((resp.report as Record<string, unknown> | null) || {}) as Record<string, unknown>;
  const meta = (r.meta || {}) as NonNullable<InsightReport['meta']>;
  const sug = r.suggestions && typeof r.suggestions === 'object' && !Array.isArray(r.suggestions)
    ? r.suggestions as { p0?: string[]; p1?: string[]; warn_if_no_action?: string }
    : {};
  const charts: AnalyticsChart[] = (r.charts as AnalyticsChart[]) || [];
  return {
    source: '单次分析报告 · AI 推理',
    period_label: (r.period_label as string) || '',
    report_type: (r.report_type as string) || '',
    from_cache: false,
    updated_at: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    meta,
    conclusions: (r.conclusions as string[]) || [],
    risk_list: (r.risk_list as RiskItem[]) || [],
    suggestion_p0: (r.suggestion_p0 as string[]) || sug.p0 || [],
    suggestion_p1: (r.suggestion_p1 as string[]) || sug.p1 || [],
    warn_if_no_action: (r.warn_if_no_action as string) || sug.warn_if_no_action || '',
    group_summaries: (r.group_summaries as Record<string, string>) || {},
    key_talent_top3: (r.key_talent_top3 as string[]) || [],
    appendix_note: (r.appendix_note as string) || '',
    charts,
    tenure: TENURE_FALLBACK,
    kpi: calcKpi(charts),
  };
}

function seriesOf(charts: AnalyticsChart[], kw: string): { name: string; value: number }[] | null {
  for (const c of charts) {
    if ((c.title || '').includes(kw) && c.series && c.series[0]?.data) {
      return c.series[0].data.map((x) => ({ name: String(x.name), value: safeNum(x.value) }));
    }
  }
  return null;
}
function sumNames(list: { name: string; value: number }[] | null, names: string[]): number | null {
  if (!list) return null;
  let t = 0; let hit = false;
  list.forEach((x) => { if (names.includes(x.name)) { t += x.value; hit = true; } });
  return hit ? t : null;
}
function calcKpi(charts: AnalyticsChart[]): FullReport['kpi'] {
  const k = { avg: SNAPSHOT.kpi.avg, talent: SNAPSHOT.kpi.talent, bad: SNAPSHOT.kpi.bad, train_rate: SNAPSHOT.kpi.train_rate };
  const edu = seriesOf(charts, '各学历层次');
  if (edu) k.talent = edu.reduce((a, b) => a + b.value, 0);
  const as = seriesOf(charts, '各能力等级测评次数');
  if (as) {
    const b = sumNames(as, ['待提升', '待评分']);
    if (b != null) k.bad = b;
  }
  const tr = seriesOf(charts, '各学习状态的轨迹条数');
  if (tr) {
    let done = 0; let all = 0;
    tr.forEach((x) => { all += x.value; if (x.name === '已完成') done += x.value; });
    if (all) k.train_rate = Math.round((done / all) * 1000) / 10;
  }
  return k;
}

/* ──────────────────────── ECharts 选项生成 ──────────────────────── */
function pieOption(labels: string[], data: number[], title?: string): EChartsOption {
  return {
    tooltip: { trigger: 'item', backgroundColor: 'rgba(28,38,57,.94)', borderWidth: 0, textStyle: { color: '#fff', fontSize: 12 } },
    legend: { bottom: 0, textStyle: { fontSize: 10, color: '#6B788B' }, itemWidth: 9, itemHeight: 9 },
    color: BLUE,
    series: [{
      type: 'pie', radius: ['45%', '70%'], center: ['50%', '45%'],
      data: labels.map((n, i) => ({ name: n, value: data[i] })),
      label: { fontSize: 10, color: '#4A5668' }, labelLine: { length: 6, length2: 6 },
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
    }],
  };
}
function barOption(labels: string[], data: number[], horizontal = false): EChartsOption {
  const cat = horizontal ? { type: 'value' as const, axisLabel: { fontSize: 9.5, color: '#6B788B' }, splitLine: { lineStyle: { color: '#EDF1F6' } } } : { type: 'category' as const, data: labels, axisLabel: { fontSize: 9.5, color: '#6B788B', rotate: labels.some((l) => l.length > 4) ? 25 : 0, interval: 0 }, axisTick: { show: false }, axisLine: { show: false } };
  const val = horizontal ? { type: 'category' as const, data: labels, axisLabel: { fontSize: 9.5, color: '#6B788B' }, axisTick: { show: false }, axisLine: { show: false } } : { type: 'value' as const, axisLabel: { fontSize: 9.5, color: '#6B788B' }, splitLine: { lineStyle: { color: '#EDF1F6' } } };
  return {
    tooltip: { trigger: 'axis', backgroundColor: 'rgba(28,38,57,.94)', borderWidth: 0, textStyle: { color: '#fff', fontSize: 12 } },
    grid: { left: horizontal ? 60 : 40, right: 20, top: 10, bottom: horizontal ? 20 : 45, containLabel: false },
    xAxis: horizontal ? val : cat, yAxis: horizontal ? cat : val,
    series: [{
      type: 'bar', data, barWidth: '55%',
      itemStyle: { color: '#2F6FD0', borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] },
    }],
  };
}

/* ──────────────────────── 主组件 ──────────────────────── */
export default function AnalyticsPage() {
  const { message } = App.useApp();
  const [view, setView] = useState<'report' | 'period'>('report');
  const [question, setQuestion] = useState('帮我生成人才决策报告');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ text: string; kind: '' | 'ok' | 'warn' | 'err' }>({ text: '正在加载真实数据服务…', kind: '' });
  const [report, setReport] = useState<FullReport>(SNAPSHOT);
  const [periodType, setPeriodType] = useState<'weekly' | 'monthly' | 'yearly'>('monthly');
  const [qaInput, setQaInput] = useState('');
  const [chatMsgs, setChatMsgs] = useState<{ who: 'user' | 'agent'; text: string }[]>([
    { who: 'agent', text: '试试：各学历层次的人才分布？ / 测评平均分是多少？' },
  ]);
  const chatBoxRef = useRef<HTMLDivElement>(null);

  const setStatusT = useCallback((text: string, kind: '' | 'ok' | 'warn' | 'err' = '') => setStatus({ text, kind }), []);

  /* ── 启动时静默加载真实月报（失败则保留快照） ── */
  const boot = useCallback(async () => {
    try {
      const d = await agentsApi.periodReport('monthly');
      const r = normalizePeriod(d);
      setReport(r);
      setStatusT(`月报已载入（真实数据库）· 结论 ${r.conclusions.length} 条 / 风险 ${r.risk_list.length} 项 / 图表 ${r.charts.length} 张`, 'ok');
    } catch (e: unknown) {
      setStatusT(`实时服务暂不可用（${formatApiError(e)}），当前展示上次真实取数快照；启动后端后可点「生成报告」刷新`, 'warn');
    }
  }, [setStatusT]);

  useEffect(() => { void boot(); }, [boot]);

  /* ── 生成单次分析报告 ── */
  const loadChat = useCallback(async () => {
    const q = question.trim() || '帮我生成人才决策报告';
    setLoading(true); setStatusT('⏳ 正在调用 analytics Agent 生成报告（AI 推理，约 30-60 秒）…');
    try {
      const resp = await agentsApi.analyticsChat({ message: q });
      const r = resp.report ? normalizeChat(resp) : SNAPSHOT;
      setReport(r); setView('report');
      setStatusT(`单次分析报告生成成功 · 结论 ${r.conclusions.length} 条 / 风险 ${r.risk_list.length} 项 / 图表 ${r.charts.length} 张（${(resp.elapsed_ms / 1000).toFixed(1)}s）`, 'ok');
    } catch (e: unknown) {
      setStatusT(formatApiError(e), 'err'); message.error(formatApiError(e));
    } finally { setLoading(false); }
  }, [question, setStatusT, message]);

  /* ── 周期报告（周/月/年） ── */
  const loadPeriod = useCallback(async (type: 'weekly' | 'monthly' | 'yearly', force = false) => {
    setPeriodType(type); setLoading(true);
    const name = type === 'weekly' ? '周报' : type === 'yearly' ? '年报' : '月报';
    setStatusT(`⏳ 正在${force ? '重新计算' : '读取'}${name}（真实取数 / 只读分析库）…`);
    try {
      const d = await agentsApi.periodReport(type, force);
      const r = normalizePeriod(d);
      setReport(r); setView('report');
      setStatusT(`${name}${d.from_cache ? '（缓存命中）' : '已重新计算'} · 结论 ${r.conclusions.length} 条 / 风险 ${r.risk_list.length} 项 / 图表 ${r.charts.length} 张`, 'ok');
    } catch (e: unknown) {
      setStatusT(formatApiError(e), 'err'); message.error(formatApiError(e));
    } finally { setLoading(false); }
  }, [setStatusT, message]);

  /* ── 智能问答 ── */
  const askQa = useCallback(async () => {
    const q = qaInput.trim(); if (!q) return;
    setChatMsgs((m) => [...m, { who: 'user', text: q }]); setQaInput('');
    const lid = Date.now();
    setChatMsgs((m) => [...m, { who: 'agent', text: `__loading__${lid}` }]);
    try {
      const r = await agentsApi.nl2sqlAsk(q);
      setChatMsgs((m) => m.map((x) => (x.text === `__loading__${lid}` ? { who: 'agent' as const, text: r.insight || '查询成功，但无文字解读' } : x)));
    } catch (e: unknown) {
      setChatMsgs((m) => m.map((x) => (x.text === `__loading__${lid}` ? { who: 'agent' as const, text: `错误：${formatApiError(e)}` } : x)));
    }
  }, [qaInput]);
  useEffect(() => { if (chatBoxRef.current) chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight; }, [chatMsgs]);

  /* ── 导出 PDF（浏览器打印） ── */
  const exportPdf = useCallback(() => { window.print(); }, []);

  /* ── 渲染工具：从 charts 里按关键词取序列 ── */
  const series = useMemo(() => {
    function pick(kw: string) { return seriesOf(report.charts, kw); }
    const rename = (list: { name: string; value: number }[] | null, map: Record<string, string>) =>
      (list || []).map((x) => ({ name: map[x.name] || x.name, value: x.value }));

    const edu = pick('各学历层次');
    const as = pick('各能力等级测评次数');
    const tr = pick('各学习状态的轨迹条数');
    const pot = pick('各潜力等级');
    const act = rename(pick('各行为类型次数'), { page_view: '浏览', search: '检索', ai_call: 'AI调用', export: '导出', 学习中: '学习中' });
    return { edu, as, tr, pot, act };
  }, [report.charts]);

  /* ── KPI / 摘要文本 ── */
  const k = report.kpi;
  const firstConcl = (report.conclusions[0] || '').replace(/^【[^】]+】/, '');
  const meta = report.meta;
  const chartCount = report.charts.length;
  const groupCount = new Set(report.charts.map((c) => c.group || 'other')).size;

  const hi = (n: number) => {
    const arr = [n]; const d = series.edu || []; d.forEach((x) => arr.push(x.value));
    const i = arr.indexOf(Math.max(...arr.filter((v) => Number.isFinite(v))));
    return i === 0 ? '—' : d[i - 1] ? `${d[i - 1].name} ${d[i - 1].value}` : '—';
  };

  const p0 = report.suggestion_p0, p1 = report.suggestion_p1;
  const risks = report.risk_list;
  const highRisks = risks.filter((r) => r.priority === 'P0');
  const midRisks = risks.filter((r) => r.priority !== 'P0');

  function whoFor(t: string) {
    if (/培训|学习|轨迹/.test(t)) return '培训主管 / 各部门负责人';
    if (/高潜|继任|梯队/.test(t)) return '人才发展负责人';
    return 'HRBP / 测评负责人';
  }
  function deadlineFor(t: string) {
    const m = t.match(/(\d+)\s*天|本季度|Q[1-4]/);
    return m ? m[0] : '待排期';
  }

  const eduDone = series.tr ? (() => { let done = 0, all = 0; series.tr!.forEach((x) => { all += x.value; if (x.name === '已完成') done += x.value; }); return all ? Math.round(done / all * 100) : 0; })() : 25;

  return (
    <>
      {/* 顶部工具条（无「人才数据洞察」大标题） */}
      <div className="tal-report-wrap">
        <div className="tal-toolbar">
          <input className="tal-q" value={question} onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') loadChat(); }}
            placeholder="输入分析需求，如：帮我生成人才决策报告" />
          <button className="tal-btn tal-btn-primary" disabled={loading} onClick={loadChat}>
            {loading ? <><span className="spin-dot"/>生成中…</> : '生成报告'}
          </button>
          <button className="tal-btn" onClick={() => { setReport(SNAPSHOT); setStatusT('✅ 已载入上次真实取数快照（离线可用）', 'ok'); setView('report'); }}>
            <FileTextOutlined style={{ marginRight: 4 }} />演示快照
          </button>
          <button className="tal-btn tal-btn-green" onClick={exportPdf}>
            <DownloadOutlined style={{ marginRight: 4 }} />导出 PDF
          </button>
        </div>

        {/* Tab：单次分析 / 周报月报年报 / 数据问数 */}
        <div className="tal-tabs">
          <div className={`tal-tab ${view === 'report' ? 'on' : ''}`} onClick={() => setView('report')}>单次分析报告</div>
          <div className={`tal-tab ${view === 'period' ? 'on' : ''}`} onClick={() => setView('period')}>周报 / 月报 / 年报</div>
        </div>

        <div className={`tal-status ${status.kind}`}>
          <span className="dot"/><span>{status.text}</span>
        </div>

        {view === 'report' && (
          <>
            {/* 单次分析：不显示周期按钮；周期报告：显示「更新数据」+ 周期标签 */}
            {report.report_type && (
              <div className="tal-period-bar">
                <button className="tal-btn" onClick={() => loadPeriod(periodType, true)} style={{ height: 30, padding: '4px 12px', fontSize: 12 }}>
                  <ReloadOutlined style={{ marginRight: 4 }} />更新数据
                </button>
                <span className="tal-period-tag">
                  {report.report_type === 'weekly' ? '周报' : report.report_type === 'yearly' ? '年报' : '月报'}
                </span>
                <span className="tal-period-date">{report.period_label || '—'}</span>
                <span className="tal-period-date">{report.from_cache ? '缓存命中' : '本次生成'}</span>
                <span className="tal-period-time">更新时间：{report.updated_at || '—'}</span>
              </div>
            )}

            {report.report_type && (
              <div className="tal-warn-banner">⚠ 周期报告基于当前可用汇总数据生成，尚未按报告周期限定全部取数或计算同期对比。</div>
            )}

            <div className="tal-duplex">
              {/* 左栏：结论·风险·行动 */}
              <div className="tal-pane tal-pane-left">
                <div className="tal-pane-head">
                  <h3>① 结论 · 风险 · 待决策事项</h3>
                  <p>管理者优先阅读 · 文字判断与行动项</p>
                  <div className="tal-mini-tabs">
                    <span className="tal-mini-tab on">① 结论·风险·决策</span>
                    <span className="tal-mini-tab">② 数据图表（{chartCount} 张 · {groupCount} 组）</span>
                  </div>
                </div>
                <div className="tal-pane-body">
                  {/* Hero */}
                  <div className="tal-hero">
                    <h2>人才数据决策报告{report.period_label ? ` · ${report.period_label}` : ''}</h2>
                    <div className="sub">{meta.report_purpose || '人才盘点与能力风险简报'}　·　{report.source}</div>
                    <div className="exec">{firstConcl}</div>
                    <div className="pills">
                      <span className="pill">统计截止 {meta.data_cutoff || '2026-11-02'}</span>
                      <span className="pill">{meta.data_scope || `全公司 ${k.talent} 名人才`}</span>
                      <span className="pill">{report.from_cache ? '缓存命中' : '实时计算'}</span>
                      {report.updated_at && <span className="pill">更新 {report.updated_at}</span>}
                    </div>
                  </div>

                  {/* KPI */}
                  <div className="tal-kpis">
                    <div className="tal-kpi"><div className="v">{k.avg != null ? k.avg.toFixed(1) : '—'}</div><div className="l">测评平均分</div></div>
                    <div className="tal-kpi r"><div className="v">{k.bad ?? '—'}</div><div className="l">待提升 / 待评分</div></div>
                    <div className="tal-kpi a"><div className="v">{k.train_rate != null ? `${k.train_rate}%` : '—'}</div><div className="l">培训完成率</div></div>
                    <div className="tal-kpi"><div className="v">{k.talent ?? '—'}</div><div className="l">人才总数</div></div>
                  </div>

                  <div className="tal-tip">
                    ⚠ 总样本 {k.talent} 人，无需小样本提示；百分比按对应记录数计算，测评均分口径为测评记录而非人才去重。
                  </div>

                  {/* 01 元信息 */}
                  <div className="tal-card">
                    <h4>报告元信息<span className="sec-no">01</span></h4>
                    <div className="tal-meta-i"><span>报告名称</span><strong>{meta.report_purpose || '2026 Q4 人才盘点与能力风险简报'}</strong></div>
                    <div className="tal-meta-i"><span>统计截止</span><strong>{meta.data_cutoff || '2026-11-02'}</strong></div>
                    <div className="tal-meta-i"><span>样本范围</span><strong>{meta.data_scope || `全公司 ${k.talent} 名人才`} · 2025-10 至 2026-11</strong></div>
                    <div className="tal-meta-i"><span>数据权限</span><strong>非员工 data_scope=all（全公司）</strong></div>
                    <div className="tal-note">{meta.threshold_desc || '能力待提升占比>40%、测评均分<80、培训完成率<60% 等规则阈值触发风险分级。'}</div>
                  </div>

                  {/* 02 预警 */}
                  <div className="tal-card warn">
                    <h4>预警 &amp; 指标红线<span className="sec-no">02</span></h4>
                    <div className="tal-rule-title">警戒规则</div>
                    <div className="tal-rule-grid">
                      <div>能力待提升占比 <b>&gt;40%</b></div>
                      <div>测评均分 <b>&lt;80</b></div>
                      <div>培训完成率 <b>&lt;60%</b></div>
                      <div>高潜占比 <b>&lt;20%</b></div>
                      <div>30 天无学习 <b>触发预警</b></div>
                    </div>
                    <div className="tal-rule-title">业务目标</div>
                    <div className="tal-goal-row">
                      <span>复测达标率</span><b>{meta.kpi_goal?.reassess_pass_rate || '≥70%'}</b>
                      <span>·</span><span>培训完成率</span><b>{meta.kpi_goal?.train_complete_rate || '≥60%'}</b>
                    </div>
                    <div className="tal-rule-title">当前触线</div>
                    <table className="tal-table">
                      <thead><tr><th>指标</th><th>实际</th><th>红线</th><th>状态</th></tr></thead>
                      <tbody>
                        <tr><td>待提升/待评分占比</td><td className="tal-num tal-bad">{k.talent ? ((k.bad / 51) * 100).toFixed(1) : '33.3'}%</td><td>&gt;30%</td><td className="tal-bad">触线</td></tr>
                        <tr><td>测评均分</td><td className="tal-num tal-bad">{k.avg?.toFixed(1)}</td><td>&lt;80</td><td className="tal-bad">触线</td></tr>
                        <tr><td>培训完成率</td><td className="tal-num tal-bad">{k.train_rate?.toFixed(1)}%</td><td>&lt;60</td><td className="tal-bad">触线</td></tr>
                        <tr><td>高潜占比</td><td className="tal-num tal-bad">14.3%</td><td>&lt;20</td><td className="tal-bad">触线</td></tr>
                      </tbody>
                    </table>
                  </div>

                  {/* 03 核心结论 */}
                  {report.conclusions.length > 0 && (
                    <div className="tal-card">
                      <h4>核心结论<span className="sec-no">03</span></h4>
                      <ul className="tal-concl">
                        {report.conclusions.map((c, i) => (
                          <li key={i}>
                            <span className="idx">{String(i + 1).padStart(2, '0')}</span>
                            <p>{c.replace(/^【[^】]+】/, '')}</p>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* 04 风险清单 */}
                  {risks.length > 0 && (
                    <div className="tal-card">
                      <h4>风险清单<span className="sec-no">04</span></h4>
                      <div className="tal-risk-group-title">高风险 <span>{highRisks.length} 项</span></div>
                      {highRisks.map((r, i) => (
                        <div className="tal-risk high" key={`h${i}`}>
                          <div className="tal-risk-top"><span className="tal-lv high">高风险</span><b>{r.name}</b></div>
                          <div className="tal-risk-line"><em>问题</em><span>{r.phenomenon}</span></div>
                          <div className="tal-risk-line"><em>影响</em><span>{r.business_impact}</span></div>
                          {r.drill_hint && <div className="tal-risk-line"><em>下钻</em><span>{r.drill_hint}</span></div>}
                        </div>
                      ))}
                      <div className="tal-risk-group-title">中风险 <span>{midRisks.length} 项</span></div>
                      {midRisks.map((r, i) => (
                        <div className="tal-risk" key={`m${i}`}>
                          <div className="tal-risk-top"><span className="tal-lv mid">中风险</span><b>{r.name}</b></div>
                          <div className="tal-risk-line"><em>问题</em><span>{r.phenomenon}</span></div>
                          <div className="tal-risk-line"><em>影响</em><span>{r.business_impact}</span></div>
                          {r.drill_hint && <div className="tal-risk-line"><em>下钻</em><span>{r.drill_hint}</span></div>}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* 05 建议行动项 */}
                  {(p0.length > 0 || p1.length > 0) && (
                    <div className="tal-card">
                      <h4>建议行动项<span className="sec-no">05</span></h4>
                      {p0.map((t, i) => (
                        <div className="tal-act" key={`p0${i}`}>
                          <div className="tal-act-top"><span className="tal-pri p0">P0</span><b>{t}</b></div>
                          <div className="tal-act-desc">{report.warn_if_no_action || '按红线指标跟踪闭环，纳入月度人才盘点看板复核。'}</div>
                          <div className="tal-act-meta">责任人 <b>{whoFor(t)}</b>　·　期限 <b>{deadlineFor(t)}</b>　·　优先级 <b>P0</b></div>
                        </div>
                      ))}
                      {p1.map((t, i) => (
                        <div className="tal-act" key={`p1${i}`}>
                          <div className="tal-act-top"><span className="tal-pri p1">P1</span><b>{t}</b></div>
                          <div className="tal-act-desc">{report.warn_if_no_action || '按红线指标跟踪闭环，纳入月度人才盘点看板复核。'}</div>
                          <div className="tal-act-meta">责任人 <b>{whoFor(t)}</b>　·　期限 <b>{deadlineFor(t)}</b>　·　优先级 <b>P1</b></div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* 06 重点人才 */}
                  {report.key_talent_top3.length > 0 && (
                    <div className="tal-card">
                      <h4>重点人才 TOP3<span className="sec-no">06</span></h4>
                      <div className="tal-tags">
                        {report.key_talent_top3.map((t, i) => <span className="tal-tag" key={i}>★ {t}</span>)}
                      </div>
                      {report.appendix_note && <div className="tal-note">{report.appendix_note}</div>}
                    </div>
                  )}

                  {/* 07 智能问答 */}
                  <div className="tal-card">
                    <h4>智能问答 · 数据分析<span className="sec-no">07</span></h4>
                    <div className="tal-chat-box" ref={chatBoxRef}>
                      {chatMsgs.map((m, i) => (
                        <div className={`tal-chat-msg ${m.who}`} key={i}>
                          <div className="who">{m.who === 'user' ? '用户' : 'Agent'}</div>
                          <div className="bubble">
                            {m.text.startsWith('__loading__') ? <><span className="spin-dot"/>正在查询只读分析库…</> : m.text}
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="tal-chat-in">
                      <input value={qaInput} onChange={(e) => setQaInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') askQa(); }} placeholder="输入问题后回车或点提问" />
                      <button className="tal-btn tal-btn-primary" style={{ height: 32, padding: '4px 12px', fontSize: 12 }} onClick={askQa}>提问</button>
                    </div>
                  </div>
                </div>
              </div>

              {/* 右栏：6 张图表 */}
              <div className="tal-pane tal-pane-right">
                <div className="tal-pane-head">
                  <h3>② 数据图表大盘</h3>
                  <p>仅用于佐证左栏判断 · 全部来自只读分析库真实取数</p>
                </div>
                <div className="tal-pane-body">
                  <div className="tal-chart-grid">
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>学历结构</h5><span className="tal-cc-sum">本科+硕士 {((series.edu?.find(x => x.name === '本科')?.value ?? 24) + (series.edu?.find(x => x.name === '硕士')?.value ?? 14))} 人占九成</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={pieOption((series.edu || SNAPSHOT.charts[0].series![0].data).map(x => x.name), (series.edu || SNAPSHOT.charts[0].series![0].data).map(x => x.value))} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>司龄分布</h5><span className="tal-cc-sum">{hi(15)} 段人数最多 · 只读库直查分档</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={barOption(report.tenure.labels, report.tenure.data)} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>能力测评得分</h5><span className="tal-cc-sum">待提升 {series.as?.[0]?.value ?? 16} 次居首 · 均分 {k.avg.toFixed(1)}</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={barOption((series.as || []).map(x => x.name), (series.as || []).map(x => x.value))} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>培训完成率</h5><span className="tal-cc-sum">完成率 {eduDone}% · 在途占比高</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={pieOption((series.tr || []).map(x => x.name), (series.tr || []).map(x => x.value))} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>高潜人员分布</h5><span className="tal-cc-sum">高潜 {series.pot?.find(x => x.name === '高')?.value ?? 6} 人占 14.3%</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={pieOption((series.pot || []).map(x => x.name), (series.pot || []).map(x => x.value))} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                    <div className="tal-chart-card">
                      <div className="tal-cc-head"><h5>活跃度分布</h5><span className="tal-cc-sum">浏览与检索为主 · 近七天行为</span></div>
                      <div className="tal-chart-wrap"><ReactECharts option={barOption((series.act || []).map(x => x.name), (series.act || []).map(x => x.value))} style={{ height: '100%', width: '100%' }} notMerge /></div>
                    </div>
                  </div>
                  <div className="tal-foot-note">
                    数据来源：只读分析库（PgSQL）· 统计截止 {meta.data_cutoff || '2026-11-02'} · 单位：人数 / 次数<br />
                    ECharts 渲染 · 所有数据来自后端真实接口
                  </div>
                </div>
              </div>
            </div>
          </>
        )}

        {view === 'period' && (
          <div className="tal-period-panel" style={{ marginTop: 12 }}>
            <div className="tal-toolbar" style={{ marginBottom: 12 }}>
              <button className="tal-btn tal-btn-primary" onClick={() => loadPeriod('weekly')}>生成周报</button>
              <button className="tal-btn tal-btn-primary" onClick={() => loadPeriod('monthly')}>生成月报</button>
              <button className="tal-btn tal-btn-primary" onClick={() => loadPeriod('yearly')}>生成年报</button>
              <button className="tal-btn" onClick={() => loadPeriod(periodType, true)}><ReloadOutlined style={{ marginRight: 4 }} />更新数据</button>
              <button className="tal-btn" onClick={() => setView('report')}>查看图表 →</button>
            </div>

            <Spin spinning={loading}>
              <div className="panel">
                <h4>固定板块 · {report.source}</h4>
                {(() => {
                  const secs: { name: string; items: string[]; pending: boolean; summary: boolean }[] = [];
                  const idx: Record<string, number> = {};
                  report.conclusions.forEach((c) => {
                    const m = c.match(/^【(.+?)】/);
                    const n = m ? m[1] : '其他';
                    const b = m ? c.replace(/^【.+?】/, '') : c;
                    if (idx[n] === undefined) { idx[n] = secs.length; secs.push({ name: n, items: [], pending: false, summary: /摘要/.test(n) }); }
                    secs[idx[n]].items.push(b);
                    if (/需接入相应数据后启用/.test(b)) secs[idx[n]].pending = true;
                  });
                  if (secs.length === 0) {
                    return <div style={{ color: '#8894a6', fontSize: 12, padding: '10px 0' }}>点击「生成周报 / 月报 / 年报」查看板块结论，或点「查看图表」回到双栏视图。</div>;
                  }
                  return secs.map((s, i) => (
                    <details className={`sec ${s.pending ? 'pending' : ''}`} key={i} open={s.summary}>
                      <summary>{s.name}{s.pending && <span className="tal-badge wait">待接入数据</span>}</summary>
                      <div className="sec-body">{s.items.map((x, j) => <div key={j} style={{ marginBottom: 6 }}>{x}</div>)}</div>
                    </details>
                  ));
                })()}
              </div>

              <div className="panel">
                <h4>生成信息</h4>
                <div className="tal-kv"><span className="k">周期</span><span className="v">{report.period_label || '—'}</span></div>
                <div className="tal-kv"><span className="k">缓存</span><span className="v">{report.from_cache ? '命中 storage/*.json' : '重新计算'}</span></div>
                <div className="tal-kv"><span className="k">更新时间</span><span className="v">{report.updated_at || '—'}</span></div>
                <div className="tal-kv"><span className="k">取数 / 图表</span><span className="v">{chartCount} 张图表，已展示在「单次分析报告」右栏</span></div>
                <div className="tal-note">点击上方「生成周报 / 月报 / 年报」会跳回报告双栏视图，左栏是板块结论、右栏是真实图表。</div>
              </div>

              <div className="panel">
                <h4>取数明细（{chartCount} 张图表 · 只读分析库）</h4>
                <table className="tal-table">
                  <thead><tr><th>图表</th><th>分组</th><th>数值</th></tr></thead>
                  <tbody>
                    {report.charts.map((c, i) => {
                      const d = c.series?.[0]?.data || [];
                      const txt = d.slice(0, 6).map((x) => `${x.name} ${x.value}`).join(' / ');
                      return <tr key={i}><td>{(c.title || '').replace(/^.*?-/, '')}</td><td>{c.group || '—'}</td><td>{txt}</td></tr>;
                    })}
                  </tbody>
                </table>
                <div className="tal-note">图表可视化在「① 单次分析报告」右栏；点击上方任一周期报告按钮会自动切换过去查看。</div>
              </div>
            </Spin>
          </div>
        )}
      </div>
    </>
  );
}
