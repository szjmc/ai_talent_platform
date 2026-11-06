import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Col, Drawer, Form, Input, InputNumber, Modal, Pagination, Progress, Row, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, EditOutlined, FileTextOutlined, NodeIndexOutlined, PlusOutlined, RobotOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { PageHeader, SkillTags, StatusBadge } from '../components/Common';
import { flattenOrgs, organizationsApi, positionsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatDate } from '../utils';
import type { OrgNode, Position } from '../types';

const levelMap: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级', expert: '专家' };
const BACKEND_PENDING = '后端岗位 CRUD 接口尚未实现（当前 OpenAPI 仅保留占位），无法执行该写操作。';

export default function PositionsPage() {
  const [data, setData] = useState<Position[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const [selected, setSelected] = useState<Position | null>(null);
  const [open, setOpen] = useState(false); const [editing, setEditing] = useState<Position | null>(null); const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState(''); const [status, setStatus] = useState<string>(); const [orgId, setOrgId] = useState<number>();
  const [organizations, setOrganizations] = useState<OrgNode[]>([]); const [form] = Form.useForm();
  const { message, modal } = App.useApp(); const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const result = await positionsApi.listPlaceholder();
      const items = (result.items || []) as Position[];
      setData(items); setTotal(result.total || items.length);
    } catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); organizationsApi.tree().then(setOrganizations).catch(() => setOrganizations([])); }, [load]);
  const orgOptions = useMemo(() => flattenOrgs(organizations).map((item) => ({ value: item.id, label: item.name })), [organizations]);
  const filtered = data.filter((p) => (!keyword || `${p.title}${p.code}${(p.skill_tags || []).join('')}`.toLowerCase().includes(keyword.toLowerCase())) && (!status || p.status === status) && (!orgId || p.organization_id === orgId));
  const openEdit = (p?: Position) => { setEditing(p || null); form.setFieldsValue(p || { level: 'middle', headcount: 1, status: 'open', skill_tags: [] }); setOpen(true); };
  const save = async () => {
    await form.validateFields(); setSaving(true);
    try { message.error(BACKEND_PENDING); } finally { setSaving(false); }
  };
  const toggleStatus = async (p: Position) => {
    modal.confirm({ title: `确认${p.status === 'open' ? '暂停' : '恢复'}招聘「${p.title}」？`, onOk: async () => { message.error(BACKEND_PENDING); } });
  };
  const columns: ColumnsType<Position> = [
    { title: '岗位名称 / 编码', dataIndex: 'title', width: 240, render: (_, r) => <div className="position-title-cell">{r.title}<Tag style={{ marginLeft: 7, fontSize: 9 }} color={r.level === 'expert' ? 'purple' : 'blue'}>{levelMap[r.level] || r.level}</Tag><div className="position-code">{r.code}</div></div> },
    { title: '所属部门', dataIndex: 'organization_name', width: 135, render: (v) => v || '—' },
    { title: '核心技能要求', dataIndex: 'skill_tags', render: (v) => <SkillTags tags={v || []} max={3}/> },
    { title: '薪资范围', width: 130, render: (_, r) => <span className="salary">{r.salary_min != null && r.salary_max != null ? `${r.salary_min / 1000}K–${r.salary_max / 1000}K` : '—'}</span> },
    { title: '编制 / 到位', width: 155, render: (_, r) => <div className="headcount-progress"><Progress percent={r.headcount ? Math.round((r.headcount_filled || 0) / r.headcount * 100) : 0} showInfo={false} size="small" strokeColor="#5273df"/><span className="nowrap muted">{r.headcount_filled ?? 0}/{r.headcount ?? 0}</span></div> },
    { title: '状态', dataIndex: 'status', width: 85, render: (v) => <StatusBadge status={v}/> },
    { title: '发布日期', dataIndex: 'publish_at', width: 110, render: (v) => formatDate(v) },
    { title: '操作', width: 180, fixed: 'right', render: (_, r) => <div><Button type="link" size="small" onClick={() => navigate('/matchings')} icon={<NodeIndexOutlined/>}>匹配</Button><Button type="text" size="small" icon={<EditOutlined/>} onClick={() => openEdit(r)}/><Button type="text" size="small" icon={<FileTextOutlined/>} onClick={() => setSelected(r)}/></div> },
  ];
  return <>
    <PageHeader title="岗位管理" description="构建标准岗位能力模型，让每一个岗位需求清晰、可衡量、可匹配" actions={<><Button icon={<RobotOutlined/>} onClick={() => message.info('请先选择岗位，后端岗位画像接口支持 JD 解析与确认')}>AI 解析 JD</Button><Button type="primary" icon={<PlusOutlined/>} onClick={() => openEdit()}>新建岗位</Button></>}/>
    {loadError && <Alert type="warning" showIcon message="岗位列表暂不可用" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">岗位总数</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">在招岗位</div><div className="metric-strip-value">{data.filter((p) => p.status === 'open').length}<span className="metric-strip-extra">当前页</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">已暂停</div><div className="metric-strip-value">{data.filter((p) => p.status === 'paused').length}<span className="metric-strip-extra">当前页</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">已关闭</div><div className="metric-strip-value">{data.filter((p) => p.status === 'closed').length}<span className="metric-strip-extra">当前页</span></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} placeholder="搜索岗位名称、编码或技能" style={{ width: 285 }} value={keyword} onChange={(e) => setKeyword(e.target.value)}/><Select placeholder="岗位状态" allowClear style={{ width: 130 }} value={status} onChange={setStatus} options={[{ value: 'open', label: '招聘中' },{ value: 'paused', label: '已暂停' },{ value: 'closed', label: '已关闭' }]}/><Select placeholder="岗位层级" allowClear style={{ width: 130 }} options={Object.entries(levelMap).map(([value,label]) => ({ value,label }))}/><Select placeholder="所属部门" allowClear style={{ width: 150 }} value={orgId} onChange={setOrgId} options={orgOptions}/><div className="filter-spacer"/><Button onClick={() => { setKeyword(''); setStatus(undefined); setOrgId(undefined); }}>重置</Button></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">岗位列表<span className="table-count">共 {filtered.length} 个岗位</span></div></div><Table rowKey="id" dataSource={filtered} columns={columns} loading={loading} pagination={false} scroll={{ x: 1250 }} locale={{ emptyText: loadError ? '后端岗位接口暂不可用' : '暂无岗位数据' }}/><div className="table-footer"><Pagination total={total} pageSize={20} showSizeChanger={false}/></div></div>
    <Drawer open={!!selected} onClose={() => setSelected(null)} width={540} title="岗位详情" extra={selected && <Button size="small" onClick={() => toggleStatus(selected)}>{selected.status === 'open' ? '暂停招聘' : '恢复招聘'}</Button>}>
      {selected && <><div className="detail-hero"><div style={{ width: 52, height: 52, borderRadius: 12, background: '#e8efff', color: '#4267d8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 25 }}><FileTextOutlined/></div><div style={{ flex: 1 }}><div className="detail-name" style={{ fontSize: 19 }}>{selected.title}</div><div className="detail-meta">{selected.organization_name || '—'} · {selected.code}</div></div><StatusBadge status={selected.status}/></div>
      <div className="drawer-section-title">岗位说明</div><p style={{ fontSize: 13, lineHeight: 24, color: '#5f6b7e' }}>{selected.description || '—'}</p><div className="drawer-section-title">核心技能</div><SkillTags tags={selected.skill_tags || []} max={10}/>
      <div className="drawer-section-title">任职要求</div><div className="description-list"><div><div className="description-item-label">最低学历</div><div className="description-item-value">{selected.requirements?.education || '—'}</div></div><div><div className="description-item-label">工作经验</div><div className="description-item-value">{selected.requirements?.experience_years != null ? `${selected.requirements.experience_years} 年以上` : '—'}</div></div><div><div className="description-item-label">岗位层级</div><div className="description-item-value">{levelMap[selected.level] || selected.level}</div></div><div><div className="description-item-label">薪资范围</div><div className="description-item-value">{selected.salary_min != null && selected.salary_max != null ? `${selected.salary_min / 1000}K–${selected.salary_max / 1000}K` : '—'}</div></div></div>
      <Button block type="primary" style={{ marginTop: 22 }} icon={<NodeIndexOutlined/>} onClick={() => navigate('/matchings')}>为该岗位匹配人才</Button></>}
    </Drawer>
    <Modal title={editing ? '编辑岗位' : '新建岗位'} open={open} onCancel={() => setOpen(false)} onOk={save} confirmLoading={saving} width={700} okText="保存岗位"><Form form={form} layout="vertical"><Row gutter={16}><Col span={12}><Form.Item name="title" label="岗位名称" rules={[{ required: true }]}><Input placeholder="例如：高级后端工程师"/></Form.Item></Col><Col span={12}><Form.Item name="code" label="岗位编码" rules={[{ required: true }]}><Input disabled={!!editing} placeholder="例如：RD-BE-026"/></Form.Item></Col><Col span={8}><Form.Item name="organization_id" label="所属部门"><Select options={orgOptions}/></Form.Item></Col><Col span={8}><Form.Item name="level" label="岗位层级"><Select options={Object.entries(levelMap).map(([value,label]) => ({ value,label }))}/></Form.Item></Col><Col span={8}><Form.Item name="headcount" label="招聘编制"><InputNumber min={1} style={{ width: '100%' }}/></Form.Item></Col><Col span={12}><Form.Item name="salary_min" label="最低月薪（元）"><InputNumber min={0} style={{ width: '100%' }}/></Form.Item></Col><Col span={12}><Form.Item name="salary_max" label="最高月薪（元）"><InputNumber min={0} style={{ width: '100%' }}/></Form.Item></Col><Col span={24}><Form.Item name="skill_tags" label="核心技能"><Select mode="tags" placeholder="输入技能后回车"/></Form.Item></Col><Col span={24}><Form.Item name="description" label="岗位说明书"><Input.TextArea rows={5} placeholder="输入岗位职责与任职要求，保存后可使用 AI 自动解析"/></Form.Item></Col></Row></Form></Modal>
  </>;
}
