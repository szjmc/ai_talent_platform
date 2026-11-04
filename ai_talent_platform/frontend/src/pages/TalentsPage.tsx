import { useEffect, useMemo, useState } from 'react';
import { App, Button, Col, Drawer, Form, Input, Modal, Progress, Row, Select, Tag } from 'antd';
import { DeleteOutlined, EditOutlined, ExportOutlined, MergeCellsOutlined, PlusOutlined, RobotOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Chart, PageHeader, PersonAvatar, PotentialTag, SkillTags } from '../components/Common';
import ResumeCoverflow from '../components/talents/ResumeCoverflow';
import TalentMap from '../components/talents/TalentMap';
import TalentTable from '../components/talents/TalentTable';
import type { TalentFilterState } from '../components/talents/types';
import { demoTalents } from '../services/mockData';
import { formatApiError, request, requestWithFallback } from '../services/api';
import { downloadText, sleep } from '../utils';
import { useAuth } from '../store/AuthContext';
import type { PageResult, Talent } from '../types';
import '../components/talents/talents.css';

const portraitDimensions = [{ name: '专业技能', value: 94 }, { name: '项目经验', value: 88 }, { name: '综合素质', value: 90 }, { name: '发展潜力', value: 96 }, { name: '团队协作', value: 85 }];
const departments = ['技术研发部', '人力资源部', '数据智能部', '市场品牌部'];
const departmentIds: Record<string, number> = { 技术研发部: 3, 人力资源部: 2, 数据智能部: 5, 市场品牌部: 4 };
const emptyFilters: TalentFilterState = {};

