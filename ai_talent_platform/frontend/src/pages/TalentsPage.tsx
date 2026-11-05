import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Form, Input, InputNumber, Modal, Select, Tag, Upload, message } from 'antd';
import { ApartmentOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined, GlobalOutlined, PlusOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { useLocation } from 'react-router-dom';
import ResumeCoverflow from '../components/talents/ResumeCoverflow';
import TalentMap from '../components/talents/TalentMap';
import TalentTable from '../components/talents/TalentTable';
import type { TalentFilterState } from '../components/talents/types';
import type { OrgNode, Talent } from '../types';
import { demoOrgs, demoTalents } from '../services/mockData';
import { downloadText } from '../utils';
import '../components/talents/talents.css';

const emptyFilters: TalentFilterState = { keyword: '', status: undefined, organization_id: undefined, province: undefined };
const flattenOrgs = (items: OrgNode[]): OrgNode[] => items.flatMap(item => [item, ...flattenOrgs(item.children || [])]);

/** 交互验收版本：本地演示数据与状态适配层集中在此，后续可原位替换为人才 API。 */
export default function TalentsPage() {
  const location = useLocation();
  const [talents, setTalents] = useState<Talent[]>(demoTalents);
  const [filters, setFilters] = useState<TalentFilterState>(emptyFilters);
  const [fullscreen, setFullscreen] = useState(Boolean((location.state as { fullscreen?: boolean } | null)?.fullscreen));
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Talent | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [deleting, setDeleting] = useState<Talent | null>(null);
  const [portraitTalent, setPortraitTalent] = useState<Talent | null>(null);
  const [form] = Form.useForm();
  const organizations = useMemo(() => flattenOrgs(demoOrgs), []);

  useEffect(() => {
    const enter = () => setFullscreen(true);
    window.addEventListener('talentos:archive-fullscreen', enter);
    return () => window.removeEventListener('talentos:archive-fullscreen', enter);
  }, []);
  const updateFilters = useCallback((patch: Partial<TalentFilterState>) => setFilters(current => ({ ...current, ...patch })), []);
  const mapTalents = useMemo(() => {
    const keyword = (filters.keyword || '').trim().toLowerCase();
    return talents.filter(talent => (!keyword || [talent.name, talent.current_position, talent.current_company, talent.school, ...talent.skill_tags].join(' ').toLowerCase().includes(keyword))
      && (!filters.status || talent.status === filters.status) && (!filters.organization_id || talent.organization_id === filters.organization_id));
  }, [talents, filters.keyword, filters.status, filters.organization_id]);
  const filteredTalents = useMemo(() => mapTalents.filter(talent => !filters.province || talent.province === filters.province), [mapTalents, filters.province]);
  const statistics = useMemo(() => ({ total: talents.length, active: talents.filter(item => item.status === 'active').length, provinces: new Set(talents.map(item => item.province)).size, highPotential: talents.filter(item => item.potential_level === 'A').length }), [talents]);
  const skillRanking = useMemo(() => {
    const counts: Record<string, number> = {}; talents.forEach(talent => talent.skill_tags.forEach(skill => { counts[skill] = (counts[skill] || 0) + 1; }));
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [talents]);
  const organizationRanking = useMemo(() => {
    const counts: Record<string, number> = {}; talents.forEach(talent => { counts[talent.organization_name] = (counts[talent.organization_name] || 0) + 1; });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [talents]);

  const openEditor = (talent?: Talent) => { setEditing(talent || null); setResumeFile(null); setEditorOpen(true); if (talent) form.setFieldsValue(talent); else form.resetFields(); };
  const saveTalent = async () => {
    const values = await form.validateFields();
    if (editing) setTalents(current => current.map(item => item.id === editing.id ? { ...item, ...values } : item));
    else setTalents(current => [...current, { ...demoTalents[0], ...values, id: Date.now(), skill_tags: values.skill_tags || [], portrait_summary: '新建演示人才档案。' }]);
    message.success(editing ? '演示档案已更新' : '演示档案已创建'); setEditorOpen(false);
  };
  const exportRows = () => {
    const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    downloadText('人才档案.csv', '\ufeff姓名,省份,城市,组织,职位,学历,技能,状态\n' + filteredTalents.map(item => [item.name, item.province, item.work_city, item.organization_name, item.current_position, item.education, item.skill_tags.join('/'), item.status].map(escape).join(',')).join('\n'));
  };

  return <div className={`talents-page talent-archive-page ${fullscreen ? 'archive-focus-active' : ''}`}>
    {!fullscreen && <>
      <header className="archive-page-header"><div><div className="archive-kicker"><span /> TALENT INTELLIGENCE / 人才资产</div><h1>人才档案 <Tag color="cyan">交互预览 · 演示数据</Tag></h1><p>以立体档案卡组组织人才信息，让查阅、比较与人才邀约自然衔接。</p></div><div className="archive-header-actions"><Button icon={<DownloadOutlined />} onClick={exportRows}>导出当前视图</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>新建人才档案</Button></div></header>
      <section className="archive-metric-grid"><div><span className="metric-icon navy"><UserOutlined /></span><p>人才档案总量</p><strong>{statistics.total}</strong><small>当前演示数据集</small></div><div><span className="metric-icon green"><ThunderboltOutlined /></span><p>当前在职</p><strong>{statistics.active}</strong><small>{Math.round(statistics.active / statistics.total * 100)}% 在职占比</small></div><div><span className="metric-icon blue"><GlobalOutlined /></span><p>覆盖省级地域</p><strong>{statistics.provinces}</strong><small>国内人才地域分布</small></div><div><span className="metric-icon amber"><ApartmentOutlined /></span><p>A 级高潜人才</p><strong>{statistics.highPotential}</strong><small>基于演示人才画像</small></div></section>
      <section className="archive-ai-search"><RobotOutlined /><div><b>AI 人才搜索</b><span>支持姓名、职位、技能与院校的自然组合检索</span></div><Input allowClear prefix={<SearchOutlined />} placeholder="试试搜索：高级算法、浙江大学或 Python" value={filters.keyword} onChange={event => updateFilters({ keyword: event.target.value })} /><Select allowClear placeholder="所属组织" value={filters.organization_id} onChange={value => updateFilters({ organization_id: value })} options={organizations.map(item => ({ value: item.id, label: item.name }))} /><Select allowClear placeholder="人才状态" value={filters.status} onChange={value => updateFilters({ status: value })} options={[{ value: 'active', label: '在职' }, { value: 'left', label: '离职' }, { value: 'reserve', label: '储备' }]} /></section>
    </>}

    <ResumeCoverflow talents={filteredTalents} province={filters.province} fullscreen={fullscreen} onFullscreenChange={setFullscreen} />

    {!fullscreen && <>
      <section className="talent-intelligence-grid"><TalentMap talents={mapTalents} resultCount={filteredTalents.length} filters={filters} onFilterChange={updateFilters} /><aside className="talent-data-panel"><div className="data-panel-heading"><div><div className="section-eyebrow">DEMO DATA PROFILE</div><h2>人才数据切片</h2></div><span className="live-indicator"><i />演示数据</span></div><p className="data-panel-intro">摘要随顶部 AI 搜索、筛选与地图选择实时更新。</p><div className="data-slice-block"><h3>高频技能 <span>TOP {skillRanking.length}</span></h3><div className="skill-ranking">{skillRanking.map(([skill, count], index) => <div key={skill}><span className="rank-index">{String(index + 1).padStart(2, '0')}</span><b>{skill}</b><div><i style={{ width: `${count / skillRanking[0][1] * 100}%` }} /></div><strong>{count}</strong></div>)}</div></div><div className="data-slice-block organization-slice"><h3>组织人才分布 <span>TOP {organizationRanking.length}</span></h3>{organizationRanking.map(([name, count]) => <div className="organization-rank" key={name}><span>{name}</span><b>{count}<small> 人</small></b></div>)}</div><div className="data-source-note"><span>DATA ADAPTER</span><b>demoTalents</b><small>预留人才 API 替换位</small></div></aside></section>
      <section className="archive-filter-bar"><div className="archive-filter-title"><SearchOutlined /><div><b>当前人才清单</b><span>与卡组、地图实时联动</span></div></div><span className="filter-result-count">{filteredTalents.length} 份匹配档案</span>{filters.province && <Tag closable onClose={() => updateFilters({ province: undefined })}>{filters.province}</Tag>}<Button type="text" onClick={() => setFilters(emptyFilters)}>重置全部筛选</Button></section>
      <TalentTable talents={filteredTalents} province={filters.province} onPortrait={setPortraitTalent} onEdit={openEditor} onDelete={setDeleting} />
    </>}

    <Modal title={editing ? '编辑演示档案' : '新建演示档案'} open={editorOpen} onCancel={() => setEditorOpen(false)} onOk={saveTalent} okText="保存档案" width={720} destroyOnHidden><Form form={form} layout="vertical" initialValues={{ gender: 'unknown', status: 'active', work_years: 0 }} className="talent-editor-form"><div className="grid grid-cols-3 gap-x-4"><Form.Item name="name" label="姓名" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="mobile" label="手机号"><Input /></Form.Item><Form.Item name="email" label="邮箱"><Input /></Form.Item><Form.Item name="current_company" label="当前公司"><Input /></Form.Item><Form.Item name="current_position" label="当前职位"><Input /></Form.Item><Form.Item name="organization_id" label="所属组织"><Select options={organizations.map(item => ({ value: item.id, label: item.name }))} /></Form.Item><Form.Item name="education" label="学历"><Select options={['博士', '硕士', '本科', '大专'].map(value => ({ value, label: value }))} /></Form.Item><Form.Item name="school" label="毕业院校"><Input /></Form.Item><Form.Item name="work_years" label="工作年限"><InputNumber min={0} className="w-full" /></Form.Item></div><Form.Item name="skill_tags" label="核心技能"><Select mode="tags" /></Form.Item><div className="resume-upload-field"><CloudUploadOutlined /><div><b>简历附件</b><span>{resumeFile?.name || '仅用于演示文件选择，不会上传'}</span></div><Upload maxCount={1} beforeUpload={file => { setResumeFile(file); return false; }} showUploadList={false}><Button>选择文件</Button></Upload></div></Form></Modal>
    <Modal open={!!deleting} title="删除演示档案" onCancel={() => setDeleting(null)} onOk={() => { if (deleting) setTalents(current => current.filter(item => item.id !== deleting.id)); setDeleting(null); message.success('演示档案已删除'); }} okText="确认删除" okButtonProps={{ danger: true, icon: <DeleteOutlined /> }}><p>确定删除「{deleting?.name}」吗？本次仅修改本地演示数据。</p></Modal>
    <Drawer title={portraitTalent ? `${portraitTalent.name} · AI 人才画像` : '人才画像'} width={480} open={!!portraitTalent} onClose={() => setPortraitTalent(null)}>{portraitTalent && <div className="real-portrait-content"><div className="portrait-version"><span>演示画像 · 匹配度 {portraitTalent.match_score}</span><b>{portraitTalent.potential_level} 级潜力</b></div><h3>画像摘要</h3><p className="text-sm leading-7 text-slate-500">{portraitTalent.portrait_summary}</p><h3>核心技能</h3>{portraitTalent.skill_tags.map(item => <Tag key={item}>{item}</Tag>)}<h3>建议岗位</h3><Tag color="green">{portraitTalent.current_position}</Tag></div>}</Drawer>
  </div>;
}
