import { useState } from 'react';
import { App, Button, Drawer, Modal, Progress, Select, Table, Tag, Upload } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CheckCircleFilled, CloudUploadOutlined, FileImageOutlined, FilePdfOutlined, FileWordOutlined, LinkOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { demoResumes } from '../services/mockData';
import { useAuth } from '../store/AuthContext';
import { formatApiError, request } from '../services/api';
import { formatDate, formatFileSize, sleep } from '../utils';
import type { Resume } from '../types';

export default function ResumesPage() {
  const [data, setData] = useState(demoResumes); const [selected, setSelected] = useState<Resume | null>(null); const [parsing, setParsing] = useState<number[]>([]);
  const { demo } = useAuth(); const { message } = App.useApp();
  const parse = async (record: Resume) => {
    setParsing((old) => [...old, record.id]); setData((old) => old.map((r) => r.id === record.id ? { ...r, status: 'parsing' } : r));
    try {
      if (!demo) await request({ method: 'POST', url: `/resumes/${record.id}/parse`, data: { force: record.status === 'failed' } });
      else { await sleep(1600); setData((old) => old.map((r) => r.id === record.id ? { ...r, status: 'success', accuracy: .96, parsed_at: new Date().toISOString() } : r)); }
      message.success('AI 简历解析完成，已提取 28 项结构化信息');
    } catch (e) { message.error(formatApiError(e)); } finally { setParsing((old) => old.filter((id) => id !== record.id)); }
  };
  const columns: ColumnsType<Resume> = [
    { title: '简历文件', dataIndex: 'file_name', render: (_, r) => <div className="talent-cell"><div className="doc-icon">{r.file_type === 'pdf' ? <FilePdfOutlined/> : r.file_type.includes('doc') ? <FileWordOutlined/> : <FileImageOutlined/>}</div><div><div className="talent-cell-name">{r.file_name}</div><div className="talent-cell-role">{r.file_type.toUpperCase()} · {formatFileSize(r.file_size)}</div></div></div> },
    { title: '关联人才', dataIndex: 'talent_name', width: 150, render: (v) => v || <span className="muted">待绑定</span> },
    { title: '上传时间', dataIndex: 'created_at', width: 150, render: (v) => formatDate(v, true) },
    { title: '解析状态', dataIndex: 'status', width: 100, render: (v) => <StatusBadge status={v}/> },
    { title: '解析置信度', dataIndex: 'accuracy', width: 160, render: (v) => v ? <div className="flex items-center gap-8"><Progress percent={Math.round(v * 100)} showInfo={false} size="small" style={{ width: 75 }} strokeColor={v > .95 ? '#1b9d79' : '#5273df'}/><span>{(v * 100).toFixed(0)}%</span></div> : '—' },
    { title: '操作', width: 230, render: (_, r) => <div><Button type="link" size="small" disabled={r.status !== 'success'} onClick={() => setSelected(r)}>解析结果</Button><Button type="link" size="small" loading={parsing.includes(r.id)} disabled={r.status === 'parsing'} onClick={() => parse(r)}>{r.status === 'failed' ? '重试' : 'AI 解析'}</Button><Button type="link" size="small" disabled={r.status !== 'success'} icon={<LinkOutlined/>} onClick={() => message.success('已创建人才档案并完成绑定')}>绑定</Button></div> },
  ];
  return <>
    <PageHeader title="简历智能解析" description="多格式简历批量导入，AI 自动提取、清洗、打标并生成标准化人才档案" demo={demo} actions={<Button icon={<RobotOutlined/>} onClick={() => message.success('批量解析任务已启动')}>批量解析</Button>}/>
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">简历总量</div><div className="metric-strip-value">3,842<span className="metric-strip-extra">份</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">今日解析</div><div className="metric-strip-value">128<span className="metric-strip-extra text-success">↑ 18.6%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">平均解析准确率</div><div className="metric-strip-value">98.2<span className="metric-strip-extra">%</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">平均解析耗时</div><div className="metric-strip-value">16.8<span className="metric-strip-extra">秒 / 份</span></div></div></div>
    <div className="card upload-area mb-16" style={{ padding: 16 }}><Upload.Dragger multiple accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png" showUploadList={false} customRequest={async ({ file, onSuccess, onError }) => { try { const f = file as File; if (!demo) { const form = new FormData(); form.append('file', f); await request({ method: 'POST', url: '/resumes/upload', data: form, headers: { 'Content-Type': 'multipart/form-data' } }); } else await sleep(650); const item: Resume = { id: Date.now(), file_name: f.name, file_size: f.size, file_type: f.name.split('.').pop() || '', status: 'uploaded', created_at: new Date().toISOString() }; setData((old) => [item, ...old]); message.success(`${f.name} 上传成功`); onSuccess?.({}); } catch (e) { message.error(formatApiError(e)); onError?.(e as Error); } }}><p className="ant-upload-drag-icon"><CloudUploadOutlined/></p><p className="ant-upload-text">点击或拖拽简历到此区域上传</p><p className="ant-upload-hint">支持 PDF、Word、TXT、图片及扫描件，单文件不超过 20 MB，可批量上传</p><Button type="primary" style={{ marginTop: 8 }} icon={<CloudUploadOutlined/>}>选择简历文件</Button></Upload.Dragger></div>
    <div className="card filter-card"><div className="filter-row"><span style={{ fontWeight: 600 }}>解析任务</span><Select placeholder="全部状态" style={{ width: 130 }} options={[{ value: 'success', label: '解析成功' },{ value: 'parsing', label: '解析中' },{ value: 'uploaded', label: '待解析' },{ value: 'failed', label: '解析失败' }]}/><div className="filter-spacer"/><span className="muted" style={{ fontSize: 11 }}><CheckCircleFilled className="text-success"/> 文件经 SHA-256 校验，重复简历自动秒传</span></div></div>
    <div className="card content-card"><Table rowKey="id" columns={columns} dataSource={data} pagination={{ pageSize: 10, showTotal: (n) => `共 ${n} 份简历` }} scroll={{ x: 1100 }}/></div>
    <Drawer width={620} open={!!selected} onClose={() => setSelected(null)} title="AI 简历解析结果" extra={<Tag color="success"><CheckCircleFilled/> 置信度 96%</Tag>}>
      {selected && <><div className="detail-hero"><div className="ai-logo" style={{ width: 50, height: 50 }}><RobotOutlined/></div><div><div className="detail-name" style={{ fontSize: 18 }}>结构化解析完成</div><div className="detail-meta">{selected.file_name}</div></div></div><div className="drawer-section-title">基本信息</div><div className="description-list">{[['姓名','张明远'],['手机号码','138****0001'],['电子邮箱','zhangmy@example.com'],['工作年限','8 年'],['当前职位','高级算法工程师'],['最高学历','硕士 · 浙江大学']].map(([k,v]) => <div key={k}><div className="description-item-label">{k}</div><div className="description-item-value">{v}</div></div>)}</div><div className="drawer-section-title">技能标签</div><div>{['Python','PyTorch','RAG','LangChain','Milvus','大模型微调','机器学习','向量检索'].map((x) => <Tag color="geekblue" key={x}>{x}</Tag>)}</div><div className="drawer-section-title">工作经历</div><div className="portrait-summary"><b>智远科技 · 高级算法工程师</b><div className="muted" style={{ margin: '5px 0' }}>2022.06 — 至今</div>负责企业级大模型应用架构设计与研发，主导 RAG 知识库及智能体平台落地，检索准确率提升至 96%。</div><div className="portrait-summary mt-16"><b>云图数据 · 算法工程师</b><div className="muted" style={{ margin: '5px 0' }}>2018.07 — 2022.05</div>负责 NLP 算法研究及工程落地，构建文本分类、信息抽取与语义检索模型。</div><div className="drawer-section-title">AI 质量检查</div><div style={{ padding: 14, background: '#f0faf6', borderRadius: 8, color: '#34836d', fontSize: 12 }}><CheckCircleFilled/> 未发现关键字段缺失，2 段工作经历、3 段项目经历均已完成结构化。</div><Button type="primary" block style={{ marginTop: 22 }} icon={<ThunderboltOutlined/>} onClick={() => { message.success('人才档案已创建'); setSelected(null); }}>生成并绑定人才档案</Button></>}
    </Drawer>
  </>;
}