export default function TalentsPage() {
  const [data, setData] = useState<Talent[]>(demoTalents);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Talent | null>(null);
  const [editing, setEditing] = useState<Talent | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const [semantic, setSemantic] = useState('');
  const [semanticLoading, setSemanticLoading] = useState(false);
  const [draftFilters, setDraftFilters] = useState<TalentFilterState>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<TalentFilterState>(emptyFilters);
  const [searchParams] = useSearchParams();
  const { demo } = useAuth();
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();

  useEffect(() => { const query = searchParams.get('keyword'); if (query) { setKeyword(query); setAppliedKeyword(query); } }, [searchParams]);

  const load = async (filters = appliedFilters, search = appliedKeyword) => {
    setLoading(true);
    try {
      const result = await requestWithFallback<PageResult<Talent>>({
        url: '/talents',
        params: {
          page: 1, page_size: 200, keyword: search || undefined,
          education: filters.education, status: filters.status,
          organization_id: filters.organization_name ? departmentIds[filters.organization_name] : undefined,
        },
      }, { items: demoTalents, total: demoTalents.length, page: 1, page_size: 200, pages: 1 });
      setData(result.data.items);
    } catch (error) { message.error(formatApiError(error)); } finally { setLoading(false); }
  };
  useEffect(() => { load(emptyFilters, ''); }, []);

  // 唯一筛选派生数据源：CoverFlow、地图选中态和表格始终消费同一结果。
  const filteredTalents = useMemo(() => data.filter((talent) => {
    const keywordMatched = !appliedKeyword || `${talent.name}${talent.current_position}${talent.skill_tags.join('')}`.toLowerCase().includes(appliedKeyword.toLowerCase());
    return keywordMatched
      && (!appliedFilters.education || talent.education === appliedFilters.education)
      && (!appliedFilters.status || talent.status === appliedFilters.status)
      && (!appliedFilters.organization_name || talent.organization_name === appliedFilters.organization_name)
      && (!appliedFilters.province || talent.province === appliedFilters.province);
  }), [data, appliedKeyword, appliedFilters]);

  const applyFilters = () => { setAppliedFilters({ ...draftFilters }); setAppliedKeyword(keyword); load({ ...draftFilters }, keyword); };
  const resetFilters = () => {
    setDraftFilters(emptyFilters); setAppliedFilters(emptyFilters); setKeyword(''); setAppliedKeyword(''); setSemantic('');
    setData(demoTalents); load(emptyFilters, '');
  };
  const selectProvince = (province?: string) => {
    const next = { ...appliedFilters, province };
    setAppliedFilters(next); setDraftFilters(next);
  };
  const openEdit = (record?: Talent) => {
    setEditing(record || null);
    form.setFieldsValue(record ? { ...record, skill_tags: record.skill_tags } : { gender: 'unknown', status: 'active', work_years: 0, skill_tags: [] });
    setModalOpen(true);
  };
  const save = async () => {
    const values = await form.validateFields(); setSaving(true);
    try {
      if (demo) {
        await sleep(500);
        if (editing) setData((old) => old.map((talent) => talent.id === editing.id ? { ...talent, ...values } : talent));
        else setData((old) => [{ ...demoTalents[0], ...values, id: Date.now() }, ...old]);
      } else {
        if (editing) await request({ method: 'PUT', url: `/talents/${editing.id}`, data: values });
        else await request({ method: 'POST', url: '/talents', data: values });
        await load();
      }
      message.success(editing ? '人才档案已更新' : '人才档案创建成功，AI 画像生成中'); setModalOpen(false);
    } catch (error) { message.error(formatApiError(error)); } finally { setSaving(false); }
  };
  const semanticSearch = async () => {
    if (!semantic.trim()) return; setSemanticLoading(true);
    try {
      if (!demo) {
        const result = await request<{ items: { talent_id: number }[] }>({ method: 'POST', url: '/talents/search/semantic', data: { query: semantic, top_k: 20, filters: appliedFilters } });
        const ids = new Set(result.items.map((item) => item.talent_id));
        setData((old) => old.filter((talent) => ids.has(talent.id)));
      } else { await sleep(800); setData(demoTalents.slice(0, 4)); }
      setAppliedFilters(emptyFilters); setDraftFilters(emptyFilters); setAppliedKeyword('');
      message.success('AI 语义检索完成，简历与人才列表已同步刷新');
    } catch (error) { message.error(formatApiError(error)); } finally { setSemanticLoading(false); }
  };
  const remove = (record: Talent) => modal.confirm({
    title: `确认删除「${record.name}」的档案？`, content: '删除后，该人才关联向量将同步清理，历史业务记录保留。', okButtonProps: { danger: true },
    onOk: async () => { if (!demo) await request({ method: 'DELETE', url: `/talents/${record.id}` }); setData((old) => old.filter((talent) => talent.id !== record.id)); message.success('档案已删除'); },
  });
  const exportData = () => {
    downloadText('人才档案.csv', '姓名,工作地,部门,职位,学历,技能\n' + filteredTalents.map((talent) => `${talent.name},${talent.work_city || talent.province || ''},${talent.organization_name},${talent.current_position},${talent.education},${talent.skill_tags.join('/')}`).join('\n'));
    message.success('当前筛选结果已导出');
  };

  return <>
    <PageHeader title="人才档案" description="统一管理人才主数据，以 AI 数字画像看见每个人的能力与潜力" demo={demo} actions={<><Button icon={<MergeCellsOutlined />} onClick={() => message.info('请选择两份重复档案进行智能合并')}>档案合并</Button><Button icon={<ExportOutlined />} onClick={exportData}>导出</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openEdit()}>新建档案</Button></>} />

    <div className="metric-strip">
      <div className="metric-strip-item"><div className="metric-strip-label">人才总数</div><div className="metric-strip-value">1,286<span className="metric-strip-extra">人</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">在职人才</div><div className="metric-strip-value">1,168<span className="metric-strip-extra text-success">90.8%</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">A 级高潜人才</div><div className="metric-strip-value">306<span className="metric-strip-extra">23.8%</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">本月新增档案</div><div className="metric-strip-value">34<span className="metric-strip-extra text-success">↑ 12.5%</span></div></div>
    </div>

    <div className="semantic-search"><div className="search-icon"><ThunderboltOutlined /></div><div className="semantic-body"><div className="semantic-title">AI 语义搜人</div><div className="semantic-sub">无需设置复杂条件，用一句话描述您要找的人</div><div className="flex gap-2 semantic-input"><Input value={semantic} onChange={(event) => setSemantic(event.target.value)} onPressEnter={semanticSearch} prefix={<RobotOutlined className="text-[#6981ce]" />} placeholder="例如：查找有 3 年以上 AI 项目经验、擅长 RAG 的技术骨干" /><Button type="primary" loading={semanticLoading} icon={<SearchOutlined />} onClick={semanticSearch}>智能搜索</Button></div></div></div>

    <div className="card talent-filter-card">
      <div className="filter-row">
        <Input allowClear value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={applyFilters} placeholder="搜索姓名、职位、技能" prefix={<SearchOutlined />} />
        <Select allowClear placeholder="学历" className="w-[120px]" value={draftFilters.education} onChange={(value) => setDraftFilters((old) => ({ ...old, education: value }))} options={['博士', '硕士', '本科', '大专'].map((value) => ({ value, label: value }))} />
        <Select allowClear placeholder="人才状态" className="w-[125px]" value={draftFilters.status} onChange={(value) => setDraftFilters((old) => ({ ...old, status: value }))} options={[{ value: 'active', label: '在职' }, { value: 'reserve', label: '人才储备' }, { value: 'left', label: '离职' }]} />
        <Select allowClear placeholder="所属部门" className="w-[145px]" value={draftFilters.organization_name} onChange={(value) => setDraftFilters((old) => ({ ...old, organization_name: value }))} options={departments.map((value) => ({ value, label: value }))} />
        <Button type="primary" ghost onClick={applyFilters}>查询</Button><Button onClick={resetFilters}>重置</Button>
        <div className="filter-spacer" /><div className="talent-filter-summary">{appliedFilters.province ? <><span>地域筛选</span><b>{appliedFilters.province}</b><span>·</span></> : <span>全国人才</span>}<span>当前 {filteredTalents.length} 份档案</span></div>
      </div>
    </div>

    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
      <ResumeCoverflow talents={filteredTalents} province={appliedFilters.province} />
    </motion.div>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay: 0.05 }}>
      <TalentMap selectedProvince={appliedFilters.province} onProvinceChange={selectProvince} />
    </motion.div>
    <TalentTable talents={filteredTalents} loading={loading} province={appliedFilters.province} onPortrait={setSelected} onEdit={openEdit} onDelete={remove} />

    <Drawer width={590} open={!!selected} onClose={() => setSelected(null)} title="人才数字画像" extra={<Button type="primary" size="small" icon={<ThunderboltOutlined />} onClick={() => message.success('AI 画像更新任务已提交')}>更新画像</Button>}>
      {selected && <><div className="detail-hero"><PersonAvatar name={selected.name} size={68} className="detail-avatar" /><div className="flex-1"><div className="detail-name">{selected.name} <PotentialTag level={selected.potential_level} /></div><div className="detail-meta">{selected.current_position} · {selected.organization_name}</div><div className="mt-2"><SkillTags tags={selected.skill_tags} max={4} /></div></div><div className="text-right"><div className="score-text text-[26px]">{selected.match_score}</div><div className="muted text-[10px]">综合评分</div></div></div>
        <div className="drawer-section-title">AI 画像摘要 <Tag color="blue" className="text-[9px]">由 Talent Agent 生成</Tag></div><div className="portrait-summary">{selected.portrait_summary}</div>
        <div className="drawer-section-title">能力维度分析</div><div className="flex items-center"><Chart height={235} option={{ radar: { indicator: portraitDimensions.map((dimension) => ({ name: dimension.name, max: 100 })), radius: '68%', axisName: { color: '#758096', fontSize: 11 }, splitArea: { areaStyle: { color: ['#fafbff', '#f4f6fc'] } }, splitLine: { lineStyle: { color: '#e0e6f1' } }, axisLine: { lineStyle: { color: '#e0e6f1' } } }, series: [{ type: 'radar', data: [{ value: portraitDimensions.map((dimension) => dimension.value), areaStyle: { color: 'rgba(70,105,225,.22)' }, lineStyle: { color: '#496de0', width: 2 }, itemStyle: { color: '#496de0' } }] }] }} /><div className="flex-1">{portraitDimensions.slice(0, 4).map((dimension) => <div key={dimension.name} className="mb-3"><div className="flex justify-between text-[11px] mb-1"><span>{dimension.name}</span><b>{dimension.value}</b></div><Progress percent={dimension.value} showInfo={false} size="small" strokeColor="#5574df" /></div>)}</div></div>
        <div className="drawer-section-title">档案信息</div><div className="description-list">{[['性别', selected.gender === 'male' ? '男' : '女'], ['手机号码', selected.mobile], ['最高学历', `${selected.education} · ${selected.school}`], ['专业', selected.major], ['工作年限', `${selected.work_years} 年`], ['工作地', selected.work_city || selected.province || '—'], ['电子邮箱', selected.email || '—']].map(([key, value]) => <div key={key}><div className="description-item-label">{key}</div><div className="description-item-value">{value}</div></div>)}</div>
        <div className="drawer-section-title">AI 发展建议</div><div className="portrait-summary border-l-[#24a082] bg-[#f3faf8]">建议作为「技术专家」序列重点培养对象，参与跨部门 AI 创新项目，补充技术团队管理与业务战略规划经验。</div>
      </>}
    </Drawer>

    <Modal title={editing ? '编辑人才档案' : '新建人才档案'} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={save} confirmLoading={saving} width={700} okText="保存档案">
      <Form form={form} layout="vertical"><Row gutter={16}>
        <Col span={12}><Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}><Input /></Form.Item></Col><Col span={12}><Form.Item name="mobile" label="手机号" rules={[{ required: true, message: '请输入手机号' }]}><Input /></Form.Item></Col>
        <Col span={8}><Form.Item name="gender" label="性别"><Select options={[{ value: 'male', label: '男' }, { value: 'female', label: '女' }, { value: 'unknown', label: '未知' }]} /></Form.Item></Col><Col span={8}><Form.Item name="education" label="最高学历"><Select options={['博士', '硕士', '本科', '大专'].map((value) => ({ value, label: value }))} /></Form.Item></Col><Col span={8}><Form.Item name="work_years" label="工作年限"><Input type="number" suffix="年" /></Form.Item></Col>
        <Col span={12}><Form.Item name="school" label="毕业院校"><Input /></Form.Item></Col><Col span={12}><Form.Item name="major" label="专业"><Input /></Form.Item></Col>
        <Col span={12}><Form.Item name="current_position" label="当前职位"><Input /></Form.Item></Col><Col span={12}><Form.Item name="organization_name" label="所属部门"><Select options={departments.map((value) => ({ value, label: value }))} /></Form.Item></Col>
        <Col span={12}><Form.Item name="province" label="工作省份"><Select allowClear options={['北京', '上海', '广东', '浙江', '江苏', '四川', '湖北'].map((value) => ({ value, label: value }))} /></Form.Item></Col><Col span={12}><Form.Item name="work_city" label="工作城市"><Input /></Form.Item></Col>
        <Col span={24}><Form.Item name="skill_tags" label="技能标签"><Select mode="tags" placeholder="输入技能后回车" /></Form.Item></Col>
      </Row></Form>
    </Modal>
  </>;
}
