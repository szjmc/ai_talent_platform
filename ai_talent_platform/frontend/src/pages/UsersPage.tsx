import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Col, Drawer, Dropdown, Form, Input, Modal, Pagination, Row, Select, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, EditOutlined, KeyOutlined, LockOutlined, MoreOutlined, PlusOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons';
import { PageHeader, PersonAvatar, StatusBadge } from '../components/Common';
import { flattenOrgs, organizationsApi, usersApi } from '../services/platform';
import { useAuth } from '../store/AuthContext';
import { formatApiError } from '../services/api';
import { formatDate } from '../utils';
import type { OrgNode, User } from '../types';

const roles = [{ value: 'super_admin', label: '超级管理员', color: 'red' },{ value: 'admin', label: '系统管理员', color: 'orange' },{ value: 'hr', label: '人力资源', color: 'blue' },{ value: 'manager', label: '部门管理者', color: 'purple' },{ value: 'employee', label: '普通员工', color: 'default' }];
export default function UsersPage() {
  const [data, setData] = useState<User[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const [organizations, setOrganizations] = useState<OrgNode[]>([]);
  const [open, setOpen] = useState(false); const [editing, setEditing] = useState<User | null>(null); const [selected, setSelected] = useState<User | null>(null); const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState(''); const [active, setActive] = useState<boolean>(); const [orgId, setOrgId] = useState<number>(); const [page, setPage] = useState(1);
  const { user: currentUser } = useAuth(); const { message, modal } = App.useApp(); const [form] = Form.useForm();
  const load = useCallback(async (p = page) => {
    setLoading(true); setLoadError('');
    try { const res = await usersApi.list({ page: p, page_size: 20, keyword: keyword || undefined, is_active: active, organization_id: orgId }); setData(res.items || []); setTotal(res.total || 0); }
    catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, [page, keyword, active, orgId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { organizationsApi.tree().then(setOrganizations).catch(() => setOrganizations([])); }, []);
  const orgOptions = flattenOrgs(organizations).map((o) => ({ value: o.id, label: o.name }));
  const openEdit = (u?: User) => { setEditing(u || null); form.setFieldsValue(u ? { ...u, role_codes: u.roles.map((r) => r.code) } : { is_active: true, role_codes: ['employee'] }); setOpen(true); };
  const save = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      if (editing) { const { role_codes, username, password, ...rest } = values; await usersApi.update(editing.id, rest); await usersApi.assignRoles(editing.id, role_codes); }
      else await usersApi.create(values);
      message.success(editing ? '用户信息及角色权限已更新' : '用户创建成功'); setOpen(false); await load();
    } catch (error) { message.error(formatApiError(error)); } finally { setSaving(false); }
  };
  const toggle = (u: User) => modal.confirm({ title: `确认${u.is_active ? '停用' : '启用'}账号「${u.real_name || u.username}」？`, content: u.is_active ? '停用后该用户将无法登录平台。' : '启用后用户可正常登录并访问授权功能。', onOk: async () => { try { await usersApi.status(u.id, !u.is_active, u.is_active ? '管理员停用' : '管理员启用'); message.success('账号状态已更新'); await load(); } catch (error) { message.error(formatApiError(error)); } } });
  const resetPassword = (u: User) => { let password = ''; modal.confirm({ title: `重置「${u.real_name || u.username}」的登录密码`, content: <Input.Password placeholder="请输入新密码（至少 8 位，含字母和数字）" onChange={(e) => { password = e.target.value; }} style={{ marginTop: 15 }}/>, onOk: async () => { if (!/^(?=.*[A-Za-z])(?=.*\d).{8,64}$/.test(password)) { message.error('密码至少 8 位，且必须包含字母和数字'); throw new Error('invalid'); } try { await usersApi.resetPassword(u.id, password); message.success('密码已重置，账号锁定状态已解除'); } catch (error) { message.error(formatApiError(error)); } } }); };
  const remove = (u: User) => modal.confirm({ title: `确认删除用户「${u.real_name || u.username}」？`, content: '删除为逻辑删除，账号将无法登录且历史操作日志保留。', okButtonProps: { danger: true }, onOk: async () => { try { await usersApi.remove(u.id); message.success('用户已删除'); await load(); } catch (error) { message.error(formatApiError(error)); } } });
  const columns: ColumnsType<User> = [
    { title: '用户', dataIndex: 'real_name', width: 220, render: (_, u) => <div className="talent-cell"><PersonAvatar name={u.real_name || u.username} size={37}/><div><div className="talent-cell-name">{u.real_name || u.username}{u.is_superuser && <SafetyCertificateOutlined style={{ color: '#df9a31', marginLeft: 6 }}/>}</div><div className="talent-cell-role">@{u.username}</div></div></div> },
    { title: '联系方式', dataIndex: 'mobile', width: 200, render: (_, u) => <div>{u.mobile || '—'}<div className="talent-cell-role">{u.email || '未填写邮箱'}</div></div> },
    { title: '所属部门 / 职务', dataIndex: 'organization_name', width: 190, render: (_, u) => <div>{u.organization_name || '—'}<div className="talent-cell-role">{u.position_title || '未设置职务'}</div></div> },
    { title: '角色权限', dataIndex: 'roles', render: (rs: User['roles']) => (rs || []).map((r) => <Tag key={r.code} color={roles.find((x) => x.value === r.code)?.color}>{r.name}</Tag>) },
    { title: '状态', dataIndex: 'is_active', width: 80, render: (v) => <StatusBadge status={v ? 'enabled' : 'disabled'}/> },
    { title: '最近登录', dataIndex: 'last_login_at', width: 145, render: (v) => formatDate(v, true) },
    { title: '操作', fixed: 'right', width: 130, render: (_, u) => <div><Button type="link" size="small" onClick={() => setSelected(u)}>详情</Button><Button type="text" size="small" icon={<EditOutlined/>} onClick={() => openEdit(u)}/><Dropdown menu={{ items: [{ key: 'reset', icon: <KeyOutlined/>, label: '重置密码', onClick: () => resetPassword(u) },{ key: 'status', icon: <LockOutlined/>, label: u.is_active ? '停用账号' : '启用账号', onClick: () => toggle(u) },{ type: 'divider' },{ key: 'delete', icon: <DeleteOutlined/>, label: '删除用户', danger: true, disabled: u.id === currentUser?.id, onClick: () => remove(u) }] }}><Button type="text" size="small" icon={<MoreOutlined/>}/></Dropdown></div> },
  ];
  return <>
    <PageHeader title="用户与权限" description="基于 RBAC 的细粒度访问控制，保障每个人只看见该看见的数据" actions={<Button type="primary" icon={<PlusOutlined/>} onClick={() => openEdit()}>新建用户</Button>}/>
    {loadError && <Alert type="error" showIcon message="用户列表加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">平台用户</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">人</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页启用</div><div className="metric-strip-value">{data.filter((u) => u.is_active).length}<span className="metric-strip-extra">人</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">角色类型</div><div className="metric-strip-value">{roles.length}<span className="metric-strip-extra">类内置角色</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前筛选</div><div className="metric-strip-value">{active == null ? '全部' : active ? '启用' : '停用'}<span className="metric-strip-extra">账号状态</span></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={() => { setPage(1); void load(1); }} placeholder="搜索账号、姓名、手机号或邮箱" style={{ width: 290 }}/><Select placeholder="所属部门" allowClear style={{ width: 150 }} value={orgId} onChange={(v) => { setOrgId(v); setPage(1); }} options={orgOptions}/><Select placeholder="账号状态" allowClear style={{ width: 120 }} value={active} onChange={(v) => { setActive(v); setPage(1); }} options={[{ value: true, label: '已启用' },{ value: false, label: '已停用' }]}/><Button type="primary" ghost onClick={() => { setPage(1); void load(1); }}>查询</Button><Button onClick={() => { setKeyword(''); setActive(undefined); setOrgId(undefined); setPage(1); }}>重置</Button></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">用户列表<span className="table-count">共 {total} 个账号</span></div><span className="muted" style={{ fontSize: 11 }}><SafetyCertificateOutlined className="text-success"/> 全链路 RBAC 权限校验</span></div><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={false} scroll={{ x: 1250 }}/><div className="table-footer"><Pagination current={page} total={total} pageSize={20} onChange={setPage} showSizeChanger={false}/></div></div>
    <Modal title={editing ? '编辑用户与角色' : '新建用户'} open={open} onCancel={() => setOpen(false)} onOk={save} confirmLoading={saving} width={650} okText="保存"><Form form={form} layout="vertical"><Row gutter={16}><Col span={12}><Form.Item name="username" label="登录账号" rules={[{ required: true, pattern: /^[a-zA-Z0-9_]{4,32}$/, message: '4–32 位字母、数字或下划线' }]}><Input disabled={!!editing}/></Form.Item></Col>{!editing && <Col span={12}><Form.Item name="password" label="初始密码" rules={[{ required: true, pattern: /^(?=.*[A-Za-z])(?=.*\d).{8,64}$/, message: '至少 8 位且包含字母和数字' }]}><Input.Password/></Form.Item></Col>}<Col span={12}><Form.Item name="real_name" label="姓名" rules={[{ required: true }]}><Input/></Form.Item></Col><Col span={12}><Form.Item name="mobile" label="手机号" rules={[{ pattern: /^1[3-9]\d{9}$/, message: '请输入正确的 11 位手机号' }]}><Input/></Form.Item></Col><Col span={12}><Form.Item name="email" label="邮箱"><Input/></Form.Item></Col><Col span={12}><Form.Item name="organization_id" label="所属部门"><Select allowClear options={orgOptions}/></Form.Item></Col><Col span={12}><Form.Item name="position_title" label="职务"><Input/></Form.Item></Col><Col span={12}><Form.Item name="role_codes" label="角色" rules={[{ required: true }]}><Select mode="multiple" options={roles}/></Form.Item></Col><Col span={12}><Form.Item name="is_active" label="启用状态" valuePropName="checked"><Switch/></Form.Item></Col></Row></Form></Modal>
    <Drawer title="用户详情" width={520} open={!!selected} onClose={() => setSelected(null)}>{selected && <div className="description-list">{[['登录账号',selected.username],['姓名',selected.real_name],['手机号',selected.mobile],['邮箱',selected.email],['所属部门',selected.organization_name],['职务',selected.position_title],['角色',selected.roles.map((r) => r.name).join('、')],['最近登录',formatDate(selected.last_login_at, true)],['创建时间',formatDate(selected.created_at, true)]].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v || '—'}</div></div>)}</div>}</Drawer>
  </>;
}
