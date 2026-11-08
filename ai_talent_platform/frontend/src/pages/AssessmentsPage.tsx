import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, InputNumber, Modal, Row, Select, Spin, Table, Tag, Upload } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { BarChartOutlined, ClockCircleOutlined, CloudUploadOutlined, EyeOutlined, FileAddOutlined, PlusOutlined, RobotOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { Chart, PageHeader, PersonAvatar, StatusBadge } from '../components/Common';
import { analyticsApi, assessmentsApi, resumesApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { downloadText, formatDate } from '../utils';
import dayjs from 'dayjs';
import type { Assessment, AssessmentReport, AssessmentSummary } from '../types';

export default function AssessmentsPage() {
  const [data, setData] = useState<Assessment[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const [open, setOpen] = useState(false); const [selected, setSelected] = useState<Assessment | null>(null);
  const [report, setReport] = useState<AssessmentReport | null>(null); const [reportLoading, setReportLoading] = useState(false);
  const [page, setPage] = useState(1); const [saving, setSaving] = useState(false); const [status, setStatus] = useState<string>(); const [summary, setSummary] = useState<AssessmentSummary | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null); const { message } = App.useApp(); const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try { const res = await assessmentsApi.list({ page, page_size: 10, status }); setData(res.items || []); setTotal(res.total || 0); }
    catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, [status, page]);
  useEffect(() => { void load(); analyticsApi.assessmentSummary().then(setSummary).catch(() => setSummary(null)); }, [load]);

  const create = async () => {
    const values = await form.validateFields();
    if (!resumeFile) { message.warning('请上传候选人简历文件，后端将基于简历自动解析并组卷'); return; }
    setSaving(true);
    try {
      const uploaded = await resumesApi.upload(resumeFile);
      if (!uploaded.file_id) throw new Error('简历上传成功但未返回文件 ID，无法创建测评');
      const created = await assessmentsApi.create({ file_id: uploaded.file_id, title: values.title || undefined, question_count: values.question_count, deadline: values.deadline?.toISOString() });
      message.success(`测评创建成功，共 ${created.question_count} 题${created.email && (created.email as { ok?: boolean }).ok ? '，邮件已发送' : '，邮件发送结果请查看详情'}`);
      setOpen(false); form.resetFields(); setResumeFile(null); await load();
    } catch (error) { message.error(formatApiError(error)); } finally { setSaving(false); }
  };
  const openReport = async (record: Assessment) => {
    setSelected(record); setReport(null); setReportLoading(true);
    try { const result = await assessmentsApi.report(record.id); setReport(result.report || null); } catch (error) { message.error(formatApiError(error)); }
    finally { setReportLoading(false); }
  };
  const generateReport = async (record: Assessment) => {
    setSelected(record); setReport(null); setReportLoading(true);
    try { setReport(await assessmentsApi.generateReport(record.id)); await load(); }
    catch (error) { message.error(formatApiError(error)); }
    finally { setReportLoading(false); }
  };
  const enterExam = async (record: Assessment) => {
    try { const detail = await assessmentsApi.detail(record.id); if (detail.exam_url) window.open(detail.exam_url, '_blank', 'noopener'); else message.warning('后端未返回考试链接'); }
    catch (error) { message.error(formatApiError(error)); }
  };
  const columns: ColumnsType<Assessment> = [
    { title: '测评任务', dataIndex: 'title', width: 260, render: (v, r) => <div><div className="talent-cell-name">{v}</div><div className="talent-cell-role">测评 ID：{r.id}</div></div> },
    { title: '测评对象', dataIndex: 'talent_id', width: 150, render: (v, r) => <div className="talent-cell"><PersonAvatar name={r.talent_name || `人才${v}`} size={30}/><span>{r.talent_name || `人才 #${v}`}</span></div> },
    { title: '题量', dataIndex: 'question_count', width: 75, render: (v) => `${v} 题` },
    { title: '截止时间', dataIndex: 'deadline', width: 155, render: (v) => <span><ClockCircleOutlined style={{ marginRight: 5, color: '#98a2b3' }}/>{v ? formatDate(v, true) : '未设置'}</span> },
    { title: '状态', dataIndex: 'status', width: 95, render: (v) => <StatusBadge status={v}/> },
    { title: '测评得分', dataIndex: 'total_score', width: 120, render: (v, r) => v != null ? <span><b className="score-text">{v}</b>{r.level && <Tag color={v >= 90 ? 'green' : 'blue'} style={{ marginLeft: 7 }}>{r.level}</Tag>}</span> : <span className="muted">{r.status === 'submitted' ? '待评分' : r.status === 'scored' ? '暂无分数' : r.status === 'in_progress' ? '作答中' : '尚未作答'}</span> },
    { title: '操作', width: 180, render: (_, r) => <>{r.report_available ? <Button type="link" size="small" icon={<EyeOutlined/>} onClick={() => openReport(r)}>能力报告</Button> : <Button type="link" size="small" disabled={r.status !== 'scored'} onClick={() => Modal.confirm({ title: '生成综合报告？', content: '将根据现有笔试及面试证据生成报告并更新列表得分；尚无面试记录时仅使用已有证据。', onOk: () => generateReport(r) })}>生成报告</Button>}{r.status === 'in_progress' && <Button type="link" size="small" onClick={() => enterExam(r)}>进入测评</Button>}</> },
  ];
  const radarEntries = report ? Object.entries(report.radar).filter(([, v]) => v != null) as [string, number][] : [];
  return <>
    <PageHeader title="AI 智能测评" description="千人千卷、自适应测评与 AI 深度研判，科学量化人才能力" actions={<><Button icon={<FileAddOutlined/>} onClick={() => message.info('题库管理请使用后端 /knowledge/questions 接口')}>题库管理</Button><Button type="primary" icon={<PlusOutlined/>} onClick={() => setOpen(true)}>创建测评</Button></>}/>
    {loadError && <Alert type="warning" showIcon message="测评列表加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">累计测评</div><div className="metric-strip-value">{summary?.record_count ?? total}<span className="metric-strip-extra">条记录</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">已评分</div><div className="metric-strip-value">{summary?.scored_count ?? '—'}<span className="metric-strip-extra">条</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">平均得分</div><div className="metric-strip-value">{summary?.average_score ?? '—'}<span className="metric-strip-extra">分</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">参与人才</div><div className="metric-strip-value">{summary?.talent_count ?? '—'}<span className="metric-strip-extra">人</span></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Select placeholder="测评状态" allowClear style={{ width: 130 }} value={status} onChange={v => { setPage(1); setStatus(v); }} options={[{ value: 'pending', label: '待开始' },{ value: 'in_progress', label: '作答中' },{ value: 'submitted', label: '已提交' },{ value: 'scored', label: '已评分' }]}/><Button onClick={() => void load()}>刷新</Button><div className="filter-spacer"/><span className="muted" style={{ fontSize: 11 }}><SafetyCertificateOutlined className="text-success"/> 防泄密作答 · 标准答案不下发</span></div></div>
    <div className="card content-card"><div className="table-toolbar"><div className="table-title">测评任务列表<span className="table-count">共 {total} 条</span></div></div><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={{ current: page, pageSize: 10, total, showSizeChanger: false, onChange: setPage, showTotal: (n) => `共 ${n} 条` }} scroll={{ x: 1150 }}/></div>
    <Modal open={open} onCancel={() => setOpen(false)} title="创建 AI 智能测评" onOk={create} confirmLoading={saving} okText="智能组卷并创建" width={620}><Form form={form} layout="vertical" initialValues={{ question_count: 5 }}><Form.Item name="title" label="测评名称"><Input placeholder="例如：技术序列高级人才能力测评"/></Form.Item><Row gutter={16}><Col span={12}><Form.Item name="question_count" label="题目数量"><InputNumber min={1} max={50} style={{ width: '100%' }}/></Form.Item></Col><Col span={12}><Form.Item label="候选人简历" required><Upload maxCount={1} beforeUpload={(file) => { setResumeFile(file); return false; }} onRemove={() => setResumeFile(null)} fileList={resumeFile ? [{ uid: '1', name: resumeFile.name }] : []}><Button icon={<CloudUploadOutlined/>}>选择简历文件</Button></Upload></Form.Item></Col></Row><Form.Item name="deadline" label="截止时间（可选，超过后无法交卷）" rules={[{ validator: (_, value) => !value || value.isAfter(dayjs()) ? Promise.resolve() : Promise.reject(new Error('截止时间必须晚于当前时间')) }]}><DatePicker showTime style={{ width: '100%' }} placeholder="未设置则不限制截止时间" /></Form.Item><div className="portrait-summary"><RobotOutlined/> 后端将解析简历关键字，从授权题库智能组卷，并按简历邮箱推送笔试链接。</div></Form></Modal>
    <Drawer width={620} open={!!selected} onClose={() => setSelected(null)} title="AI 能力评估报告" extra={report && <Button size="small" icon={<BarChartOutlined/>} onClick={() => downloadText(`测评报告-${selected?.id}.json`, JSON.stringify(report, null, 2))}>导出报告</Button>}>
      {selected && <Spin spinning={reportLoading}>{report ? <><div className="detail-hero"><PersonAvatar name={selected.talent_name || `人才${selected.talent_id}`} size={62}/><div style={{ flex: 1 }}><div className="detail-name" style={{ fontSize: 19 }}>{selected.talent_name || `人才 #${selected.talent_id}`}</div><div className="detail-meta">{selected.title}</div></div><div style={{ textAlign: 'right' }}><div className="score-text" style={{ fontSize: 30 }}>{report.total_score}</div><Tag color="green">{report.level}</Tag></div></div><div className="drawer-section-title">能力维度得分</div>{radarEntries.length ? <Chart height={280} option={{ radar: { indicator: radarEntries.map(([key]) => ({ name: report.radar_labels[key] || key, max: 100 })), axisName: { color: '#6c788b', fontSize: 11 } }, series: [{ type: 'radar', data: [{ value: radarEntries.map(([, v]) => v), areaStyle: { color: 'rgba(72,108,227,.25)' }, lineStyle: { color: '#496ce0' } }] }] }}/> : <div className="empty-hint">暂无可用维度得分</div>}<div className="drawer-section-title">AI 综合研判</div><div className="portrait-summary">{report.conclusion}</div><div className="drawer-section-title">核心优势</div><Row gutter={12}>{report.strengths.map((s) => <Col span={12} key={s}><div style={{ background: '#f0faf6', padding: 14, borderRadius: 8, color: '#347d68', fontSize: 12, marginBottom: 12 }}>{s}</div></Col>)}</Row><div className="drawer-section-title">发展建议</div><div style={{ fontSize: 13, lineHeight: 25, color: '#59667a' }}>{report.suggestions.map((s, i) => <div key={s}>{i + 1}. {s}</div>)}</div></> : <div className="empty-hint">{reportLoading ? '报告加载中…' : '报告尚未生成或加载失败；有分数不代表已生成综合报告'}</div>}</Spin>}
    </Drawer>
  </>;
}
