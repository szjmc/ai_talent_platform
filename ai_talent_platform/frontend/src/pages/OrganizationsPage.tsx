import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Empty, Form, Input, InputNumber, Modal, Select, Spin, Switch, Tree } from 'antd';
import type { DataNode } from 'antd/es/tree';
import { ApartmentOutlined, DeleteOutlined, EditOutlined, MoreOutlined, PlusOutlined, SearchOutlined, TeamOutlined } from '@ant-design/icons';
import { PageHeader } from '../components/Common';
import { flattenOrgs, organizationsApi, usersApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatDate } from '../utils';
import type { OrgNode, User } from '../types';

export default function OrganizationsPage() {
  const [tree, setTree] = useState<OrgNode[]>([]); const [selected, setSelected] = useState<OrgNode | null>(null);
  const [loading, setLoading] = useState(false); const [loadError, setLoadError] = useState('');
  const [open, setOpen] = useState(false); const [editing, setEditing] = useState<OrgNode | null>(null); const [saving, setSaving] = useState(false);
  const [users, setUsers] = useState<User[]>([]); const [search, setSearch] = useState('');
  const { message, modal } = App.useApp(); const [form] = Form.useForm();
  const all = useMemo(() => flattenOrgs(tree), [tree]);
  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try { const res = await organizationsApi.tree(); setTree(res); setSelected((current) => current ? flattenOrgs(res).find((n) => n.id === current.id) || res[0] || null : res[0] || null); }
    catch (error) { setLoadError(formatApiError(error)); setTree([]); setSelected(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); usersApi.list({ page: 1, page_size: 100 }).then((res) => setUsers(res.items || [])).catch(() => setUsers([])); }, [load]);
  const treeData = (nodes: OrgNode[]): DataNode[] => nodes.filter((n) => !search || n.name.includes(search) || n.code.includes(search)).map((n) => ({ key: n.id, title: <div className="flex items-center justify-between" style={{ paddingRight: 6 }}><span>{n.name}</span><span className="muted" style={{ fontSize: 10 }}>{n.user_count}</span></div>, children: treeData(n.children || []) }));
  const openEdit = (node?: OrgNode, parent?: OrgNode) => { setEditing(node || null); form.setFieldsValue(node || { parent_id: parent?.id || selected?.id, sort: 0, is_active: true }); setOpen(true); };
  const save = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      if (editing) { const { code, parent_id, ...rest } = values; await organizationsApi.update(editing.id, rest); }
      else await organizationsApi.create(values);
      message.success(editing ? '部门信息已更新' : '部门创建成功'); setOpen(false); await load();
    } catch (error) { message.error(formatApiError(error)); } finally { setSaving(false); }
  };
  const remove = () => { if (!selected) return; modal.confirm({ title: `确认删除「${selected.name}」？`, content: '仅当部门下无子部门及在职员工时可删除。', okButtonProps: { danger: true }, onOk: async () => { try { await organizationsApi.remove(selected.id); message.success('部门已删除'); await load(); } catch (error) { message.error(formatApiError(error)); } } }); };
  const move = () => { if (!selected) return; let parent: number | null | undefined; modal.confirm({ title: `移动「${selected.name}」`, content: <div style={{ marginTop: 15 }}><Select style={{ width: '100%' }} placeholder="选择新的上级部门（留空移动到根）" allowClear options={all.filter((n) => n.id !== selected.id).map((n) => ({ value: n.id, label: n.name }))} onChange={(v) => { parent = v ?? null; }}/></div>, onOk: async () => { if (parent === undefined) throw new Error('请选择上级部门'); try { await organizationsApi.move(selected.id, parent); message.success('部门已移动，层级路径已自动更新'); await load(); } catch (error) { message.error(formatApiError(error)); } } }); };

  return <>
    <PageHeader title="组织架构" description="清晰管理组织层级与人员编制，让人才数据在正确的组织中流动" actions={<Button type="primary" icon={<PlusOutlined/>} onClick={() => openEdit()}>新建部门</Button>}/>
    {loadError && <Alert type="error" showIcon message="组织架构加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
    <Spin spinning={loading}><div className="org-layout" style={{ display: 'grid', gridTemplateColumns: '330px minmax(0,1fr)', gap: 18 }}>
      <section className="card" style={{ padding: 17, minHeight: 650 }}><div className="flex justify-between items-center mb-16"><div className="section-card-title">部门结构</div><Button type="text" size="small" icon={<PlusOutlined/>} onClick={() => openEdit()}/></div><Input prefix={<SearchOutlined/>} placeholder="搜索部门" value={search} onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 15 }}/><Tree treeData={treeData(tree)} defaultExpandedKeys={[1,2,3]} selectedKeys={selected ? [selected.id] : []} onSelect={(keys) => { const n = all.find((x) => x.id === keys[0]); if (n) setSelected(n); }} blockNode showLine={{ showLeafIcon: false }}/></section>
      <section>{selected ? <><div className="card" style={{ padding: 24, marginBottom: 18 }}><div className="flex items-start justify-between"><div className="flex gap-16"><div style={{ width: 56, height: 56, borderRadius: 13, background: '#eaf0ff', color: '#4267da', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 27 }}><ApartmentOutlined/></div><div><h2 style={{ fontSize: 21, margin: '2px 0 7px' }}>{selected.name}</h2><div className="muted" style={{ fontSize: 12 }}>部门编码：{selected.code}<span style={{ margin: '0 10px' }}>·</span><span className={`status-badge ${selected.is_active ? 'success' : 'default'}`}>{selected.is_active ? '启用中' : '已停用'}</span></div></div></div><div className="flex gap-8"><Button icon={<EditOutlined/>} onClick={() => openEdit(selected)}>编辑</Button><Button icon={<MoreOutlined/>} onClick={move}>移动</Button><Button danger type="text" icon={<DeleteOutlined/>} onClick={remove}/></div></div><div className="metric-strip" style={{ margin: '25px 0 0' }}><div className="metric-strip-item"><div className="metric-strip-label">在职人数</div><div className="metric-strip-value">{selected.user_count}<span className="metric-strip-extra">人</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">下级部门</div><div className="metric-strip-value">{selected.children?.length || 0}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">组织层级</div><div className="metric-strip-value">{selected.level || 1}<span className="metric-strip-extra">级</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">排序号</div><div className="metric-strip-value">{selected.sort}<span className="metric-strip-extra">sort</span></div></div></div></div>
        <div className="card chart-card"><div className="card-header"><div><h3 className="section-card-title">部门信息</h3><div className="section-card-subtitle">组织主数据与负责人信息</div></div></div><div className="description-list" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>{[['部门全称',selected.name],['部门编码',selected.code],['组织层级',`${selected.level || 1} 级`],['部门负责人',selected.leader_name || '—'],['排序号',String(selected.sort)],['创建时间',formatDate(selected.created_at)]].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v}</div></div>)}</div><div className="drawer-section-title">下级组织</div>{selected.children?.length ? selected.children.map((n) => <div key={n.id} className="flex items-center" style={{ padding: '13px 4px', borderBottom: '1px solid #edf0f4' }}><ApartmentOutlined style={{ color: '#6580d6', marginRight: 12 }}/><div style={{ flex: 1 }}><div style={{ fontSize: 13, fontWeight: 550 }}>{n.name}</div><div className="talent-cell-role">{n.code}</div></div><TeamOutlined className="muted"/><span style={{ margin: '0 18px 0 5px', fontSize: 12 }}>{n.user_count} 人</span><Button type="link" size="small" onClick={() => setSelected(n)}>查看</Button></div>) : <div className="empty-hint">暂无下级部门<Button type="link" onClick={() => openEdit(undefined, selected)}>添加下级部门</Button></div>}</div></> : <Empty description="暂无组织数据" style={{ padding: 80 }}/>}</section>
    </div></Spin>
    <Modal open={open} title={editing ? '编辑部门' : '新建部门'} onCancel={() => setOpen(false)} onOk={save} confirmLoading={saving} okText="保存"><Form form={form} layout="vertical">{!editing && <Form.Item name="parent_id" label="上级部门"><Select allowClear placeholder="留空则创建根部门" options={all.map((n) => ({ value: n.id, label: n.name }))}/></Form.Item>}<Form.Item name="name" label="部门名称" rules={[{ required: true, min: 2, message: '请输入至少 2 个字符' }]}><Input/></Form.Item><Form.Item name="code" label="部门编码" rules={[{ required: true, min: 2 }]}><Input disabled={!!editing} placeholder="例如：RD_AI"/></Form.Item><Form.Item name="leader_id" label="部门负责人"><Select allowClear showSearch optionFilterProp="label" options={users.map((u) => ({ value: u.id, label: u.real_name || u.username }))}/></Form.Item><Form.Item name="sort" label="排序号"><InputNumber min={0} style={{ width: '100%' }}/></Form.Item><Form.Item name="is_active" label="启用状态" valuePropName="checked"><Switch/></Form.Item><Form.Item name="remark" label="备注"><Input.TextArea rows={3}/></Form.Item></Form></Modal>
  </>;
}
