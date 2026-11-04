import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Spin, Tag, Upload, message } from 'antd';
import { ApartmentOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined, GlobalOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import ResumeCoverflow from '../components/talents/ResumeCoverflow';
import TalentMap from '../components/talents/TalentMap';
import TalentTable from '../components/talents/TalentTable';
import type { TalentFilterState } from '../components/talents/types';
import { request } from '../services/api';
import type { OrgNode, PageResult, Talent } from '../types';
import { downloadText } from '../utils';
import '../components/talents/talents.css';

const emptyFilters: TalentFilterState = { keyword: '', status: undefined, organization_id: undefined, country: undefined, province: undefined };
const flattenOrgNodes = (items: OrgNode[]): OrgNode[] => items.flatMap(item => [item, ...flattenOrgNodes(item.children || [])]);

export default function TalentsPage() {
  const [talents, setTalents] = useState<Talent[]>([]);
  const [organizations, setOrgNodes] = useState<OrgNode[]>([]);
  const [filters, setFilters] = useState<TalentFilterState>(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [lastSync, setLastSync] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Talent | null>(null);
  const [saving, setSaving] = useState(false);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [deleting, setDeleting] = useState<Talent | null>(null);
  const [portraitTalent, setPortraitTalent] = useState<Talent | null>(null);
  const [portraitLoading, setPortraitLoading] = useState(false);
  const [portraitError, setPortraitError] = useState('');
  const [form] = Form.useForm();

  const loadData = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const [talentPage, organizationTree] = await Promise.all([
        request<PageResult<Talent>>({ url: '/talents', params: { page: 1, page_size: 100 } }),
        request<OrgNode[]>({ url: '/organizations/tree' }),
      ]);
      setTalents(talentPage.items || []);
      setOrgNodes(flattenOrgNodes(organizationTree || []));
      setLastSync(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
    } catch (error) {
      setTalents([]); setOrgNodes([]);
      setLoadError(error instanceof Error ? error.message : '人才数据加载失败');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { loadData(); }, [loadData]);

  const updateFilters = useCallback((patch: Partial<TalentFilterState>) => setFilters(current => ({ ...current, ...patch })), []);
  const mapTalents = useMemo(() => {
    const keyword = (filters.keyword || '').trim().toLowerCase();
    return talents.filter(talent => {
      const matchesKeyword = !keyword || [talent.name, talent.current_position, talent.current_company, talent.school, ...(talent.skill_tags || [])].join(' ').toLowerCase().includes(keyword);
      return matchesKeyword && (!filters.status || talent.status === filters.status)
        && (!filters.organization_id || talent.organization_id === filters.organization_id);
    });
  }, [talents, filters.keyword, filters.status, filters.organization_id]);
  const filteredTalents = useMemo(() => mapTalents.filter(talent =>
    (!filters.country || talent.country === filters.country) && (!filters.province || talent.province === filters.province)
  ), [mapTalents, filters.country, filters.province]);

  const statistics = useMemo(() => ({
    total: talents.length,
    active: talents.filter(item => item.status === 'active').length,
    countries: new Set(talents.map(item => item.country).filter(Boolean)).size,
    highPotential: talents.filter(item => item.potential_level === 'A').length,
  }), [talents]);
  const skillRanking = useMemo(() => {
    const counts: Record<string, number> = {};
    talents.forEach(talent => (talent.skill_tags || []).forEach(skill => { counts[skill] = (counts[skill] || 0) + 1; }));
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [talents]);
  const organizationRanking = useMemo(() => {
    const counts: Record<string, number> = {};
    talents.forEach(talent => { const name = talent.organization_name || '未分配组织'; counts[name] = (counts[name] || 0) + 1; });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [talents]);

  const openEditor = (talent?: Talent) => {
    setEditing(talent || null); setResumeFile(null); setEditorOpen(true);
    if (talent) form.setFieldsValue(talent);
    else form.resetFields();
  };
  const saveTalent = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      const saved = await request<Talent>({ url: editing ? `/talents/${editing.id}` : '/talents', method: editing ? 'PUT' : 'POST', data: { ...values, skill_tags: values.skill_tags || [] } });
      if (resumeFile) {
        const body = new FormData(); body.append('file', resumeFile);
        await request({ url: `/talents/${saved.id}/resume`, method: 'POST', data: body, headers: { 'Content-Type': 'multipart/form-data' } });
      }
      message.success(editing ? '人才档案已更新' : '人才档案已创建');
      setEditorOpen(false); await loadData();
    } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); }
    finally { setSaving(false); }
  };
  const deleteTalent = async () => {
    if (!deleting) return;
    try { await request({ url: `/talents/${deleting.id}`, method: 'DELETE' }); message.success('人才档案已删除'); setDeleting(null); await loadData(); }
    catch (error) { message.error(error instanceof Error ? error.message : '删除失败'); }
  };
  const openPortrait = async (talent: Talent) => {
    setPortraitTalent(talent); setPortraitLoading(true); setPortraitError('');
    try {
      const portrait = await request<Talent['portrait']>({ url: `/talents/${talent.id}/portrait` });
      setPortraitTalent(current => current ? { ...current, portrait: portrait || undefined } : current);
    } catch (error) { setPortraitError(error instanceof Error ? error.message : '画像加载失败'); }
    finally { setPortraitLoading(false); }
  };
  const generatePortrait = async () => {
    if (!portraitTalent) return;
    setPortraitLoading(true); setPortraitError('');
    try {
      await request({ url: `/talents/${portraitTalent.id}/portrait/generate`, method: 'POST' });
      message.success('画像生成任务已提交'); await openPortrait(portraitTalent);
    } catch (error) { setPortraitError(error instanceof Error ? error.message : '画像生成失败'); setPortraitLoading(false); }
  };
  const exportRows = () => {
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const rows = filteredTalents.map(item => [item.name, item.country, item.province, item.work_city, item.organization_name, item.current_position, item.education, (item.skill_tags || []).join('/'), item.status].map(escape).join(','));
    downloadText('人才档案.csv', '\ufeff姓名,国家,省份,城市,组织,职位,学历,技能,状态\n' + rows.join('\n'));
  };

  return <div className="talents-page talent-archive-page">
    <header className="archive-page-header">
      <div><div className="archive-kicker"><span /> TALENT INTELLIGENCE / 人才资产</div><h1>人才档案</h1><p>从全球分布到个人能力画像，让人才信息在同一视图中清晰流动。</p></div>
      <div className="archive-header-actions"><Button icon={<DownloadOutlined />} onClick={exportRows} disabled={!filteredTalents.length}>导出当前视图</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>新建人才档案</Button></div>
    </header>

    {loadError && <Alert className="talent-load-error" type="error" showIcon message="真实人才数据加载失败" description={loadError} action={<Button size="small" icon={<ReloadOutlined />} onClick={loadData}>重新加载</Button>} />}
    <section className="archive-metric-grid">
      <div><span className="metric-icon navy"><UserOutlined /></span><p>人才档案总量</p><strong>{statistics.total}</strong><small>来自人才数据库</small></div>
      <div><span className="metric-icon green"><ThunderboltOutlined /></span><p>当前在职</p><strong>{statistics.active}</strong><small>{statistics.total ? `${Math.round(statistics.active / statistics.total * 100)}% 在职占比` : '暂无数据'}</small></div>
      <div><span className="metric-icon blue"><GlobalOutlined /></span><p>覆盖国家 / 地区</p><strong>{statistics.countries}</strong><small>按人才国家字段统计</small></div>
      <div><span className="metric-icon amber"><ApartmentOutlined /></span><p>A 级高潜人才</p><strong>{statistics.highPotential}</strong><small>基于已生成人才画像</small></div>
    </section>

    <ResumeCoverflow talents={filteredTalents} province={filters.province || filters.country} />

    <section className="talent-intelligence-grid">
      <TalentMap talents={mapTalents} resultCount={filteredTalents.length} filters={filters} onFilterChange={updateFilters} />
      <aside className="talent-data-panel">
        <div className="data-panel-heading"><div><div className="section-eyebrow">LIVE DATA PROFILE</div><h2>人才数据切片</h2></div><span className="live-indicator"><i />实时数据</span></div>
        <p className="data-panel-intro">以下摘要完全由当前数据库档案计算，不使用预设比例。</p>
        <div className="data-slice-block"><h3>高频技能 <span>TOP {skillRanking.length}</span></h3>{skillRanking.length ? <div className="skill-ranking">{skillRanking.map(([skill, count], index) => <div key={skill}><span className="rank-index">{String(index + 1).padStart(2, '0')}</span><b>{skill}</b><div><i style={{ width: `${count / skillRanking[0][1] * 100}%` }} /></div><strong>{count}</strong></div>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无技能数据" />}</div>
        <div className="data-slice-block organization-slice"><h3>组织人才分布 <span>TOP {organizationRanking.length}</span></h3>{organizationRanking.length ? organizationRanking.map(([name, count]) => <div className="organization-rank" key={name}><span>{name}</span><b>{count}<small> 人</small></b></div>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无组织数据" />}</div>
        <div className="data-source-note"><span>DATA SOURCE</span><b>/api/v1/talents</b><small>{lastSync ? `最近同步 ${lastSync}` : '等待数据同步'}</small></div>
      </aside>
    </section>

    <section className="archive-filter-bar">
      <div className="archive-filter-title"><SearchOutlined /><div><b>人才检索</b><span>检索结果同步至简历与地图</span></div></div>
      <Input allowClear prefix={<SearchOutlined />} placeholder="搜索姓名、职位、公司或技能" value={filters.keyword} onChange={event => updateFilters({ keyword: event.target.value })} />
      <Select allowClear placeholder="所属组织" value={filters.organization_id} onChange={value => updateFilters({ organization_id: value })} options={organizations.map(item => ({ value: item.id, label: item.name }))} />
      <Select allowClear placeholder="人才状态" value={filters.status} onChange={value => updateFilters({ status: value })} options={[{ value: 'active', label: '在职' }, { value: 'left', label: '离职' }, { value: 'reserve', label: '储备' }]} />
      {(filters.country || filters.province) && <Tag closable onClose={() => updateFilters({ country: undefined, province: undefined })}>{filters.province || filters.country}</Tag>}
      <Button type="text" onClick={() => setFilters(emptyFilters)}>重置</Button>
    </section>
    <TalentTable talents={filteredTalents} loading={loading} province={filters.province || filters.country} onPortrait={openPortrait} onEdit={openEditor} onDelete={setDeleting} />

    <Modal title={editing ? '编辑人才档案' : '新建人才档案'} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={saveTalent} confirmLoading={saving} okText="保存档案" width={760} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ gender: 'unknown', status: 'active', work_years: 0 }} className="talent-editor-form">
        <div className="form-section-label">基本信息</div>
        <div className="grid grid-cols-3 gap-x-4"><Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}><Input /></Form.Item><Form.Item name="gender" label="性别"><Select options={[{ value: 'male', label: '男' }, { value: 'female', label: '女' }, { value: 'unknown', label: '未知' }]} /></Form.Item><Form.Item name="mobile" label="手机号"><Input /></Form.Item><Form.Item name="email" label="邮箱"><Input /></Form.Item><Form.Item name="organization_id" label="所属组织"><Select allowClear options={organizations.map(item => ({ value: item.id, label: item.name }))} /></Form.Item><Form.Item name="status" label="人才状态"><Select options={[{ value: 'active', label: '在职' }, { value: 'left', label: '离职' }, { value: 'reserve', label: '储备' }]} /></Form.Item></div>
        <div className="form-section-label">职业与教育</div>
        <div className="grid grid-cols-3 gap-x-4"><Form.Item name="current_company" label="当前公司"><Input /></Form.Item><Form.Item name="current_position" label="当前职位"><Input /></Form.Item><Form.Item name="work_years" label="工作年限"><InputNumber min={0} max={60} className="w-full" /></Form.Item><Form.Item name="education" label="学历"><Select allowClear options={['博士', '硕士', '本科', '大专', '其他'].map(value => ({ value, label: value }))} /></Form.Item><Form.Item name="school" label="毕业院校"><Input /></Form.Item><Form.Item name="major" label="专业"><Input /></Form.Item></div>
        <Form.Item name="skill_tags" label="核心技能"><Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入技能后回车" /></Form.Item>
        <Form.Item name="self_evaluation" label="个人评价"><Input.TextArea rows={3} /></Form.Item>
        <div className="resume-upload-field"><CloudUploadOutlined /><div><b>简历附件</b><span>{resumeFile?.name || '保存档案时一并上传；不上传不会生成模拟简历'}</span></div><Upload maxCount={1} beforeUpload={file => { setResumeFile(file); return false; }} showUploadList={false}><Button>选择文件</Button></Upload></div>
      </Form>
    </Modal>
    <Modal open={!!deleting} title="删除人才档案" onCancel={() => setDeleting(null)} onOk={deleteTalent} okText="确认删除" okButtonProps={{ danger: true, icon: <DeleteOutlined /> }}><p>确定删除「{deleting?.name}」的人才档案吗？此操作将直接请求数据库接口。</p></Modal>

    <Drawer title={portraitTalent ? `${portraitTalent.name} · AI 人才画像` : '人才画像'} width={520} open={!!portraitTalent} onClose={() => setPortraitTalent(null)} extra={<Button type="primary" icon={<ThunderboltOutlined />} loading={portraitLoading} onClick={generatePortrait}>生成 / 更新画像</Button>}>
      <Spin spinning={portraitLoading}>{portraitError && <Alert type="error" showIcon message="画像服务未返回数据" description={portraitError} className="mb-5" />}{portraitTalent?.portrait ? <div className="real-portrait-content"><div className="portrait-version"><span>模型 {portraitTalent.portrait.model_name || '未记录'}</span><b>{portraitTalent.portrait.potential_level || portraitTalent.potential_level} 级潜力</b></div>{Object.keys(portraitTalent.portrait.dimensions || {}).length > 0 && <div className="portrait-dimension-list">{Object.entries(portraitTalent.portrait.dimensions || {}).map(([name, value]) => <div key={name}><span>{name}</span><div><i style={{ width: `${Math.min(100, value)}%` }} /></div><b>{value}</b></div>)}</div>}<h3>优势</h3><div>{(portraitTalent.portrait.strengths || []).map(item => <Tag key={item}>{item}</Tag>)}{!portraitTalent.portrait.strengths?.length && <span className="text-gray-400">暂无数据</span>}</div><h3>待发展方向</h3><div>{(portraitTalent.portrait.weaknesses || []).map(item => <Tag key={item}>{item}</Tag>)}{!portraitTalent.portrait.weaknesses?.length && <span className="text-gray-400">暂无数据</span>}</div><h3>建议岗位</h3><div>{(portraitTalent.portrait.suggested_positions || []).map(item => <Tag key={item}>{item}</Tag>)}{!portraitTalent.portrait.suggested_positions?.length && <span className="text-gray-400">暂无数据</span>}</div></div> : !portraitLoading && !portraitError && <Empty description="数据库中暂无该人才画像，可点击右上角生成" />}</Spin>
    </Drawer>
  </div>;
}
