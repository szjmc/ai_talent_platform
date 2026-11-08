import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Drawer, Form, Input, InputNumber, Modal, Select, Spin, Tag, Upload, message } from 'antd';
import { ApartmentOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined, GlobalOutlined, PlusOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { useLocation } from 'react-router-dom';
import ResumeCoverflow from '../components/talents/ResumeCoverflow';
import TalentMap from '../components/talents/TalentMap';
import TalentTable from '../components/talents/TalentTable';
import type { TalentFilterState } from '../components/talents/types';
import type { OrgNode, Talent, TalentPortrait } from '../types';
import { analyticsApi, flattenOrgs, orgNameMap, organizationsApi, resumesApi, talentsApi, withOrganizationNames } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText } from '../utils';
import '../components/talents/talents.css';

const emptyFilters: TalentFilterState = { keyword: '', status: undefined, organization_id: undefined, province: undefined };

/** 人才档案：列表、CRUD、画像与邀约均对接后端真实接口；页面交互保持不变。 */
export default function TalentsPage() {
  const location = useLocation();
  const [talents, setTalents] = useState<Talent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [filters, setFilters] = useState<TalentFilterState>(emptyFilters);
  const [fullscreen, setFullscreen] = useState(Boolean((location.state as { fullscreen?: boolean } | null)?.fullscreen));
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Talent | null>(null);
  const [saving, setSaving] = useState(false);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [deleting, setDeleting] = useState<Talent | null>(null);
  const [portraitTalent, setPortraitTalent] = useState<Talent | null>(null);
  const [portrait, setPortrait] = useState<TalentPortrait | null>(null);
  const [portraitLoading, setPortraitLoading] = useState(false);
  const [organizations, setOrganizations] = useState<OrgNode[]>([]);
  const [form] = Form.useForm();
  const [geoMap, setGeoMap] = useState<Record<number, Partial<Talent>>>({});
  useEffect(() => { void analyticsApi.talentGeo().then(rows => { if (rows && rows.length) setGeoMap(Object.fromEntries(rows.map(r => [r.talent_id, r]))); }).catch(() => {}); }, []);

  const loadOrganizations = useCallback(async () => {
    try { setOrganizations(await organizationsApi.tree()); } catch (error) { message.error(formatApiError(error)); }
  }, []);
  const loadTalents = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const names = orgNameMap(organizations);
      const result = await talentsApi.list({ page: 1, page_size: 100, keyword: filters.keyword || undefined, status: filters.status, organization_id: filters.organization_id });
      setTalents(withOrganizationNames(result.items || [], names)); setTotal(result.total || 0);
    } catch (error) { setLoadError(formatApiError(error)); setTalents([]); setTotal(0); }
    finally { setLoading(false); }
  }, [filters.keyword, filters.status, filters.organization_id, organizations]);
  useEffect(() => { void loadOrganizations(); }, [loadOrganizations]);
  useEffect(() => { void loadTalents(); }, [loadTalents]);

  useEffect(() => {
    const enter = () => setFullscreen(true);
    window.addEventListener('talentos:archive-fullscreen', enter);
    return () => window.removeEventListener('talentos:archive-fullscreen', enter);
  }, []);
  const updateFilters = useCallback((patch: Partial<TalentFilterState>) => setFilters(current => ({ ...current, ...patch })), []);
  const mapTalents = useMemo(() => {
    const keyword = (filters.keyword || '').trim().toLowerCase();
    return talents.filter(talent => (!keyword || [talent.name, talent.current_position, talent.current_company, talent.school, ...(talent.skill_tags || [])].join(' ').toLowerCase().includes(keyword))
      && (!filters.status || talent.status === filters.status) && (!filters.organization_id || talent.organization_id === filters.organization_id)).map(talent => { const g = geoMap[talent.id]; return g ? { ...talent, ...g } : talent; });
  }, [talents, geoMap, filters.keyword, filters.status, filters.organization_id]);
  const filteredTalents = useMemo(() => mapTalents.filter(talent => (!filters.province || talent.province === filters.province) && (!filters.country || talent.country === filters.country)), [mapTalents, filters.province, filters.country]);
  const statistics = useMemo(() => ({ total, active: talents.filter(item => item.status === 'active').length, provinces: new Set(talents.map(item => item.country).filter(Boolean)).size, highPotential: talents.filter(item => item.potential_level === 'A').length }), [talents, total]);
  const skillRanking = useMemo(() => {
    const counts: Record<string, number> = {}; talents.forEach(talent => (talent.skill_tags || []).forEach(skill => { counts[skill] = (counts[skill] || 0) + 1; }));
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [talents]);
  const organizationRanking = useMemo(() => {
    const counts: Record<string, number> = {}; talents.forEach(talent => { const name = talent.organization_name || '未分配组织'; counts[name] = (counts[name] || 0) + 1; });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [talents]);

  const openEditor = (talent?: Talent) => { setEditing(talent || null); setResumeFile(null); setEditorOpen(true); if (talent) form.setFieldsValue(talent); else form.resetFields(); };
  const saveTalent = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      const payload: Record<string, unknown> = { ...values, skill_tags: values.skill_tags || [] };
      // 后端列表返回脱敏联系方式；未修改时不把脱敏值写回数据库。
      if (typeof payload.mobile === 'string' && payload.mobile.includes('*')) delete payload.mobile;
      if (typeof payload.email === 'string' && payload.email.includes('*')) delete payload.email;
      const saved = editing ? await talentsApi.update(editing.id, payload) : await talentsApi.create(payload);
      if (resumeFile) {
        const uploaded = await resumesApi.upload(resumeFile);
        await resumesApi.bind(uploaded.id, saved.id);
      }
      message.success(editing ? '人才档案已更新' : '人才档案已创建'); setEditorOpen(false); await loadTalents();
    } catch (error) { message.error(formatApiError(error)); } finally { setSaving(false); }
  };
  const confirmDelete = async () => {
    if (!deleting) return;
    try { await talentsApi.remove(deleting.id); message.success('人才档案已删除'); setDeleting(null); await loadTalents(); }
    catch (error) { message.error(formatApiError(error)); }
  };
  const openPortrait = async (talent: Talent) => {
    setPortraitTalent(talent); setPortrait(null); setPortraitLoading(true);
    try { setPortrait(await talentsApi.portrait(talent.id)); } catch (error) { message.error(formatApiError(error)); }
    finally { setPortraitLoading(false); }
  };
  const exportRows = () => {
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    downloadText('人才档案.csv', '\ufeff姓名,省份,城市,组织,职位,学历,技能,状态\n' + filteredTalents.map(item => [item.name, item.province, item.work_city, item.organization_name, item.current_position, item.education, (item.skill_tags || []).join('/'), item.status].map(escape).join(',')).join('\n'));
  };
  const orgOptions = flattenOrgs(organizations).map(item => ({ value: item.id, label: item.name }));

  return <div className={`talents-page talent-archive-page ${fullscreen ? 'archive-focus-active' : ''}`}>
    {!fullscreen && <>
      <header className="archive-page-header"><div><div className="archive-kicker"><span /> TALENT INTELLIGENCE / 人才资产</div><h1>人才档案 <Tag color="cyan">真实数据</Tag></h1><p>以立体档案卡组组织人才信息，让查阅、比较与人才邀约自然衔接。</p></div><div className="archive-header-actions"><Button icon={<DownloadOutlined />} onClick={exportRows}>导出当前视图</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>新建人才档案</Button></div></header>
      {loadError && <Alert type="error" showIcon message="人才档案加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
      <section className="archive-metric-grid"><div><span className="metric-icon navy"><UserOutlined /></span><p>人才档案总量</p><strong>{statistics.total}</strong><small>后端分页总数</small></div><div><span className="metric-icon green"><ThunderboltOutlined /></span><p>当前在职</p><strong>{statistics.active}</strong><small>{statistics.total ? Math.round(statistics.active / Math.max(talents.length, 1) * 100) : 0}% 当前页在职占比</small></div><div><span className="metric-icon blue"><GlobalOutlined /></span><p>覆盖国家 / 地区</p><strong>{statistics.provinces}</strong><small>基于后端返回地域字段</small></div><div><span className="metric-icon amber"><ApartmentOutlined /></span><p>A 级高潜人才</p><strong>{statistics.highPotential}</strong><small>基于后端人才画像</small></div></section>
      <section className="archive-ai-search"><RobotOutlined /><div><b>AI 人才搜索</b><span>支持姓名、职位、技能与院校的自然组合检索</span></div><Input allowClear prefix={<SearchOutlined />} placeholder="试试搜索：高级算法、浙江大学或 Python" value={filters.keyword} onChange={event => updateFilters({ keyword: event.target.value })} /><Select allowClear placeholder="所属组织" value={filters.organization_id} onChange={value => updateFilters({ organization_id: value })} options={orgOptions} /><Select allowClear placeholder="人才状态" value={filters.status} onChange={value => updateFilters({ status: value })} options={[{ value: 'active', label: '在职' }, { value: 'left', label: '离职' }, { value: 'reserve', label: '储备' }]} /></section>
    </>}

    <Spin spinning={loading && !fullscreen}>
      <ResumeCoverflow talents={filteredTalents} province={filters.province || filters.country} fullscreen={fullscreen} onFullscreenChange={setFullscreen} />
    </Spin>

    {!fullscreen && <>
      <section className="talent-intelligence-grid"><TalentMap talents={mapTalents} resultCount={filteredTalents.length} filters={filters} onFilterChange={updateFilters} /><aside className="talent-data-panel"><div className="data-panel-heading"><div><div className="section-eyebrow">TALENT DATA PROFILE</div><h2>人才数据切片</h2></div><span className="live-indicator"><i />实时数据</span></div><p className="data-panel-intro">摘要随顶部 AI 搜索、筛选与地图选择实时更新。</p><div className="data-slice-block"><h3>高频技能 <span>TOP {skillRanking.length}</span></h3><div className="skill-ranking">{skillRanking.map(([skill, count], index) => <div key={skill}><span className="rank-index">{String(index + 1).padStart(2, '0')}</span><b>{skill}</b><div><i style={{ width: `${count / skillRanking[0][1] * 100}%` }} /></div><strong>{count}</strong></div>)}</div></div><div className="data-slice-block organization-slice"><h3>组织人才分布 <span>TOP {organizationRanking.length}</span></h3>{organizationRanking.map(([name, count]) => <div className="organization-rank" key={name}><span>{name}</span><b>{count}<small> 人</small></b></div>)}</div><div className="data-source-note"><span>DATA ADAPTER</span><b>/api/v1/talents</b><small>后端真实人才档案</small></div></aside></section>
      <section className="archive-filter-bar"><div className="archive-filter-title"><SearchOutlined /><div><b>当前人才清单</b><span>与卡组、地图实时联动</span></div></div><span className="filter-result-count">{filteredTalents.length} 份匹配档案</span>{(filters.province || filters.country) && <Tag closable onClose={() => updateFilters({ province: undefined, country: undefined })}>{filters.province || filters.country}</Tag>}<Button type="text" onClick={() => setFilters(emptyFilters)}>重置全部筛选</Button></section>
      <TalentTable talents={filteredTalents} loading={loading} province={filters.province} onPortrait={openPortrait} onEdit={openEditor} onDelete={setDeleting} />
    </>}

    <Modal title={editing ? '编辑人才档案' : '新建人才档案'} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={saveTalent} confirmLoading={saving} okText="保存档案" width={720} destroyOnHidden><Form form={form} layout="vertical" initialValues={{ gender: 'unknown', status: 'active', work_years: 0 }} className="talent-editor-form"><div className="grid grid-cols-3 gap-x-4"><Form.Item name="name" label="姓名" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="mobile" label="手机号"><Input /></Form.Item><Form.Item name="email" label="邮箱"><Input /></Form.Item><Form.Item name="current_company" label="当前公司"><Input /></Form.Item><Form.Item name="current_position" label="当前职位"><Input /></Form.Item><Form.Item name="country" label="国家"><Input placeholder="如：中国" /></Form.Item><Form.Item name="province" label="省份 / 州"><Input placeholder="如：广东省" /></Form.Item><Form.Item name="work_city" label="工作城市"><Input placeholder="如：深圳" /></Form.Item><Form.Item name="job_level" label="职级"><Input placeholder="如：P7 / 高级工程师" /></Form.Item><Form.Item name="organization_id" label="所属组织"><Select options={orgOptions} /></Form.Item><Form.Item name="education" label="学历"><Select options={['博士', '硕士', '本科', '大专'].map(value => ({ value, label: value }))} /></Form.Item><Form.Item name="school" label="毕业院校"><Input /></Form.Item><Form.Item name="work_years" label="工作年限"><InputNumber min={0} className="w-full" /></Form.Item></div><Form.Item name="skill_tags" label="核心技能"><Select mode="tags" /></Form.Item><div className="resume-upload-field"><CloudUploadOutlined /><div><b>简历附件</b><span>{resumeFile?.name || '选择后将上传并绑定到该人才档案'}</span></div><Upload maxCount={1} beforeUpload={file => { setResumeFile(file); return false; }} showUploadList={false}><Button>选择文件</Button></Upload></div></Form></Modal>
    <Modal open={!!deleting} title="删除人才档案" onCancel={() => setDeleting(null)} onOk={confirmDelete} okText="确认删除" okButtonProps={{ danger: true, icon: <DeleteOutlined /> }}><p>确定删除「{deleting?.name}」吗？删除后后端将逻辑删除该档案。</p></Modal>
    <Drawer title={portraitTalent ? `${portraitTalent.name} · AI 人才画像` : '人才画像'} width={480} open={!!portraitTalent} onClose={() => setPortraitTalent(null)}><Spin spinning={portraitLoading}>{portraitTalent && <div className="real-portrait-content"><div className="portrait-version"><span>{portrait?.status === 'pending' ? '画像待生成' : `画像版本 ${portrait?.version ?? '—'}`}</span><b>{portrait?.potential_level || portraitTalent.potential_level || '—'} 级潜力</b></div><h3>画像摘要</h3><p className="text-sm leading-7 text-slate-500">{portrait?.summary || portraitTalent.portrait_summary || '暂无画像摘要'}</p><h3>核心技能</h3>{(portraitTalent.skill_tags || []).map(item => <Tag key={item}>{item}</Tag>)}<h3>优势</h3>{(portrait?.strengths || []).map(item => <p key={item} className="text-sm text-slate-500">{item}</p>)}<h3>建议岗位</h3>{(portrait?.suggested_positions || []).map(item => <Tag color="green" key={item}>{item}</Tag>)}</div>}</Spin></Drawer>
  </div>;
}
