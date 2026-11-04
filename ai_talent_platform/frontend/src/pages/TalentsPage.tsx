import { useEffect, useState } from 'react';
import { App, Button, Col, Drawer, Form, Input, Modal, Pagination, Progress, Row, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, EditOutlined, ExportOutlined, MergeCellsOutlined, PlusOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { Chart, PageHeader, PersonAvatar, PotentialTag, SkillTags, StatusBadge } from '../components/Common';
import { demoTalents } from '../services/mockData';
import { formatApiError, request, requestWithFallback } from '../services/api';
import { downloadText, formatDate, sleep } from '../utils';
import { useAuth } from '../store/AuthContext';
import type { PageResult, Talent } from '../types';

const portraitDimensions = [{ name: '专业技能', value: 94 }, { name: '项目经验', value: 88 }, { name: '综合素质', value: 90 }, { name: '发展潜力', value: 96 }, { name: '团队协作', value: 85 }];

export default function TalentsPage() {
  const [data, setData] = useState<Talent[]>(demoTalents); const [total, setTotal] = useState(demoTalents.length);
  const [loading, setLoading] = useState(false); const [selected, setSelected] = useState<Talent | null>(null);
  const [editing, setEditing] = useState<Talent | null>(null); const [modalOpen, setModalOpen] = useState(false); const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState(''); const [semantic, setSemantic] = useState(''); const [semanticLoading, setSemanticLoading] = useState(false);
  const [filters, setFilters] = useState<{ education?: string; status?: string }>({});
  const [searchParams] = useSearchParams(); const { demo } = useAuth(); const { message, modal } = App.useApp(); const [form] = Form.useForm();
  useEffect(() => { const q = searchParams.get('keyword'); if (q) setKeyword(q); }, [searchParams]);
  const load = async (params?: Record<string, unknown>) => {
    setLoading(true);
    try { const result = await requestWithFallback<PageResult<Talent>>({ url: '/talents', params: { page: 1, page_size: 20, keyword, ...filters, ...params } }, { items: demoTalents, total: demoTalents.length, page: 1, page_size: 20, pages: 1 }); setData(result.data.items); setTotal(result.data.total); }
    catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  const openEdit = (record?: Talent) => { setEditing(record || null); form.setFieldsValue(record ? { ...record, skill_tags: record.skill_tags } : { gender: 'unknown', status: 'active', work_years: 0, skill_tags: [] }); setModalOpen(true); };
  const save = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      if (demo) { await sleep(500); if (editing) setData((old) => old.map((t) => t.id === editing.id ? { ...t, ...values } : t)); else setData((old) => [{ ...demoTalents[0], ...values, id: Date.now() }, ...old]); }
      else { if (editing) await request({ method: 'PUT', url: `/talents/${editing.id}`, data: values }); else await request({ method: 'POST', url: '/talents', data: values }); await load(); }
      message.success(editing ? '人才档案已更新' : '人才档案创建成功，AI 画像生成中'); setModalOpen(false);
    } catch (e) { message.error(formatApiError(e)); } finally { setSaving(false); }
  };
  const semanticSearch = async () => {
    if (!semantic.trim()) return; setSemanticLoading(true);
    try { if (!demo) await request({ method: 'POST', url: '/talents/search/semantic', data: { query: semantic, top_k: 10, filters } }); else await sleep(900); setData(demoTalents.slice(0, 4)); setTotal(4); message.success('已为您找到 4 位高相关人才'); }
    catch (e) { message.error(formatApiError(e)); } finally { setSemanticLoading(false); }
  };
  const remove = (record: Talent) => modal.confirm({ title: `确认删除「${record.name}」的档案？`, content: '删除后，该人才关联向量将同步清理，历史业务记录保留。', okButtonProps: { danger: true }, onOk: async () => { if (!demo) await request({ method: 'DELETE', url: `/talents/${record.id}` }); setData((old) => old.filter((t) => t.id !== record.id)); message.success('档案已删除'); } });
  const columns: ColumnsType<Talent> = [
    { title: '人才信息', dataIndex: 'name', width: 220, render: (_, r) => <div className="talent-cell"><PersonAvatar name={r.name} size={38}/><div><div className="talent-cell-name">{r.name}<span style={{ fontSize: 10, color: '#9aa3b3', marginLeft: 7, fontWeight: 400 }}>ID {String(r.id).padStart(4, '0')}</span></div><div className="talent-cell-role">{r.current_position} · {r.work_years} 年经验</div></div></div> },
    { title: '所属部门', dataIndex: 'organization_name', width: 125 },
    { title: '学历 / 院校', dataIndex: 'education', width: 160, render: (_, r) => <div>{r.education}<div className="talent-cell-role">{r.school}</div></div> },
    { title: '核心技能', dataIndex: 'skill_tags', render: (v) => <SkillTags tags={v} max={3}/> },
    { title: '状态', dataIndex: 'status', width: 90, render: (v) => <StatusBadge status={v}/> },
    { title: '潜力等级', dataIndex: 'potential_level', width: 85, render: (v) => <PotentialTag level={v}/> },
    { title: '入职 / 建档日期', dataIndex: 'created_at', width: 125, render: (v) => formatDate(v) },
    { title: '操作', fixed: 'right', width: 145, render: (_, r) => <div className="flex gap-8"><Button type="link" size="small" onClick={() => setSelected(r)}>画像</Button><Button type="text" size="small" icon={<EditOutlined/>} onClick={() => openEdit(r)}/><Button type="text" size="small" danger icon={<DeleteOutlined/>} onClick={() => remove(r)}/></div> },
  ];
  return <>
    <PageHeader title="人才档案" description="统一管理人才主数据，以 AI 数字画像看见每个人的能力与潜力" demo={demo} actions={<><Button icon={<MergeCellsOutlined/>} onClick={() => message.info('请选择两份重复档案进行智能合并')}>档案合并</Button><Button icon={<ExportOutlined/>} onClick={() => { downloadText('人才档案.csv', '姓名,部门,职位,学历,技能\n' + data.map((t) => `${t.name},${t.organization_name},${t.current_position},${t.education},${t.skill_tags.join('/')}`).join('\n')); message.success('人才数据已导出'); }}>导出</Button><Button type="primary" icon={<PlusOutlined/>} onClick={() => openEdit()}>新建档案</Button></>}/>
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">人才总数</div><div className="metric-strip-value">1,286<span className="metric-strip-extra">人</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">在职人才</div><div className="metric-strip-value">1,168<span className="metric-strip-extra text-success">90.8%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">A 级高潜人才</div><div className="metric-strip-value">306<span className="metric-strip-extra">23.8%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">本月新增档案</div><div className="metric-strip-value">34<span className="metric-strip-extra text-success">↑ 12.5%</span></div></div></div>
    <div className="semantic-search"><div className="search-icon"><ThunderboltOutlined/></div><div className="semantic-body"><div className="semantic-title">AI 语义搜人</div><div className="semantic-sub">无需设置复杂条件，用一句话描述您要找的人</div><div className="flex gap-8 semantic-input"><Input value={semantic} onChange={(e) => setSemantic(e.target.value)} onPressEnter={semanticSearch} prefix={<RobotOutlined style={{ color: '#6981ce' }}/>} placeholder="例如：查找有 3 年以上 AI 项目经验、擅长 RAG 的技术骨干"/><Button type="primary" loading={semanticLoading} icon={<SearchOutlined/>} onClick={semanticSearch}>智能搜索</Button></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input.Search allowClear value={keyword} onChange={(e) => setKeyword(e.target.value)} onSearch={() => load()} placeholder="搜索姓名、职位、技能" prefix={<SearchOutlined/>}/><Select allowClear placeholder="学历" style={{ width: 120 }} onChange={(v) => setFilters((f) => ({ ...f, education: v }))} options={['博士','硕士','本科','大专'].map((v) => ({ value: v, label: v }))}/><Select allowClear placeholder="人才状态" style={{ width: 125 }} onChange={(v) => setFilters((f) => ({ ...f, status: v }))} options={[{ value: 'active', label: '在职' }, { value: 'reserve', label: '人才储备' }, { value: 'left', label: '离职' }]}/><Select allowClear placeholder="所属部门" style={{ width: 145 }} options={['技术研发部','人力资源部','数据智能部','市场品牌部'].map((v) => ({ value: v, label: v }))}/><Button type="primary" ghost onClick={() => load()}>查询</Button><Button onClick={() => { setKeyword(''); setFilters({}); setData(demoTalents); }}>重置</Button><div className="filter-spacer"/><span className="muted" style={{ fontSize: 11 }}>数据更新于 10:30</span></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">人才列表<span className="table-count">共 {total} 条记录</span></div><Button size="small" icon={<RobotOutlined/>} onClick={() => message.success('已启动批量画像更新任务')}>批量更新画像</Button></div><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={false} scroll={{ x: 1200 }}/><div className="table-footer"><Pagination total={total} pageSize={20} showSizeChanger={false} showTotal={(n) => `共 ${n} 条`}/></div></div>
    <Drawer width={590} open={!!selected} onClose={() => setSelected(null)} title="人才数字画像" extra={<Button type="primary" size="small" icon={<ThunderboltOutlined/>} onClick={() => message.success('AI 画像更新任务已提交')}>更新画像</Button>}>
      {selected && <><div className="detail-hero"><PersonAvatar name={selected.name} size={68} className="detail-avatar"/><div style={{ flex: 1 }}><div className="detail-name">{selected.name} <PotentialTag level={selected.potential_level}/></div><div className="detail-meta">{selected.current_position} · {selected.organization_name}</div><div style={{ marginTop: 9 }}><SkillTags tags={selected.skill_tags} max={4}/></div></div><div style={{ textAlign: 'right' }}><div className="score-text" style={{ fontSize: 26 }}>{selected.match_score}</div><div className="muted" style={{ fontSize: 10 }}>综合评分</div></div></div>
        <div className="drawer-section-title">AI 画像摘要 <Tag color="blue" style={{ fontSize: 9 }}>由 Talent Agent 生成</Tag></div><div className="portrait-summary">{selected.portrait_summary}</div>
        <div className="drawer-section-title">能力维度分析</div><div className="flex items-center"><Chart height={235} option={{ radar: { indicator: portraitDimensions.map((d) => ({ name: d.name, max: 100 })), radius: '68%', axisName: { color: '#758096', fontSize: 11 }, splitArea: { areaStyle: { color: ['#fafbff','#f4f6fc'] } }, splitLine: { lineStyle: { color: '#e0e6f1' } }, axisLine: { lineStyle: { color: '#e0e6f1' } } }, series: [{ type: 'radar', data: [{ value: portraitDimensions.map((d) => d.value), areaStyle: { color: 'rgba(70,105,225,.22)' }, lineStyle: { color: '#496de0', width: 2 }, itemStyle: { color: '#496de0' } }] }] }}/><div style={{ flex: 1 }}>{portraitDimensions.slice(0,4).map((d) => <div key={d.name} style={{ marginBottom: 13 }}><div className="flex justify-between" style={{ fontSize: 11, marginBottom: 4 }}><span>{d.name}</span><b>{d.value}</b></div><Progress percent={d.value} showInfo={false} size="small" strokeColor="#5574df"/></div>)}</div></div>
        <div className="drawer-section-title">档案信息</div><div className="description-list">{[['性别',selected.gender === 'male' ? '男' : '女'],['手机号码',selected.mobile],['最高学历',`${selected.education} · ${selected.school}`],['专业',selected.major],['工作年限',`${selected.work_years} 年`],['电子邮箱',selected.email || '—']].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v}</div></div>)}</div>
        <div className="drawer-section-title">AI 发展建议</div><div className="portrait-summary" style={{ borderLeftColor: '#24a082', background: '#f3faf8' }}>建议作为「技术专家」序列重点培养对象，参与跨部门 AI 创新项目，补充技术团队管理与业务战略规划经验。</div>
      </>}
    </Drawer>
    <Modal title={editing ? '编辑人才档案' : '新建人才档案'} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={save} confirmLoading={saving} width={700} okText="保存档案">
      <Form form={form} layout="vertical"><Row gutter={16}><Col span={12}><Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}><Input/></Form.Item></Col><Col span={12}><Form.Item name="mobile" label="手机号" rules={[{ required: true, message: '请输入手机号' }]}><Input/></Form.Item></Col><Col span={8}><Form.Item name="gender" label="性别"><Select options={[{ value: 'male', label: '男' },{ value: 'female', label: '女' },{ value: 'unknown', label: '未知' }]}/></Form.Item></Col><Col span={8}><Form.Item name="education" label="最高学历"><Select options={['博士','硕士','本科','大专'].map((v) => ({ value: v, label: v }))}/></Form.Item></Col><Col span={8}><Form.Item name="work_years" label="工作年限"><Input type="number" suffix="年"/></Form.Item></Col><Col span={12}><Form.Item name="school" label="毕业院校"><Input/></Form.Item></Col><Col span={12}><Form.Item name="major" label="专业"><Input/></Form.Item></Col><Col span={12}><Form.Item name="current_position" label="当前职位"><Input/></Form.Item></Col><Col span={12}><Form.Item name="organization_name" label="所属部门"><Select options={['技术研发部','人力资源部','数据智能部','市场品牌部'].map((v) => ({ value: v, label: v }))}/></Form.Item></Col><Col span={24}><Form.Item name="skill_tags" label="技能标签"><Select mode="tags" placeholder="输入技能后回车"/></Form.Item></Col></Row></Form>
    </Modal>
  </>;
}
