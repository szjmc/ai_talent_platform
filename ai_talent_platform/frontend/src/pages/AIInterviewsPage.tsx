import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Button, Col, Collapse, Drawer, Empty, Form, Input, InputNumber, Modal, Progress,
  Radio, Row, Select, Spin, Switch, Table, Tabs, Tag, Tooltip,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  BarChartOutlined, CopyOutlined, EyeOutlined, LinkOutlined, PlusOutlined, ReloadOutlined,
  RobotOutlined, SendOutlined, SoundOutlined, TeamOutlined, VideoCameraOutlined,
} from '@ant-design/icons';
import { Chart, PageHeader, PersonAvatar } from '../components/Common';
import DigitalHuman from '../components/ai/DigitalHuman';
import { aiInterviewsApi, positionsApi, talentsApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText } from '../utils';
import type {
  AiInterviewConfig, AiInterviewDetail, AiInterviewPersona, AiInterviewReport, AiInterviewSession, Position, Talent,
} from '../types';
import './ai-interview.css';

const STATUS_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '待开始' },
  in_progress: { color: 'processing', label: '进行中' },
  completed: { color: 'success', label: '已完成' },
  cancelled: { color: 'default', label: '已取消' },
  expired: { color: 'warning', label: '已过期' },
};
const RECOMMENDATION_META: Record<string, { color: string; label: string }> = {
  strongly_recommend: { color: 'green', label: '强烈推荐' },
  recommend: { color: 'blue', label: '推荐' },
  consider: { color: 'orange', label: '可考虑' },
  not_recommend: { color: 'red', label: '暂不推荐' },
  insufficient: { color: 'default', label: '样本不足' },
};
const DIFFICULTY_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级' };
const STYLE_LABEL: Record<string, string> = {
  warm: '亲和', professional: '专业', rigorous: '严谨', global: '国际化',
};
const DIMENSION_LABELS: Record<string, string> = {
  professional: '专业能力',
  problem_solving: '问题解决',
  communication: '沟通表达',
  culture_fit: '协作与文化',
  potential: '成长潜力',
};
const FALLBACK_PERSONAS: AiInterviewPersona[] = [
  { key: 'friendly_hr', name: '晓岚', title: '资深招聘官 · AI 数字人', style: 'warm', tagline: '关注你的真实经历与成长动机', avatar_theme: 'aurora', language: 'zh-CN', voice_hint: 'female' },
  { key: 'tech_lead', name: '陈拓', title: '技术负责人 · AI 数字人', style: 'professional', tagline: '深挖技术深度与工程判断', avatar_theme: 'cyan', language: 'zh-CN', voice_hint: 'male' },
  { key: 'strict_expert', name: '沈砚', title: '首席面试专家 · AI 数字人', style: 'rigorous', tagline: '高标准、重证据的结构化面试', avatar_theme: 'violet', language: 'zh-CN', voice_hint: 'male' },
  { key: 'global_interviewer', name: 'Ava', title: 'Global Talent Partner · AI', style: 'global', tagline: 'Bilingual, competency-based interview', avatar_theme: 'amber', language: 'en-US', voice_hint: 'female' },
];

function absoluteUrl(url: string) {
  if (!url) return '';
  try {
    const parsed = new URL(url, window.location.origin);
    // 后端可能按 FRONTEND_URL/请求 Host 生成 localhost 链接；在远程预览环境中改写成当前访问域名，
    // 保证复制给候选人的链接真实可打开（生产环境显式配置 INVITE_BASE_URL 时不受影响）。
    const local = new Set(['localhost', '127.0.0.1', '0.0.0.0']);
    if (local.has(parsed.hostname) && !local.has(window.location.hostname)) {
      return `${window.location.origin}${parsed.pathname}${parsed.search}`;
    }
    return parsed.toString();
  } catch { return url; }
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement('textarea');
    area.value = text; area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.appendChild(area); area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
}

