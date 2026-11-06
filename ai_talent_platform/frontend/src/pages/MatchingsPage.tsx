import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, InputNumber, Progress, Select, Slider, Spin, Tag } from 'antd';
import { BulbOutlined, CheckCircleFilled, HistoryOutlined, InfoCircleOutlined, NodeIndexOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { Chart, PageHeader, PersonAvatar, SkillTags } from '../components/Common';
import { matchingsApi, positionsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import type { Matching, MatchingRunResult, Position } from '../types';

const dimensionLabels: Record<string, string> = { skill: '专业技能', experience: '项目经验', education: '学历背景', quality: '综合素质' };
function normalizeItems(result: MatchingRunResult): Matching[] {
  return (result.items || []).map((item) => ({
    ...item,
    talent_name: item.talent_name || `人才 #${item.talent_id}`,
    skill_tags: item.skill_tags || item.matched_skills || [],
    dimension_scores: item.dimension_scores || {},
    highlights: item.highlights || [], gaps: item.gaps || [],
  }));
}

export default function MatchingsPage() {
  const [results, setResults] = useState<Matching[]>([]); const [selected, setSelected] = useState<Matching | null>(null);
  const [running, setRunning] = useState(false); const [loadingLatest, setLoadingLatest] = useState(false);
  const [positions, setPositions] = useState<Position[]>([]); const [positionError, setPositionError] = useState('');
  const [positionId, setPositionId] = useState<number>(); const [strategy, setStrategy] = useState('hybrid');
  const [topK, setTopK] = useState(20); const [minScore, setMinScore] = useState(0);
  const [batch, setBatch] = useState<MatchingRunResult | null>(null); const { message } = App.useApp();

  useEffect(() => {
    positionsApi.listPlaceholder().then((res) => setPositions((res.items || []) as Position[])).catch((error) => setPositionError(formatApiError(error)));
  }, []);
  const loadLatest = useCallback(async (id: number) => {
    setLoadingLatest(true);
    try { const res = await matchingsApi.latestByPosition(id); setBatch(res); setResults(normalizeItems(res)); }
    catch (error) { setBatch(null); setResults([]); message.info(formatApiError(error)); }
    finally { setLoadingLatest(false); }
  }, [message]);
  useEffect(() => { if (positionId) void loadLatest(positionId); }, [positionId, loadLatest]);

  const run = async () => {
    if (!positionId) { message.warning('请选择或输入目标岗位 ID'); return; }
    setRunning(true);
    try {
      const result = await matchingsApi.run({ position_id: positionId, strategy, top_k: topK, min_score: minScore, talent_ids: null, with_explanation: true });
      setBatch(result); setResults(normalizeItems(result));
      message.success(`匹配完成：参与 ${result.stats.candidates} 人，返回 ${result.stats.output} 位候选人`);
    } catch (error) { message.error(formatApiError(error)); } finally { setRunning(false); }
  };
  const weights = useMemo(() => batch?.weights_used || null, [batch]);
  const selectedPosition = positions.find((p) => p.id === positionId);
  const dimensionEntries = selected ? Object.entries(selected.dimension_scores) : [];

  return <>
    <PageHeader title="人岗精准匹配" description="硬性门槛过滤 × 高维语义召回 × 四维加权评分，让每一次人才选择都有依据" actions={<Button icon={<HistoryOutlined/>} onClick={() => positionId ? loadLatest(positionId) : message.warning('请先选择岗位')}>历史匹配批次</Button>}/>
    {positionError && <Alert type="warning" showIcon message="岗位列表接口暂不可用，可手动输入岗位 ID" description={positionError} style={{ marginBottom: 16 }}/>}
    <div className="matching-layout">
      <aside className="card matching-config"><div className="flex items-center gap-8 mb-20"><div className="ai-logo"><NodeIndexOutlined/></div><div><div className="section-card-title">新建匹配任务</div><div className="section-card-subtitle">AI Matching Agent</div></div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">1</span>选择目标岗位</div>{positions.length ? <Select className="w-100" value={positionId} onChange={setPositionId} options={positions.map((p) => ({ value: p.id, label: `${p.title}（${p.code}）` }))}/> : <InputNumber className="w-100" min={1} placeholder="岗位 ID" value={positionId} onChange={(v) => setPositionId(v || undefined)}/>}<div style={{ background: '#f7f9fd', borderRadius: 7, padding: 12, marginTop: 10, fontSize: 11, color: '#788397', lineHeight: 20 }}>{selectedPosition ? `${selectedPosition.organization_name || ''}<br/>技能要求：${(selectedPosition.skill_tags || []).join(' / ') || '—'}` : '岗位信息以后端画像接口为准'}</div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">2</span>匹配策略</div><Select className="w-100" value={strategy} onChange={setStrategy} options={[{ value: 'hybrid', label: '智能混合匹配（推荐）' },{ value: 'hard', label: '硬性门槛过滤' },{ value: 'soft', label: '向量语义软匹配' }]}/></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">3</span>维度权重</div>{Object.entries(dimensionLabels).map(([key, label], index) => { const value = weights?.[key] != null ? Math.round(weights[key] * 100) : null; return <div className="weight-row" key={key}><div className="weight-label"><span>{label}</span><b>{value != null ? `${value}%` : '—'}</b></div><Progress percent={value || 0} showInfo={false} size="small" strokeColor={['#5273df', '#6f87e4', '#8e9fe7', '#aab6e9'][index]}/></div>; })}<div className="muted" style={{ fontSize: 10 }}><InfoCircleOutlined/> {weights ? '本次匹配实际使用权重' : '权重以匹配结果返回为准'}</div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">4</span>结果筛选</div><div className="weight-label"><span>最低匹配分</span><b>{minScore} 分</b></div><Slider value={minScore} onChange={setMinScore} min={0} max={90}/><div className="flex items-center justify-between"><span className="muted" style={{ fontSize: 12 }}>返回候选人数</span><InputNumber value={topK} onChange={(v) => setTopK(v || 20)} min={1} max={100}/></div></div>
        <Button type="primary" block size="large" icon={<ThunderboltOutlined/>} loading={running} onClick={run}>开始智能匹配</Button><div style={{ textAlign: 'center', fontSize: 10, color: '#9aa3b3', marginTop: 11 }}>全程权限安全过滤 · 结果持久化可追溯</div>
      </aside>
      <section className="card matching-results"><div className="card-header"><div><h3 className="section-card-title">候选人匹配排名 {batch && <Tag color="blue">批次 #{batch.batch_id.slice(0, 8)}</Tag>}</h3><div className="section-card-subtitle">{batch?.position_title || selectedPosition?.title || '未选择岗位'} · 参与 {batch?.stats.candidates ?? 0} 人 · 召回 {batch?.recall_mode || '—'}</div></div></div>
        {batch && <div style={{ background: '#f3f8f6', padding: '10px 14px', borderRadius: 7, fontSize: 11, color: '#4b7e6e', marginBottom: 5 }}><CheckCircleFilled/> 硬性门槛淘汰 {batch.stats.excluded_by_hard} 人，待复核 {batch.stats.needs_review} 人，未知 {batch.stats.unknown} 人，最终返回 {batch.stats.output} 人</div>}
        <Spin spinning={loadingLatest || running}>{results.length === 0 ? <Empty description="暂无匹配结果，请执行匹配或选择已有批次岗位" style={{ padding: 60 }}/> : results.map((r, i) => <div className="match-item" key={r.talent_id}><div className={`rank-number ${i < 3 ? 'top' : ''}`}>{r.rank || i + 1}</div><div className="match-person"><PersonAvatar name={r.talent_name} size={40}/><div><div className="talent-cell-name">{r.talent_name}{i === 0 && <Tag color="gold" style={{ marginLeft: 7, fontSize: 9 }}>最佳匹配</Tag>}</div><div className="talent-cell-role">{r.current_position || '—'}</div><div className="talent-cell-role">{r.organization_name || '—'}</div></div></div><div className="match-skills"><SkillTags tags={r.skill_tags} max={3}/><div style={{ marginTop: 7, fontSize: 10, color: '#8c96a8' }}>{Object.entries(r.dimension_scores).map(([k, v]) => `${dimensionLabels[k] || k} ${v}`).join(' · ')}</div></div><div className="match-score-circle"><strong>{r.score}</strong><span>综合匹配度</span></div><Button type="link" size="small" onClick={() => setSelected(r)}>匹配解读</Button></div>)}</Spin>
      </section>
    </div>
    <Drawer width={590} open={!!selected} onClose={() => setSelected(null)} title="AI 可解释匹配报告" extra={<Tag color="blue">证据可追溯</Tag>}>
      {selected && <><div className="detail-hero"><PersonAvatar name={selected.talent_name} size={60}/><div style={{ flex: 1 }}><div className="detail-name" style={{ fontSize: 19 }}>{selected.talent_name}</div><div className="detail-meta">{selected.current_position || '—'}</div></div><div><div className="score-text" style={{ fontSize: 29 }}>{selected.score}</div><div className="muted" style={{ fontSize: 10 }}>综合匹配度</div></div></div>
      <div className="drawer-section-title">四维匹配分析</div>{dimensionEntries.length ? <Chart height={260} option={{ radar: { indicator: dimensionEntries.map(([k]) => ({ name: dimensionLabels[k] || k, max: 100 })), axisName: { color: '#667085' }, splitArea: { areaStyle: { color: ['#fafbff', '#f3f6fc'] } } }, series: [{ type: 'radar', data: [{ value: dimensionEntries.map(([, v]) => v), name: '候选人', areaStyle: { color: 'rgba(66,103,221,.25)' }, lineStyle: { color: '#476bdd' } }] }] }}/> : <div className="empty-hint">暂无维度得分</div>}
      <div className="description-list">{dimensionEntries.map(([k, v]) => <div key={k}><div className="description-item-label">{dimensionLabels[k] || k}{weights?.[k] != null ? `（${Math.round(weights[k] * 100)}%）` : ''}</div><div className="description-item-value"><b className="text-primary">{v}</b> / 100</div></div>)}</div>
      <div className="drawer-section-title"><BulbOutlined className="text-primary"/> 匹配亮点</div>{selected.highlights.length ? selected.highlights.map((x) => <div key={x} style={{ fontSize: 12, color: '#526074', padding: '7px 0' }}><CheckCircleFilled style={{ color: '#1da17c', marginRight: 8 }}/>{x}</div>) : <div className="empty-hint">暂无亮点说明</div>}
      <div className="drawer-section-title">待考察项</div><div style={{ padding: 12, background: '#fff8e9', borderRadius: 7, fontSize: 12, color: '#9b732b' }}>{selected.gaps.join('；') || '暂无'}</div><div className="drawer-section-title">面试建议</div><div className="portrait-summary">{selected.suggestion || '暂无建议'}</div><Button block type="primary" style={{ marginTop: 22 }} icon={<UserOutlined/>} onClick={() => message.info('请前往测评模块为该候选人创建测评')}>推荐进入面试</Button></>}
    </Drawer>
  </>;
}
