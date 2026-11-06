import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, InputNumber, Modal, Progress, Select, Spin, Table, Tag, Upload } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CheckCircleFilled, CloudUploadOutlined, FileImageOutlined, FilePdfOutlined, FileWordOutlined, LinkOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { resumesApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatDate, formatFileSize } from '../utils';
import type { Resume, ResumeParseResult } from '../types';

export default function ResumesPage() {
  const [data, setData] = useState<Resume[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const [status, setStatus] = useState<string>();
  const [selected, setSelected] = useState<Resume | null>(null); const [result, setResult] = useState<ResumeParseResult | null>(null);
  const [resultLoading, setResultLoading] = useState(false); const [parsing, setParsing] = useState<number[]>([]);
  const [bindTarget, setBindTarget] = useState<Resume | null>(null); const [bindTalentId, setBindTalentId] = useState<number | null>(null);
  const { message } = App.useApp();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try { const res = await resumesApi.list({ page: 1, page_size: 50, status }); setData(res.items || []); setTotal(res.total || 0); }
    catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, [status]);
  useEffect(() => { void load(); }, [load]);

  const pollResult = async (record: Resume) => {
    for (let i = 0; i < 15; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const res = await resumesApi.result(record.id);
      if (res.status === 'success' || res.status === 'failed') {
        setData((old) => old.map((r) => r.id === record.id ? { ...r, status: res.status as Resume['status'], accuracy: res.accuracy, parsed_at: res.parsed_at } : r));
        return res;
      }
    }
    return null;
  };
  const parse = async (record: Resume) => {
    setParsing((old) => [...old, record.id]); setData((old) => old.map((r) => r.id === record.id ? { ...r, status: 'parsing' } : r));
    try {
      await resumesApi.parse(record.id, record.status === 'failed');
      const res = await pollResult(record);
      if (res?.status === 'success') message.success('AI 简历解析完成');
      else if (res?.status === 'failed') message.error(res.error || '简历解析失败');
      else message.info('解析任务已受理，请稍后刷新查看结果');
    } catch (error) { message.error(formatApiError(error)); }
    finally { setParsing((old) => old.filter((id) => id !== record.id)); }
  };
  const openResult = async (record: Resume) => {
    setSelected(record); setResult(null); setResultLoading(true);
    try { setResult(await resumesApi.result(record.id)); } catch (error) { message.error(formatApiError(error)); }
    finally { setResultLoading(false); }
  };
  const bind = async () => {
    if (!bindTarget || !bindTalentId) { message.warning('请输入要绑定的人才 ID'); return; }
    try { await resumesApi.bind(bindTarget.id, bindTalentId); message.success('简历已绑定人才档案'); setBindTarget(null); setBindTalentId(null); await load(); }
    catch (error) { message.error(formatApiError(error)); }
  };
  const batchParse = async () => {
    const targets = data.filter((r) => r.status === 'uploaded' || r.status === 'failed');
    if (!targets.length) { message.info('当前没有待解析或失败简历'); return; }
    for (const record of targets) await parse(record);
  };
  const successRows = data.filter((r) => r.status === 'success');
  const avgAccuracy = successRows.length ? successRows.reduce((acc, r) => acc + (r.accuracy || 0), 0) / successRows.length : null;
  const columns: ColumnsType<Resume> = [
    { title: '简历文件', dataIndex: 'file_name', render: (_, r) => <div className="talent-cell"><div className="doc-icon">{r.file_type === 'pdf' ? <FilePdfOutlined/> : (r.file_type || '').includes('doc') ? <FileWordOutlined/> : <FileImageOutlined/>}</div><div><div className="talent-cell-name">{r.file_name}</div><div className="talent-cell-role">{(r.file_type || '').toUpperCase()} · {formatFileSize(r.file_size)}</div></div></div> },
    { title: '关联人才', dataIndex: 'talent_id', width: 150, render: (v) => v ? `人才 #${v}` : <span className="muted">待绑定</span> },
    { title: '上传时间', dataIndex: 'created_at', width: 150, render: (v) => formatDate(v, true) },
    { title: '解析状态', dataIndex: 'status', width: 100, render: (v) => <StatusBadge status={v}/> },
    { title: '解析置信度', dataIndex: 'accuracy', width: 160, render: (v) => v != null ? <div className="flex items-center gap-8"><Progress percent={Math.round(v * 100)} showInfo={false} size="small" style={{ width: 75 }} strokeColor={v > .95 ? '#1b9d79' : '#5273df'}/><span>{(v * 100).toFixed(0)}%</span></div> : '—' },
    { title: '操作', width: 230, render: (_, r) => <div><Button type="link" size="small" disabled={r.status !== 'success'} onClick={() => openResult(r)}>解析结果</Button><Button type="link" size="small" loading={parsing.includes(r.id)} disabled={r.status === 'parsing'} onClick={() => parse(r)}>{r.status === 'failed' ? '重试' : 'AI 解析'}</Button><Button type="link" size="small" disabled={r.status !== 'success'} icon={<LinkOutlined/>} onClick={() => { setBindTarget(r); setBindTalentId(r.talent_id || null); }}>绑定</Button></div> },
  ];
  const basic = result?.parsed?.basic_info || {};
  return <>
    <PageHeader title="简历智能解析" description="多格式简历批量导入，AI 自动提取、清洗、打标并生成标准化人才档案" actions={<Button icon={<RobotOutlined/>} onClick={batchParse}>批量解析</Button>}/>
    {loadError && <Alert type="error" showIcon message="简历列表加载失败" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">简历总量</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">份</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页解析成功</div><div className="metric-strip-value">{successRows.length}<span className="metric-strip-extra">份</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">平均解析准确率</div><div className="metric-strip-value">{avgAccuracy != null ? (avgAccuracy * 100).toFixed(1) : '—'}<span className="metric-strip-extra">%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">解析中</div><div className="metric-strip-value">{data.filter((r) => r.status === 'parsing').length}<span className="metric-strip-extra">份</span></div></div></div>
    <div className="card upload-area mb-16" style={{ padding: 16 }}><Upload.Dragger multiple accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png" showUploadList={false} customRequest={async ({ file, onSuccess, onError }) => { try { const uploaded = await resumesApi.upload(file as File); setData((old) => [uploaded, ...old]); setTotal((old) => old + 1); message.success(`${uploaded.file_name} 上传成功${uploaded.duplicate ? '（重复文件已秒传）' : ''}`); onSuccess?.({}); } catch (e) { message.error(formatApiError(e)); onError?.(e as Error); } }}><p className="ant-upload-drag-icon"><CloudUploadOutlined/></p><p className="ant-upload-text">点击或拖拽简历到此区域上传</p><p className="ant-upload-hint">支持 PDF、Word、TXT、图片及扫描件，单文件不超过 20 MB，可批量上传</p><Button type="primary" style={{ marginTop: 8 }} icon={<CloudUploadOutlined/>}>选择简历文件</Button></Upload.Dragger></div>
    <div className="card filter-card"><div className="filter-row"><span style={{ fontWeight: 600 }}>解析任务</span><Select placeholder="全部状态" allowClear style={{ width: 130 }} value={status} onChange={setStatus} options={[{ value: 'success', label: '解析成功' },{ value: 'parsing', label: '解析中' },{ value: 'uploaded', label: '待解析' },{ value: 'failed', label: '解析失败' }]}/><div className="filter-spacer"/><Button onClick={() => void load()}>刷新</Button><span className="muted" style={{ fontSize: 11 }}><CheckCircleFilled className="text-success"/> 文件经 SHA-256 校验，重复简历自动秒传</span></div></div>
    <div className="card content-card"><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={{ pageSize: 10, total: data.length, showTotal: (n) => `共 ${n} 份简历` }} scroll={{ x: 1100 }}/></div>
    <Drawer width={620} open={!!selected} onClose={() => setSelected(null)} title="AI 简历解析结果" extra={result?.accuracy != null ? <Tag color="success"><CheckCircleFilled/> 置信度 {(result.accuracy * 100).toFixed(0)}%</Tag> : null}>
      {selected && <Spin spinning={resultLoading}><div className="detail-hero"><div className="ai-logo" style={{ width: 50, height: 50 }}><RobotOutlined/></div><div><div className="detail-name" style={{ fontSize: 18 }}>{result?.status === 'success' ? '结构化解析完成' : '解析结果'}</div><div className="detail-meta">{selected.file_name}</div></div></div>
      {result?.error && <Alert type="error" showIcon message={result.error} style={{ marginBottom: 12 }}/>}
      <div className="drawer-section-title">基本信息</div><div className="description-list">{Object.entries(basic).length ? Object.entries(basic).map(([k, v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{String(v ?? '—')}</div></div>) : <div className="empty-hint">暂无结构化基本信息</div>}</div>
      <div className="drawer-section-title">技能标签</div><div>{(result?.parsed?.skills || []).map((x) => <Tag color="geekblue" key={x}>{x}</Tag>)}</div>
      <div className="drawer-section-title">工作经历</div>{(result?.parsed?.work_experience || []).map((exp, index) => <div className="portrait-summary" key={index}><b>{exp.company}{exp.title ? ` · ${exp.title}` : ''}</b><div className="muted" style={{ margin: '5px 0' }}>{exp.start_date || '—'} — {exp.end_date || '至今'}</div>{exp.description || ''}</div>)}
      <div className="drawer-section-title">AI 质量检查</div><div style={{ padding: 14, background: '#f0faf6', borderRadius: 8, color: '#34836d', fontSize: 12 }}>{(result?.warnings || []).length ? result?.warnings.join('；') : '未发现关键字段缺失警告'}</div>
      <Button type="primary" block style={{ marginTop: 22 }} icon={<ThunderboltOutlined/>} onClick={() => { setBindTarget(selected); setBindTalentId(selected.talent_id || null); }}>绑定人才档案</Button></Spin>}
    </Drawer>
    <Modal title="绑定简历到人才档案" open={!!bindTarget} onCancel={() => setBindTarget(null)} onOk={bind} okText="确认绑定"><p>简历：{bindTarget?.file_name}</p><InputNumber style={{ width: '100%' }} placeholder="请输入目标人才 ID" min={1} value={bindTalentId} onChange={(v) => setBindTalentId(v)}/></Modal>
  </>;
}