export default function AIInterviewsPage() {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<AiInterviewSession[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [status, setStatus] = useState<string>();
  const [keyword, setKeyword] = useState('');

  const [config, setConfig] = useState<AiInterviewConfig | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const [mode, setMode] = useState<'talent' | 'manual'>('talent');
  const [persona, setPersona] = useState('friendly_hr');
  const [talentOptions, setTalentOptions] = useState<Talent[]>([]);
  const [positionOptions, setPositionOptions] = useState<Position[]>([]);
  const [created, setCreated] = useState<{ url: string; name: string; email?: { ok?: boolean; error?: string | null } | null } | null>(null);

  const [selected, setSelected] = useState<AiInterviewSession | null>(null);
  const [detail, setDetail] = useState<AiInterviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const personas = config?.personas?.length ? config.personas : FALLBACK_PERSONAS;
  const activePersona = personas.find((item) => item.key === persona) || personas[0];

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const result = await aiInterviewsApi.list({ page: 1, page_size: 50, status, keyword: keyword || undefined });
      setRows(result.items || []); setTotal(result.total || 0);
    } catch (error) {
      setLoadError(formatApiError(error)); setRows([]); setTotal(0);
    } finally { setLoading(false); }
  }, [status, keyword]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    aiInterviewsApi.config().then((data) => {
      setConfig(data);
      if (data.personas?.length) setPersona(data.personas[0].key);
    }).catch(() => setConfig(null));
  }, []);

  useEffect(() => {
    if (!modalOpen) return;
    talentsApi.list({ page: 1, page_size: 100 }).then((res) => setTalentOptions(res.items || [])).catch(() => setTalentOptions([]));
    positionsApi.list({ page: 1, page_size: 100 }).then((res) => setPositionOptions(res.items || [])).catch(() => setPositionOptions([]));
  }, [modalOpen]);

  const metrics = useMemo(() => {
    const completed = rows.filter((row) => row.status === 'completed');
    const scored = completed.filter((row) => row.total_score != null);
    return {
      total,
      live: rows.filter((row) => row.status === 'in_progress' || row.status === 'pending').length,
      completed: completed.length,
      avg: scored.length ? Math.round((scored.reduce((sum, row) => sum + Number(row.total_score), 0) / scored.length) * 10) / 10 : null,
    };
  }, [rows, total]);

  const submit = async () => {
    let values: Record<string, unknown>;
    try { values = await form.validateFields(); } catch { return; }
    const payload: Record<string, unknown> = {
      position_id: values.position_id,
      title: values.title,
      interviewer_persona: persona,
      language: values.language,
      difficulty: values.difficulty,
      question_count: values.question_count,
      duration_minutes: values.duration_minutes,
      expires_hours: values.expires_hours,
      send_email: Boolean(values.send_email),
    };
    if (mode === 'talent') payload.talent_id = values.talent_id;
    else {
      payload.candidate_name = values.candidate_name;
      payload.candidate_email = values.candidate_email || undefined;
    }
    setSaving(true);
    try {
      const result = await aiInterviewsApi.create(payload);
      setModalOpen(false); form.resetFields(); setMode('talent');
      setCreated({
        url: absoluteUrl(result.url),
        name: result.candidate_name || '候选人',
        email: result.email || null,
      });
      await load();
    } catch (error) { message.error(formatApiError(error)); }
    finally { setSaving(false); }
  };

  const openDetail = async (record: AiInterviewSession) => {
    setSelected(record); setDetail(null); setDetailLoading(true);
    try { setDetail(await aiInterviewsApi.detail(record.id)); }
    catch (error) { message.error(formatApiError(error)); }
    finally { setDetailLoading(false); }
  };

  const copyLink = async (record: AiInterviewSession) => {
    const ok = await copyText(absoluteUrl(record.url));
    if (ok) message.success('面试链接已复制'); else message.warning('复制失败，请手动选择链接');
  };

  const cancelInterview = (record: AiInterviewSession) => {
    modal.confirm({
      title: '取消这场 AI 面试？',
      content: `候选人 ${record.candidate_name} 将无法继续作答，已答内容仍会保留。`,
      okText: '确认取消', okButtonProps: { danger: true }, cancelText: '再想想',
      onOk: async () => {
        try { await aiInterviewsApi.cancel(record.id); message.success('已取消'); await load(); }
        catch (error) { message.error(formatApiError(error)); }
      },
    });
  };

  const columns: ColumnsType<AiInterviewSession> = [
    {
      title: '候选人', dataIndex: 'candidate_name', width: 210,
      render: (_value, record) => (
        <div className="talent-cell">
          <PersonAvatar name={record.candidate_name || '候选人'} size={34} />
          <div>
            <div className="talent-cell-name">{record.candidate_name}</div>
            <div className="talent-cell-role">{record.position_title || '未填写应聘岗位'}{record.candidate_email ? ` · ${record.candidate_email}` : ''}</div>
          </div>
        </div>
      ),
    },
    {
      title: '数字人面试官', dataIndex: 'interviewer', width: 170,
      render: (_value, record) => (
        <div className="aii-host-cell">
          <span className="aii-host-avatar" data-theme={record.interviewer?.avatar_theme || 'aurora'}><RobotOutlined /></span>
          <div>
            <div className="aii-host-name">{record.interviewer?.name || 'AI 面试官'}</div>
            <div className="aii-host-title">{record.interviewer?.title || '数字人面试官'}</div>
          </div>
        </div>
      ),
    },
    {
      title: '面试配置', width: 135,
      render: (_value, record) => (
        <div className="aii-config-cell">
          <span>{record.question_count} 题 · {record.duration_minutes} 分钟</span>
          <span className="muted">{record.language === 'en-US' ? 'English' : '中文'} · {DIFFICULTY_LABEL[record.difficulty] || record.difficulty}</span>
        </div>
      ),
    },
    {
      title: '进度', width: 115,
      render: (_value, record) => {
        const percent = record.status === 'completed' ? 100 : Math.round((record.answered_count / Math.max(1, record.question_count)) * 100);
        return <div className="aii-progress-cell"><Progress percent={percent} size="small" showInfo={false} strokeColor="#315efb" /><span>{record.answered_count}/{record.question_count} 题</span></div>;
      },
    },
    {
      title: '状态', dataIndex: 'status', width: 88,
      render: (value: string) => {
        const meta = STATUS_META[value] || { color: 'default', label: value };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: '评估得分', width: 140,
      render: (_value, record) => record.total_score != null ? (
        <div className="aii-score-cell">
          <div className="aii-score-main"><b>{record.total_score}</b><span>{record.level || '—'}</span></div>
          {record.recommendation && record.recommendation !== 'insufficient' && <Tag color={RECOMMENDATION_META[record.recommendation]?.color || 'default'} className="aii-rec-tag">{RECOMMENDATION_META[record.recommendation]?.label || record.recommendation}</Tag>}
        </div>
      ) : <span className="muted">—</span>,
    },
    {
      title: '操作', width: 185,
      render: (_value, record) => (
        <div className="aii-actions">
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => void openDetail(record)}>详情</Button>
          <Tooltip title="复制候选人链接"><Button type="link" size="small" icon={<CopyOutlined />} onClick={() => void copyLink(record)} /></Tooltip>
          <Tooltip title="打开候选人面试页"><Button type="link" size="small" icon={<VideoCameraOutlined />} onClick={() => window.open(absoluteUrl(record.url), '_blank', 'noopener')} /></Tooltip>
          {['pending', 'in_progress'].includes(record.status) && <Button type="link" size="small" danger onClick={() => cancelInterview(record)}>取消</Button>}
        </div>
      ),
    },
  ];

  const report = (detail?.report || null) as AiInterviewReport | null;
  const radarEntries = report ? Object.entries(report.dimension_scores || {}).filter(([, value]) => value != null) as [string, number][] : [];
  const reportTurns = detail?.turns || [];
  const plan = detail?.plan || [];

  return <>
    <PageHeader
      title="AI 数字人面试"
      description="由 AI 数字人面试官完成首轮结构化面试：自动提问、追问、量化评分并生成可复核的评估报告"
      actions={<>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setModalOpen(true); if (!form.isFieldTouched('question_count')) form.setFieldsValue({ question_count: config?.default_question_count || 6, duration_minutes: config?.default_duration_minutes || 30, expires_hours: config?.default_expires_hours || 72, difficulty: 'middle', language: 'zh-CN', send_email: true }); }}>创建 AI 面试</Button>
      </>}
    />

    {loadError && <Alert type="warning" showIcon message="AI 面试列表加载失败" description={loadError} style={{ marginBottom: 16 }} />}

    <div className="metric-strip">
      <div className="metric-strip-item"><div className="metric-strip-label">累计面试</div><div className="metric-strip-value">{metrics.total}<span className="metric-strip-extra">场</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">待开始 / 进行中</div><div className="metric-strip-value">{metrics.live}<span className="metric-strip-extra">场</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">已完成</div><div className="metric-strip-value">{metrics.completed}<span className="metric-strip-extra">场</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">平均得分</div><div className="metric-strip-value">{metrics.avg ?? '—'}<span className="metric-strip-extra">分</span></div></div>
    </div>

    <div className="card filter-card">
      <div className="filter-row">
        <Select placeholder="面试状态" allowClear style={{ width: 140 }} value={status} onChange={setStatus}
          options={Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.label }))} />
        <Input.Search placeholder="搜索候选人 / 岗位 / 标题" allowClear style={{ width: 260 }} value={keyword}
          onChange={(event) => setKeyword(event.target.value)} onSearch={() => void load()} />
        <Button onClick={() => void load()}>查询</Button>
        <div className="filter-spacer" />
        <span className="muted" style={{ fontSize: 11 }}><RobotOutlined /> 数字人支持语音提问 / 语音作答转写 / 自动追问</span>
      </div>
    </div>

    <div className="card content-card">
      <div className="table-toolbar"><div className="table-title">面试任务<span className="table-count">共 {total} 条</span></div></div>
      <Table rowKey="id" columns={columns} dataSource={rows} loading={loading}
        pagination={{ pageSize: 10, total, showTotal: (n) => `共 ${n} 条` }} scroll={{ x: 1043 }} />
    </div>

    {/* 创建面试 */}
    <Modal open={modalOpen} onCancel={() => setModalOpen(false)} title="创建 AI 数字人面试"
      onOk={() => void submit()} confirmLoading={saving} okText="生成面试链接" width={900} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ question_count: 6, duration_minutes: 30, expires_hours: 72, difficulty: 'middle', language: 'zh-CN', send_email: true }}>
        <Row gutter={24}>
          <Col xs={24} lg={15}>
            <Form.Item label="候选人来源">
              <Radio.Group value={mode} onChange={(event) => setMode(event.target.value)} optionType="button" buttonStyle="solid">
                <Radio.Button value="talent"><TeamOutlined /> 从人才档案选择</Radio.Button>
                <Radio.Button value="manual">手工录入</Radio.Button>
              </Radio.Group>
            </Form.Item>
            {mode === 'talent' ? (
              <Form.Item name="talent_id" label="候选人" rules={[{ required: true, message: '请选择候选人' }]}>
                <Select showSearch placeholder="搜索人才姓名 / 当前职位" optionFilterProp="label" options={talentOptions.map((talent) => ({ value: talent.id, label: `${talent.name}${talent.current_position ? ` · ${talent.current_position}` : ''}` }))} />
              </Form.Item>
            ) : (
              <Row gutter={12}>
                <Col span={12}><Form.Item name="candidate_name" label="候选人姓名" rules={[{ required: true, message: '请填写姓名' }]}><Input placeholder="如：张伟" maxLength={32} /></Form.Item></Col>
                <Col span={12}><Form.Item name="candidate_email" label="邮箱（用于发送链接）" rules={[{ type: 'email', message: '邮箱格式不正确' }]}><Input placeholder="candidate@example.com" maxLength={128} /></Form.Item></Col>
              </Row>
            )}
            <Row gutter={12}>
              <Col span={12}><Form.Item name="position_id" label="应聘岗位（可选）"><Select allowClear showSearch placeholder="选择岗位" optionFilterProp="label" options={positionOptions.map((position) => ({ value: position.id, label: position.title }))} /></Form.Item></Col>
              <Col span={12}><Form.Item name="title" label="面试标题（可选）"><Input placeholder="留空自动生成" maxLength={128} /></Form.Item></Col>
            </Row>

            <Form.Item label="选择数字人面试官" required>
              <div className="aii-persona-grid">
                {personas.map((item) => (
                  <button type="button" key={item.key} className={`aii-persona-card ${persona === item.key ? 'active' : ''}`} onClick={() => setPersona(item.key)}>
                    <span className="aii-persona-dot" data-theme={item.avatar_theme}><RobotOutlined /></span>
                    <span className="aii-persona-info"><b>{item.name}</b><em>{item.title}</em><small>{item.tagline}</small></span>
                  </button>
                ))}
              </div>
            </Form.Item>

            <Row gutter={12}>
              <Col span={8}><Form.Item name="question_count" label="问题数量"><InputNumber min={3} max={config?.max_question_count || 10} style={{ width: '100%' }} /></Form.Item></Col>
              <Col span={8}><Form.Item name="duration_minutes" label="面试时长（分钟）"><InputNumber min={5} max={120} style={{ width: '100%' }} /></Form.Item></Col>
              <Col span={8}><Form.Item name="expires_hours" label="链接有效期（小时）"><InputNumber min={1} max={720} style={{ width: '100%' }} /></Form.Item></Col>
            </Row>
            <Row gutter={12}>
              <Col span={8}><Form.Item name="difficulty" label="难度"><Select options={(config?.difficulties || [{ value: 'junior', label: '初级' }, { value: 'middle', label: '中级' }, { value: 'senior', label: '高级' }])} /></Form.Item></Col>
              <Col span={8}><Form.Item name="language" label="面试语言"><Select options={config?.languages || [{ value: 'zh-CN', label: '中文' }, { value: 'en-US', label: 'English' }]} /></Form.Item></Col>
              <Col span={8}><Form.Item name="send_email" label="邮件发送链接" valuePropName="checked"><Switch checkedChildren="发送" unCheckedChildren="不发" /></Form.Item></Col>
            </Row>
            <Alert type="info" showIcon message="AI 面试会自动生成问题计划；候选人的语音回答将在浏览器转写为文字后提交，服务端只保存对话文本。" />
          </Col>
          <Col xs={24} lg={9}>
            <div className="aii-preview">
              <div className="aii-preview-title"><RobotOutlined /> 数字人预览</div>
              <DigitalHuman name={activePersona?.name || '晓岚'} title={activePersona?.title} theme={activePersona?.avatar_theme} state="idle" />
              <div className="aii-preview-meta">
                <div><span>人设风格</span><b>{STYLE_LABEL[activePersona?.style || 'warm'] || activePersona?.style}</b></div>
                <div><span>面试语言</span><b>{activePersona?.language === 'en-US' ? 'English' : '中文'}</b></div>
                <div><span>追问策略</span><b>每题最多 1 次</b></div>
              </div>
            </div>
          </Col>
        </Row>
      </Form>
    </Modal>

    {/* 创建成功：链接 */}
    <Modal open={!!created} onCancel={() => setCreated(null)} footer={null} title="AI 面试已创建" width={560}>
      {created && <div className="aii-created">
        <div className="aii-created-icon"><LinkOutlined /></div>
        <p>将下面的链接发送给 <b>{created.name}</b>，对方无需登录即可进入数字人面试。</p>
        <Input.TextArea value={created.url} readOnly autoSize={{ minRows: 2, maxRows: 3 }} />
        <div className="aii-created-actions">
          <Button type="primary" icon={<CopyOutlined />} onClick={async () => { const ok = await copyText(created.url); ok ? message.success('链接已复制') : message.warning('复制失败，请手动复制'); }}>复制链接</Button>
          <Button icon={<VideoCameraOutlined />} onClick={() => window.open(created.url, '_blank', 'noopener')}>打开面试页</Button>
        </div>
        {created.email && <Alert style={{ marginTop: 14 }} type={created.email.ok ? 'success' : 'warning'} showIcon
          message={created.email.ok ? '面试链接已发送到候选人邮箱' : `邮件未发送：${created.email.error || '请检查 SMTP 配置'}`} />}
      </div>}
    </Modal>

    {/* 详情 / 报告 */}
    <Drawer width={860} open={!!selected} onClose={() => setSelected(null)} title="AI 面试详情"
      extra={report && <Button size="small" icon={<BarChartOutlined />} onClick={() => downloadText(`AI面试报告-${selected?.candidate_name || selected?.id}.json`, JSON.stringify(report, null, 2))}>导出报告</Button>}>
      {selected && <Spin spinning={detailLoading}>
        <div className="aii-detail-hero">
          <DigitalHuman name={selected.interviewer?.name || 'AI 面试官'} title={selected.interviewer?.title} theme={selected.interviewer?.avatar_theme} state={selected.status === 'completed' ? 'done' : 'idle'} showStatus={false} />
          <div className="aii-detail-main">
            <div className="aii-detail-name">{selected.candidate_name}<Tag color={STATUS_META[selected.status]?.color}>{STATUS_META[selected.status]?.label}</Tag></div>
            <div className="aii-detail-meta">{selected.title}{selected.position_title ? ` · ${selected.position_title}` : ''}</div>
            <div className="aii-detail-tags">
              <span><RobotOutlined /> {selected.interviewer?.name}</span>
              <span>{selected.question_count} 题 · {selected.duration_minutes} 分钟</span>
              <span>{selected.language === 'en-US' ? 'English' : '中文'} · {DIFFICULTY_LABEL[selected.difficulty]}</span>
            </div>
          </div>
          <div className="aii-detail-score">
            <b>{selected.total_score != null ? selected.total_score : '—'}</b>
            <span>{selected.level || '待评估'}</span>
            {selected.recommendation && <Tag color={RECOMMENDATION_META[selected.recommendation]?.color}>{RECOMMENDATION_META[selected.recommendation]?.label}</Tag>}
          </div>
        </div>

        <Tabs items={[
          {
            key: 'report', label: '评估报告',
            children: report ? <div className="aii-report">
              <div className="aii-report-summary"><RobotOutlined /> {report.summary}</div>
              <Row gutter={20}>
                <Col xs={24} md={10}>
                  <div className="aii-chart-title">能力维度</div>
                  {radarEntries.length ? <Chart height={300} option={{
                    textStyle: { fontFamily: "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif" },
                    radar: {
                      center: ['50%', '52%'], radius: '68%',
                      indicator: radarEntries.map(([key]) => ({ name: report.dimension_labels[key] || key, max: 100 })),
                      axisName: { color: '#5f6d86', fontSize: 11 },
                      splitLine: { lineStyle: { color: '#e3e9f4' } },
                      splitArea: { areaStyle: { color: ['#fbfcff', '#f5f8ff'] } },
                    },
                    series: [{ type: 'radar', symbolSize: 5, data: [{ value: radarEntries.map(([, value]) => value), areaStyle: { color: 'rgba(72,108,227,.22)' }, lineStyle: { color: '#496ce0', width: 2 }, itemStyle: { color: '#496ce0' } }] }],
                  }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无维度得分" />}
                </Col>
                <Col xs={24} md={14}>
                  <div className="aii-chart-title">维度得分与完成度</div>
                  {Object.entries(report.dimension_scores || {}).map(([key, value]) => (
                    <div className="aii-dim-row" key={key}>
                      <span>{report.dimension_labels[key] || key}</span>
                      <Progress percent={value == null ? 0 : Math.round(value)} size="small" strokeColor={value == null ? '#d5dbe6' : '#315efb'} format={() => value == null ? '未测' : value} />
                    </div>
                  ))}
                  <div className="aii-integrity">
                    <div><span>完成率</span><b>{Math.round((report.completion_rate || 0) * 100)}%</b></div>
                    <div><span>作答题目</span><b>{report.answered_count}/{report.question_count}</b></div>
                    <div><span>评分方式</span><b>{report.scoring_method?.startsWith('rule+llm') ? '规则 + 大模型润色' : '规则量化'}</b></div>
                  </div>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}><div className="aii-section-title">优势亮点</div><ul className="aii-list good">{(report.strengths || []).map((item) => <li key={item}>{item}</li>)}</ul></Col>
                <Col span={12}><div className="aii-section-title">待提升项</div>{(report.weaknesses || []).length ? <ul className="aii-list warn">{report.weaknesses.map((item) => <li key={item.dimension}>{item.label}：{item.score} 分（{item.gap}）</li>)}</ul> : <div className="aii-empty-inline">暂无低于合格线的维度</div>}</Col>
              </Row>
              <div className="aii-section-title">发展建议</div>
              <ol className="aii-suggestions">{(report.suggestions || []).map((item) => <li key={item}>{item}</li>)}</ol>
              <div className="aii-section-title">逐题复盘</div>
              <Collapse ghost items={(report.per_question || []).map((item, index) => ({
                key: String(index),
                label: <div className="aii-q-label">
                  <span className="aii-q-index">Q{index + 1}</span>
                  <span className="aii-q-text">{item.question || ''}</span>
                  <Tag>{item.dimension_label || ''}</Tag>
                  <b className={item.score == null ? 'muted' : 'score-text'}>{item.score == null ? '未答' : `${item.score} 分`}</b>
                </div>,
                children: <div className="aii-q-body">
                  <div className="aii-q-block"><span>候选人回答</span><p>{item.answer || '未作答'}</p></div>
                  <div className="aii-q-block"><span>评语</span><p>{item.feedback || '—'}</p></div>
                  {!!item.signals && <div className="aii-signals">
                    <span>关键点命中：{item.signals.keywords_hit?.join('、') || '—'}</span>
                    <span>数据/指标：{item.signals.numbers?.join('、') || '—'}</span>
                    <span>回答字数：{item.signals.chinese_chars ?? '—'}</span>
                  </div>}
                </div>,
              }))} />
            </div> : <Empty description={selected.status === 'completed' ? '本次面试暂无报告' : '面试结束后自动生成评估报告'} />,
          },
          {
            key: 'turns', label: `对话记录 (${reportTurns.length})`,
            children: reportTurns.length ? <div className="aii-transcript">
              {reportTurns.map((turn) => (
                <div key={`${turn.seq}-${turn.role}`} className={`aii-turn ${turn.role}`}>
                  <div className="aii-turn-meta">{turn.role === 'interviewer' ? `${selected.interviewer?.name || '数字人'} · 提问` : '候选人 · 回答'}{turn.score != null ? ` · ${turn.score} 分` : ''}{turn.input_mode === 'voice' ? ' · 语音' : ''}</div>
                  <div className="aii-turn-bubble">{turn.content}</div>
                  {turn.feedback && <div className="aii-turn-feedback">{turn.feedback}</div>}
                </div>
              ))}
            </div> : <Empty description="暂无对话记录" />,
          },
          {
            key: 'plan', label: `问题计划 (${plan.length})`,
            children: plan.length ? <div className="aii-plan">
              {plan.map((item) => (
                <div className="aii-plan-item" key={item.id}>
                  <div className="aii-plan-top"><span className="aii-q-index">Q{item.id}</span><b>{item.text}</b><Tag>{report?.dimension_labels?.[item.dimension] || DIMENSION_LABELS[item.dimension] || item.dimension}</Tag></div>
                  <div className="aii-plan-meta"><span>考察意图：{item.intent || '—'}</span><span>关键点：{(item.keywords || []).join('、') || '—'}</span>{item.follow_up && <span>追问：{item.follow_up}</span>}</div>
                </div>
              ))}
            </div> : <Empty description="暂无问题计划" />,
          },
        ]} />
      </Spin>}
    </Drawer>
  </>;
}
