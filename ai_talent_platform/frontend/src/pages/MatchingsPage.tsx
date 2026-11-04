import { useState } from 'react';
import { App, Button, Drawer, InputNumber, Progress, Select, Slider, Tag } from 'antd';
import { BulbOutlined, CheckCircleFilled, HistoryOutlined, InfoCircleOutlined, NodeIndexOutlined, RobotOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { Chart, PageHeader, PersonAvatar, SkillTags } from '../components/Common';
import { demoMatches, demoPositions } from '../services/mockData';
import { useAuth } from '../store/AuthContext';
import { formatApiError, request } from '../services/api';
import { sleep } from '../utils';
import type { Matching } from '../types';

export default function MatchingsPage() {
  const [results, setResults] = useState(demoMatches); const [selected, setSelected] = useState<Matching | null>(null); const [running, setRunning] = useState(false);
  const [positionId, setPositionId] = useState(3); const [strategy, setStrategy] = useState('hybrid'); const [topK, setTopK] = useState(20); const [minScore, setMinScore] = useState(60);
  const { demo } = useAuth(); const { message } = App.useApp();
  const run = async () => { setRunning(true); try {
    if (!demo) { const result = await request<{ items: Matching[] }>({ method: 'POST', url: '/matchings/run', data: { position_id: positionId, strategy, top_k: topK, min_score: minScore, talent_ids: [] } }); setResults(result.items); }
    else { await sleep(1700); setResults([...demoMatches].sort(() => Math.random() - .5).sort((a,b) => b.score-a.score)); }
    message.success('匹配完成：从 1,168 位在职人才中筛选出 20 位候选人');
  } catch (e) { message.error(formatApiError(e)); } finally { setRunning(false); } };
  return <>
    <PageHeader title="人岗精准匹配" description="硬性门槛过滤 × 高维语义召回 × 四维加权评分，让每一次人才选择都有依据" demo={demo} actions={<Button icon={<HistoryOutlined/>}>历史匹配批次</Button>}/>
    <div className="matching-layout">
      <aside className="card matching-config"><div className="flex items-center gap-8 mb-20"><div className="ai-logo"><NodeIndexOutlined/></div><div><div className="section-card-title">新建匹配任务</div><div className="section-card-subtitle">AI Matching Agent</div></div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">1</span>选择目标岗位</div><Select className="w-100" value={positionId} onChange={setPositionId} options={demoPositions.filter((p) => p.status === 'open').map((p) => ({ value: p.id, label: `${p.title}（${p.code}）` }))}/><div style={{ background: '#f7f9fd', borderRadius: 7, padding: 12, marginTop: 10, fontSize: 11, color: '#788397', lineHeight: 20 }}>高级 · 技术研发部<br/>技能要求：Python / FastAPI / MySQL / 微服务</div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">2</span>匹配策略</div><Select className="w-100" value={strategy} onChange={setStrategy} options={[{ value: 'hybrid', label: '智能混合匹配（推荐）' },{ value: 'hard', label: '硬性门槛过滤' },{ value: 'soft', label: '向量语义软匹配' }]}/></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">3</span>维度权重</div>{[['专业技能',40,'#5273df'],['项目经验',30,'#6f87e4'],['学历背景',20,'#8e9fe7'],['综合素质',10,'#aab6e9']].map(([n,v,c]) => <div className="weight-row" key={n as string}><div className="weight-label"><span>{n}</span><b>{v}%</b></div><Progress percent={v as number} showInfo={false} size="small" strokeColor={c as string}/></div>)}<div className="muted" style={{ fontSize: 10 }}><InfoCircleOutlined/> 采用岗位默认权重，总和为 100%</div></div>
        <div className="config-step"><div className="config-step-title"><span className="step-number">4</span>结果筛选</div><div className="weight-label"><span>最低匹配分</span><b>{minScore} 分</b></div><Slider value={minScore} onChange={setMinScore} min={0} max={90}/><div className="flex items-center justify-between"><span className="muted" style={{ fontSize: 12 }}>返回候选人数</span><InputNumber value={topK} onChange={(v) => setTopK(v || 20)} min={1} max={100}/></div></div>
        <Button type="primary" block size="large" icon={<ThunderboltOutlined/>} loading={running} onClick={run}>开始智能匹配</Button><div style={{ textAlign: 'center', fontSize: 10, color: '#9aa3b3', marginTop: 11 }}>预计耗时 3–8 秒 · 全程权限安全过滤</div>
      </aside>
      <section className="card matching-results"><div className="card-header"><div><h3 className="section-card-title">候选人匹配排名 <Tag color="blue">批次 #B2026110401</Tag></h3><div className="section-card-subtitle">高级后端工程师 · 共筛选 1,168 人 · 用时 3.2 秒</div></div><Select size="small" defaultValue="score" options={[{ value: 'score', label: '按综合得分排序' },{ value: 'skill', label: '按技能得分排序' }]} style={{ width: 145 }}/></div>
        <div style={{ background: '#f3f8f6', padding: '10px 14px', borderRadius: 7, fontSize: 11, color: '#4b7e6e', marginBottom: 5 }}><CheckCircleFilled/> 硬性门槛已过滤 426 人，向量语义召回 80 人，最终返回综合得分最高的 5 位候选人</div>
        {results.map((r, i) => <div className="match-item" key={r.talent_id}><div className={`rank-number ${i < 3 ? 'top' : ''}`}>{i + 1}</div><div className="match-person"><PersonAvatar name={r.talent_name} size={40}/><div><div className="talent-cell-name">{r.talent_name}{i === 0 && <Tag color="gold" style={{ marginLeft: 7, fontSize: 9 }}>最佳匹配</Tag>}</div><div className="talent-cell-role">{r.current_position}</div><div className="talent-cell-role">{r.organization_name}</div></div></div><div className="match-skills"><SkillTags tags={r.skill_tags} max={3}/><div style={{ marginTop: 7, fontSize: 10, color: '#8c96a8' }}>技能 {r.dimension_scores.skill} · 经验 {r.dimension_scores.experience} · 学历 {r.dimension_scores.education}</div></div><div className="match-score-circle"><strong>{r.score}</strong><span>综合匹配度</span></div><Button type="link" size="small" onClick={() => setSelected(r)}>匹配解读</Button></div>)}
        <div style={{ textAlign: 'center', paddingTop: 15 }}><Button type="link">查看全部 20 位候选人</Button></div>
      </section>
    </div>
    <Drawer width={590} open={!!selected} onClose={() => setSelected(null)} title="AI 可解释匹配报告" extra={<Tag color="blue">证据可追溯</Tag>}>
      {selected && <><div className="detail-hero"><PersonAvatar name={selected.talent_name} size={60}/><div style={{ flex: 1 }}><div className="detail-name" style={{ fontSize: 19 }}>{selected.talent_name}</div><div className="detail-meta">{selected.current_position}</div></div><div><div className="score-text" style={{ fontSize: 29 }}>{selected.score}</div><div className="muted" style={{ fontSize: 10 }}>综合匹配度</div></div></div>
      <div className="drawer-section-title">四维匹配分析</div><Chart height={260} option={{ radar: { indicator: [{ name: '专业技能', max: 100 },{ name: '项目经验', max: 100 },{ name: '学历背景', max: 100 },{ name: '综合素质', max: 100 }], axisName: { color: '#667085' }, splitArea: { areaStyle: { color: ['#fafbff','#f3f6fc'] } } }, series: [{ type: 'radar', data: [{ value: Object.values(selected.dimension_scores), name: '候选人', areaStyle: { color: 'rgba(66,103,221,.25)' }, lineStyle: { color: '#476bdd' } },{ value: [85,80,80,75], name: '岗位基准', lineStyle: { color: '#d5a54f', type: 'dashed' }, itemStyle: { color: '#d5a54f' } }] }] }}/>
      <div className="description-list">{Object.entries(selected.dimension_scores).map(([k,v]) => <div key={k}><div className="description-item-label">{{ skill: '专业技能（40%）', experience: '项目经验（30%）', education: '学历背景（20%）', quality: '综合素质（10%）' }[k]}</div><div className="description-item-value"><b className="text-primary">{v}</b> / 100</div></div>)}</div>
      <div className="drawer-section-title"><BulbOutlined className="text-primary"/> 匹配亮点</div>{selected.highlights.map((x) => <div key={x} style={{ fontSize: 12, color: '#526074', padding: '7px 0' }}><CheckCircleFilled style={{ color: '#1da17c', marginRight: 8 }}/>{x}</div>)}<div className="drawer-section-title">待考察项</div><div style={{ padding: 12, background: '#fff8e9', borderRadius: 7, fontSize: 12, color: '#9b732b' }}>{selected.gaps[0]}</div><div className="drawer-section-title">面试建议</div><div className="portrait-summary">{selected.suggestion}</div><Button block type="primary" style={{ marginTop: 22 }} icon={<UserOutlined/>} onClick={() => message.success('已将该候选人加入面试流程')}>推荐进入面试</Button></>}
    </Drawer>
  </>;
}
