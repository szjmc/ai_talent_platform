import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Form, Input, InputNumber, Modal, Pagination, Progress, Select, Table, Tabs, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { BookOutlined, ClockCircleOutlined, PlusOutlined, RobotOutlined, TeamOutlined, ThunderboltOutlined, WarningOutlined } from '@ant-design/icons';
import { PageHeader, PersonAvatar, StatusBadge } from '../components/Common';
import { talentsApi, trainingsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatDate } from '../utils';
import type { Course, Talent, TrainingPlan, TrainingRecommendation } from '../types';

const palette = ['#4e6ef2', '#8b5cf6', '#13a8a8', '#f59e0b'];
interface LagAlert { plan_id: number; talent_id: number; name: string; completion: number; lag_ratio: number; alert: string }

export default function TrainingsPage() {
  const [coursePage, setCoursePage] = useState(1); const [planPage, setPlanPage] = useState(1);
  const [tab, setTab] = useState('courses'); const [recommendOpen, setRecommendOpen] = useState(false);
  const [loading, setLoading] = useState(false); const [courses, setCourses] = useState<Course[]>([]); const [courseTotal, setCourseTotal] = useState(0);
  const [plans, setPlans] = useState<TrainingPlan[]>([]); const [planTotal, setPlanTotal] = useState(0);
  const [alerts, setAlerts] = useState<LagAlert[]>([]); const [loadError, setLoadError] = useState('');
  const [talents, setTalents] = useState<Talent[]>([]); const [talentId, setTalentId] = useState<number>();
  const [recommendations, setRecommendations] = useState<TrainingRecommendation[]>([]); const [recommendSummary, setRecommendSummary] = useState('');
  const [courseOpen, setCourseOpen] = useState(false); const [savingCourse, setSavingCourse] = useState(false);
  const { message } = App.useApp(); const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const [c, p] = await Promise.all([trainingsApi.courses({ page: coursePage, page_size: 12, is_active: true }), trainingsApi.plans({ page: planPage, page_size: 10 })]);
      setCourses(c.items || []); setCourseTotal(c.total || 0); setPlans(p.items || []); setPlanTotal(p.total || 0);
    } catch (error) { setLoadError(formatApiError(error)); }
    finally { setLoading(false); }
  }, [coursePage, planPage]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { talentsApi.list({ page: 1, page_size: 100 }).then((res) => setTalents(res.items || [])).catch(() => setTalents([])); }, []);

  const recommend = async () => {
    if (!talentId) { message.warning('请选择培养对象'); return; }
    setLoading(true);
    try { const res = await trainingsApi.recommend({ talent_id: talentId, top_k: 3 }); setRecommendations(res.recommendations || []); setRecommendSummary(res.summary || ''); }
    catch (error) { message.error(formatApiError(error)); } finally { setLoading(false); }
  };
  const generatePlan = async () => {
    if (!talentId) { message.warning('请选择培养对象'); return; }
    try {
      const res = await trainingsApi.generatePlan({ talent_id: talentId, auto_push: true });
      if ((res as { no_training_needed?: boolean }).no_training_needed) message.success('各维度均已达标，无需生成培训计划');
      else message.success('个性化培养计划已创建并推送');
      setRecommendOpen(false); await load();
    } catch (error) { message.error(formatApiError(error)); }
  };
  const scanLag = async () => {
    try { const res = await trainingsApi.lagScan(); setAlerts((res.alerts || []) as LagAlert[]); message.success(`扫描完成：${res.scanned} 个计划，提醒 ${res.reminded}，预警 ${res.warned}`); }
    catch (error) { message.error(formatApiError(error)); }
  };
  const createCourse = async () => {
    const values = await form.validateFields(); setSavingCourse(true);
    try { await trainingsApi.createCourse(values); message.success('课程已创建'); setCourseOpen(false); form.resetFields(); await load(); }
    catch (error) { message.error(formatApiError(error)); } finally { setSavingCourse(false); }
  };
  const avgCompletion = plans.length ? Math.round(plans.reduce((acc, p) => acc + (p.completion_rate || 0), 0) / plans.length * 1000) / 10 : null;
  const planColumns: ColumnsType<TrainingPlan> = [
    { title: '培养计划', dataIndex: 'name', render: (v, r) => <div><div className="talent-cell-name">{v}{r.created_by_ai && <Tag color="purple" style={{ marginLeft: 7, fontSize: 9 }}>AI 生成</Tag>}</div><div className="talent-cell-role">截止 {formatDate(r.end_date)}</div></div> },
    { title: '培养对象', dataIndex: 'talent_id', width: 150, render: (v) => <div className="talent-cell"><PersonAvatar name={`人才${v}`} size={30}/>人才 #{v}</div> },
    { title: '学习进度', dataIndex: 'completion_rate', width: 190, render: (v) => <div className="flex items-center gap-8"><Progress percent={Math.round((v || 0) * 100)} size="small" style={{ width: 120 }} strokeColor={v >= 1 ? '#1c9b78' : '#5273df'}/></div> },
    { title: '状态', dataIndex: 'status', width: 100, render: (v) => <StatusBadge status={v}/> },
    { title: '操作', width: 100, render: (_, r) => <Button type="link" size="small" onClick={async () => { try { const detail = await trainingsApi.plan(r.id); Modal.info({ title: r.name, width: 640, content: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(detail, null, 2)}</pre> }); } catch (error) { message.error(formatApiError(error)); } }}>查看详情</Button> },
  ];
  const alertColumns: ColumnsType<LagAlert> = [
    { title: '计划', dataIndex: 'name', render: (v, r) => <div><div className="talent-cell-name">{v}</div><div className="talent-cell-role">计划 #{r.plan_id} · 人才 #{r.talent_id}</div></div> },
    { title: '完成率', dataIndex: 'completion', width: 160, render: (v) => <Progress percent={Math.round((v || 0) * 100)} size="small"/> },
    { title: '滞后比例', dataIndex: 'lag_ratio', width: 120, render: (v) => `${Math.round((v || 0) * 100)}%` },
    { title: '预警级别', dataIndex: 'alert', width: 120, render: (v) => <Tag color="orange">{v}</Tag> },
  ];

  return <>
    <PageHeader title="智能培训发展" description="基于能力缺口定制学习路径，打通“测评—诊断—培训—提升”成长闭环" actions={<><Button icon={<ThunderboltOutlined/>} onClick={() => { setRecommendOpen(true); setRecommendations([]); setRecommendSummary(''); }}>AI 推荐课程</Button><Button type="primary" icon={<PlusOutlined/>} onClick={() => setCourseOpen(true)}>新建课程</Button></>}/>
    {loadError && <Alert type="error" showIcon message="培训数据加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">在线课程</div><div className="metric-strip-value">{courseTotal}<span className="metric-strip-extra">门</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">培养计划</div><div className="metric-strip-value">{planTotal}<span className="metric-strip-extra">项</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">进行中计划</div><div className="metric-strip-value">{plans.filter((p) => p.status === 'ongoing').length}<span className="metric-strip-extra">当前页</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页平均完成率</div><div className="metric-strip-value">{avgCompletion ?? '—'}<span className="metric-strip-extra">%</span></div></div></div>
    <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'courses', label: <span><BookOutlined/> 课程中心</span> },{ key: 'plans', label: <span><TeamOutlined/> 培养计划</span> },{ key: 'warnings', label: <span><WarningOutlined/> 学习预警 <Tag color="orange" style={{ marginLeft: 3 }}>{alerts.length}</Tag></span> }]} style={{ marginBottom: 16 }}/>
    {tab === 'courses' && <><div className="card filter-card"><div className="filter-row"><span className="muted" style={{ fontSize: 11 }}>共 {courseTotal} 门上架课程</span><div className="filter-spacer"/><Button onClick={() => void load()}>刷新</Button></div></div><div className="course-grid">{courses.map((c, index) => <div className="card course-card" key={c.id}><div className="course-cover" style={{ background: `linear-gradient(135deg,${palette[index % palette.length]},${palette[index % palette.length]}bb)` }}><span className="course-category">{c.category || '未分类'}</span><div className="course-cover-icon"><RobotOutlined/></div></div><div className="course-content"><div className="course-title">{c.title}</div><div className="course-desc">{c.description || '暂无简介'}</div><div className="course-meta"><span><ClockCircleOutlined/> {c.duration_hours ?? '—'} 学时</span><Tag style={{ marginLeft: 'auto', fontSize: 9 }}>{c.level || '—'}</Tag></div><div className="flex justify-between items-center mt-16"><span className="muted" style={{ fontSize: 10 }}>{c.is_active ? '上架中' : '已下架'}</span><Button size="small" type="primary" ghost onClick={() => c.resource_url ? window.open(c.resource_url, '_blank', 'noopener') : message.info('该课程暂未配置资源地址')}>查看课程</Button></div></div></div>)}</div>{courseTotal > 12 && <Pagination current={coursePage} pageSize={12} total={courseTotal} showSizeChanger={false} onChange={setCoursePage} style={{ marginTop: 16 }} />}</>}
    {tab === 'plans' && <div className="card content-card"><div className="table-toolbar"><div className="table-title">人才培养计划<span className="table-count">共 {planTotal} 项</span></div><Button type="primary" size="small" icon={<PlusOutlined/>} onClick={() => setRecommendOpen(true)}>创建培养计划</Button></div><Table rowKey="id" columns={planColumns} dataSource={plans} loading={loading} pagination={{ current: planPage, pageSize: 10, total: planTotal, showSizeChanger: false, onChange: setPlanPage }}/></div>}
    {tab === 'warnings' && <div className="card content-card"><div className="table-toolbar"><div className="table-title">学习滞后预警<span className="table-count">来自后端滞后扫描</span></div><Button size="small" icon={<ThunderboltOutlined/>} onClick={scanLag}>一键督办</Button></div><Table rowKey="plan_id" columns={alertColumns} dataSource={alerts} pagination={false} locale={{ emptyText: '暂无预警，点击一键督办扫描' }}/></div>}
    <Drawer width={570} open={recommendOpen} onClose={() => setRecommendOpen(false)} title="AI 个性化课程推荐" extra={<Tag color="purple">Training Agent</Tag>}>
      <div className="config-step-title"><span className="step-number">1</span>选择培养对象</div><Select className="w-100 mb-16" value={talentId} onChange={(v) => { setTalentId(v); setRecommendations([]); }} placeholder="选择人才" options={talents.map((t) => ({ value: t.id, label: `${t.name} · ${t.current_position || '—'}` }))}/><Button block type="primary" style={{ margin: '18px 0' }} loading={loading} icon={<ThunderboltOutlined/>} onClick={recommend}>诊断能力缺口并推荐</Button>
      {recommendSummary && <><div className="drawer-section-title">能力缺口诊断</div><div className="portrait-summary">{recommendSummary}</div></>}
      {recommendations.length > 0 && <><div className="drawer-section-title">专属推荐学习路径</div>{recommendations.map((c, i) => <div className="card" style={{ padding: 15, marginBottom: 12 }} key={c.course_id}><div className="flex justify-between items-center"><b style={{ fontSize: 13 }}>{i + 1}. {c.title}</b><Tag color="blue">{c.duration_hours ?? '—'} 学时</Tag></div><p style={{ fontSize: 11, color: '#7e899b', lineHeight: 19 }}>推荐理由：{c.reason}</p><Progress percent={Math.round(c.score)} size="small" strokeColor={palette[i % palette.length]} format={(v) => `适配度 ${v}%`}/></div>)}<Button block type="primary" onClick={generatePlan}>生成培养计划并推送</Button></>}
    </Drawer>
    <Modal title="新建课程" open={courseOpen} onCancel={() => setCourseOpen(false)} onOk={createCourse} confirmLoading={savingCourse} okText="创建课程"><Form form={form} layout="vertical" initialValues={{ is_active: true }}><Form.Item name="title" label="课程名称" rules={[{ required: true }]}><Input/></Form.Item><Form.Item name="category" label="分类 / 能力维度"><Input/></Form.Item><Form.Item name="level" label="适用层级"><Input placeholder="L1~L5 或 初级/中级/高级"/></Form.Item><Form.Item name="duration_hours" label="学时"><InputNumber min={0} style={{ width: '100%' }}/></Form.Item><Form.Item name="resource_url" label="资源地址"><Input/></Form.Item><Form.Item name="description" label="简介"><Input.TextArea rows={3}/></Form.Item></Form></Modal>
  </>;
}
