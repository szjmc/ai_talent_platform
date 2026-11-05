import { useEffect, useMemo, useState } from 'react';
import { Avatar, Badge, Dropdown, Input, Layout, Menu, Modal, Tooltip } from 'antd';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  ApartmentOutlined, AuditOutlined, BarChartOutlined, BellOutlined, BookOutlined, CommentOutlined,
  DashboardOutlined, DatabaseOutlined, FileSearchOutlined, FileTextOutlined, FolderOpenOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, NodeIndexOutlined, QuestionCircleOutlined, RobotOutlined,
  SearchOutlined, SettingOutlined, SolutionOutlined, TeamOutlined, ThunderboltOutlined, UserOutlined,
  DownOutlined, LogoutOutlined, LockOutlined, CheckCircleFilled,
} from '@ant-design/icons';
import { useAuth } from '../store/AuthContext';

const { Sider, Header, Content } = Layout;
const menuGroups = [
  { type: 'group' as const, label: '工作空间', children: [
    { key: '/dashboard', icon: <DashboardOutlined/>, label: '人才驾驶舱' },
    { key: '/assistant', icon: <RobotOutlined/>, label: 'AI 智能助手' },
  ]},
  { type: 'group' as const, label: '人才管理', children: [
    { key: '/talents', icon: <TeamOutlined/>, label: '人才档案' },
    { key: '/positions', icon: <SolutionOutlined/>, label: '岗位管理' },
    { key: '/resumes', icon: <FileSearchOutlined/>, label: '简历解析' },
    { key: '/matchings', icon: <NodeIndexOutlined/>, label: '人岗匹配' },
  ]},
  { type: 'group' as const, label: '人才发展', children: [
    { key: '/assessments', icon: <FileTextOutlined/>, label: '智能测评' },
    { key: '/trainings', icon: <BookOutlined/>, label: '培训发展' },
  ]},
  { type: 'group' as const, label: '智能应用', children: [
    { key: '/knowledge', icon: <DatabaseOutlined/>, label: '知识库' },
    { key: '/analytics', icon: <BarChartOutlined/>, label: '数据洞察' },
  ]},
  { type: 'group' as const, label: '系统管理', children: [
    { key: '/organizations', icon: <ApartmentOutlined/>, label: '组织架构' },
    { key: '/users', icon: <UserOutlined/>, label: '用户与权限' },
    { key: '/files', icon: <FolderOpenOutlined/>, label: '文件中心' },
    { key: '/audit', icon: <AuditOutlined/>, label: '操作审计' },
  ]},
];
const permissionByPath: Record<string, string> = {
  '/assistant': 'agent:use', '/talents': 'talent:read', '/positions': 'position:read', '/resumes': 'talent:read',
  '/matchings': 'matching:read', '/assessments': 'assessment:read', '/trainings': 'training:read',
  '/knowledge': 'knowledge:read', '/analytics': 'analytics:read', '/organizations': 'organization:read',
  '/users': 'user:read', '/audit': 'audit:read',
};
const searchItems = [
  { title: '人才驾驶舱', path: '/dashboard', type: '页面' }, { title: '人才档案', path: '/talents', type: '页面' },
  { title: '岗位管理', path: '/positions', type: '页面' }, { title: '人岗匹配', path: '/matchings', type: '页面' },
  { title: '数据洞察', path: '/analytics', type: '页面' }, { title: '用户与权限', path: '/users', type: '页面' },
  { title: '张明远 · 高级算法工程师', path: '/talents?keyword=张明远', type: '人才' },
  { title: '高级后端工程师 · RD-BE-026', path: '/positions', type: '岗位' },
];

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const { user, logout, demo, hasPermission } = useAuth();
  const location = useLocation(); const navigate = useNavigate();
  const visibleMenu = useMemo(() => menuGroups.map((group) => ({ ...group, children: group.children.filter((item) => !permissionByPath[item.key] || hasPermission(permissionByPath[item.key])) })).filter((group) => group.children.length), [hasPermission]);
  const selected = '/' + (location.pathname.split('/')[1] || 'dashboard');
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); setSearchOpen(true); } };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, []);
  const results = useMemo(() => searchItems.filter((x) => x.title.toLowerCase().includes(query.toLowerCase())), [query]);
  const notifications = [
    { title: '简历解析完成', text: '张明远的简历已成功解析，人才画像已更新。', time: '5 分钟前' },
    { title: '关键岗位人才缺口提醒', text: 'AI 算法专家岗位当前仅有 3 位高匹配候选人。', time: '30 分钟前' },
    { title: '测评即将截止', text: '管理者领导力综合测评将于 11 月 8 日截止。', time: '2 小时前' },
  ];
  return <Layout className="app-layout">
    <Sider className="app-sider" width={232} collapsedWidth={80} collapsed={collapsed} trigger={null}>
      <div className="brand"><div className="brand-mark"><i/><i/><i/><i/></div>{!collapsed && <div><div className="brand-name">智才云</div><div className="brand-sub">TALENTOS · AI</div></div>}</div>
      <div className="side-menu-wrap"><Menu className="side-menu" mode="inline" items={visibleMenu} selectedKeys={[selected]} onClick={({ key }) => { if (key === '/talents') { if (location.pathname === '/talents') window.dispatchEvent(new Event('talentos:archive-fullscreen')); else navigate(key, { state: { fullscreen: true } }); } else navigate(key); }} inlineCollapsed={collapsed}/></div>
      <div className="sider-bottom"><div className="system-status"><span className="status-dot"/>{demo ? '演示环境运行中' : '系统服务运行正常'}</div><small>版本 v1.0.0 · 安全连接</small></div>
    </Sider>
    <Layout className={`main-shell ${collapsed ? 'collapsed' : ''}`}>
      <Header className="top-header">
        <button className="collapse-btn" onClick={() => setCollapsed(!collapsed)}>{collapsed ? <MenuUnfoldOutlined/> : <MenuFoldOutlined/>}</button>
        <div className="top-divider"/>
        <div className="global-search" onClick={() => setSearchOpen(true)}><Input readOnly prefix={<SearchOutlined style={{ color: '#98a2b3' }}/>} placeholder="搜索人才、岗位或功能" suffix={<span className="search-shortcut">Ctrl K</span>}/></div>
        <div className="header-right">
          <Tooltip title="打开 AI 智能助手"><button className="ant-btn ant-btn-primary ai-entry" onClick={() => navigate('/assistant')}><ThunderboltOutlined/> AI 智能助手</button></Tooltip>
          <Tooltip title="帮助中心"><button className="header-icon-btn"><QuestionCircleOutlined/></button></Tooltip>
          <Dropdown trigger={['click']} placement="bottomRight" dropdownRender={() => <div style={{ background: '#fff', borderRadius: 10, boxShadow: '0 8px 30px rgba(30,45,80,.14)', padding: 15 }}><div className="flex justify-between items-center mb-16"><b>消息通知</b><span className="text-primary" style={{ fontSize: 11 }}>全部已读</span></div>{notifications.map((n) => <div className="notification-item" key={n.title}><div className="notification-title"><span>{n.title}</span><Badge status="processing"/></div><div className="notification-text">{n.text}</div><div className="notification-time">{n.time}</div></div>)}</div>}>
            <Badge count={3} size="small" offset={[-3, 4]}><button className="header-icon-btn"><BellOutlined/></button></Badge>
          </Dropdown>
          <Dropdown menu={{ items: [
            { key: 'profile', icon: <UserOutlined/>, label: '个人中心', onClick: () => navigate('/profile') },
            { key: 'password', icon: <LockOutlined/>, label: '修改密码', onClick: () => navigate('/profile?tab=password') },
            { key: 'settings', icon: <SettingOutlined/>, label: '系统设置' },
            { type: 'divider' },
            { key: 'logout', icon: <LogoutOutlined/>, label: '退出登录', danger: true, onClick: () => logout().then(() => navigate('/login')) },
          ] }} placement="bottomRight">
            <div className="header-user"><Avatar size={34} style={{ background: '#e8edfb', color: '#3e5da9', fontWeight: 600 }}>{user?.real_name?.slice(-2) || '用户'}</Avatar><div><div className="header-user-name">{user?.real_name || user?.username}</div><div className="header-user-role">{user?.position_title || (demo ? '平台管理员' : user?.roles?.[0])}</div></div><DownOutlined style={{ fontSize: 9, color: '#9aa3b3' }}/></div>
          </Dropdown>
        </div>
      </Header>
      <Content><div className="page-content"><Outlet/></div></Content>
    </Layout>
    <Modal open={searchOpen} onCancel={() => { setSearchOpen(false); setQuery(''); }} footer={null} closable={false} width={580} styles={{ content: { padding: 14 } }}>
      <Input autoFocus size="large" prefix={<SearchOutlined/>} placeholder="搜索人才、岗位或功能…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ marginBottom: 12 }}/>
      <div style={{ maxHeight: 380, overflow: 'auto' }}>{results.map((item) => <div key={item.title} onClick={() => { navigate(item.path); setSearchOpen(false); setQuery(''); }} style={{ padding: '12px 10px', borderRadius: 7, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10 }} className="search-result"><SearchOutlined style={{ color: '#9aa3b3' }}/><span>{item.title}</span><span style={{ marginLeft: 'auto', color: '#98a2b3', fontSize: 11 }}>{item.type}</span></div>)}{results.length === 0 && <div className="empty-hint">未找到相关内容</div>}</div>
      <div style={{ fontSize: 10, color: '#a1a9b7', padding: '10px 8px 0', borderTop: '1px solid #edf0f4', marginTop: 8 }}><CheckCircleFilled style={{ color: '#20a478', marginRight: 5 }}/>全局智能搜索 · 输入关键词快速定位</div>
    </Modal>
  </Layout>;
}
