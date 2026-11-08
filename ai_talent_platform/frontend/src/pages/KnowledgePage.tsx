import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Empty, Input, Modal, Pagination, Select, Tag, Upload } from 'antd';
import { BookOutlined, CheckCircleFilled, CloudUploadOutlined, DatabaseOutlined, FilePdfOutlined, LinkOutlined, PlusOutlined, RobotOutlined, SendOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { knowledgeApi } from '../services/platform';
import { formatApiError } from '../services/api';
import type { KnowledgeDoc } from '../types';

interface Message { role: 'user'|'assistant'; text: string; citations?: { title: string; page: number; chunk: string }[] }
const suggestions = ['晋级到高级工程师需要满足哪些条件？','人才测评结果的有效期是多久？','如何申请专业技能培训？','关键岗位继任者的培养流程是什么？'];
const categories = [
  { value: 'policy', label: '制度政策' }, { value: 'resume', label: '简历' }, { value: 'job_description', label: '岗位说明书' },
  { value: 'assessment', label: '测评' }, { value: 'training', label: '培训' }, { value: 'case', label: '案例' }, { value: 'development_plan', label: '发展计划' },
];

export default function KnowledgePage() {
  const [messages, setMessages] = useState<Message[]>([]); const [input, setInput] = useState(''); const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null); const [docs, setDocs] = useState<KnowledgeDoc[]>([]); const [docTotal, setDocTotal] = useState(0);
  const [docPage, setDocPage] = useState(1); const [docKeyword, setDocKeyword] = useState('');
  const [docsError, setDocsError] = useState(''); const [uploadOpen, setUploadOpen] = useState(false); const [category, setCategory] = useState('policy');
  const [uploadFiles, setUploadFiles] = useState<File[]>([]); const [uploading, setUploading] = useState(false);
  const { message } = App.useApp(); const chatRef = useRef<HTMLDivElement>(null);

  const loadDocs = useCallback(async () => {
    try { const res = await knowledgeApi.docs({ page: docPage, page_size: 20, keyword: docKeyword || undefined }); setDocs((res.items || []).map((d) => ({ ...d, id: d.id ?? d.doc_id ?? 0 }))); setDocTotal(res.total || 0); setDocsError(''); }
    catch (error) { setDocsError(formatApiError(error)); setDocs([]); setDocTotal(0); }
  }, [docPage, docKeyword]);
  useEffect(() => { void loadDocs(); }, [loadDocs]);

  const ask = async (text = input) => {
    if (!text.trim() || loading) return; setMessages((old) => [...old, { role: 'user', text }]); setInput(''); setLoading(true);
    try {
      const res = await knowledgeApi.ask({ question: text, session_id: sessionId, top_k: 5 });
      setSessionId(res.session_id);
      setMessages((old) => [...old, { role: 'assistant', text: res.answer, citations: (res.sources || []).map((c) => ({ title: c.source || `文档 #${c.doc_id ?? ''}`, page: c.page ?? 0, chunk: c.chunk_id != null ? `切片 #${c.chunk_id}` : '' })) }]);
      setTimeout(() => chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' }), 100);
    } catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); }
  };
  const upload = async () => {
    if (!uploadFiles.length) { message.warning('请选择要上传的文档'); return; }
    setUploading(true);
    try { await knowledgeApi.upload(uploadFiles, category); message.success('文档已上传，请按后端返回状态检查索引结果'); setUploadOpen(false); setUploadFiles([]); await loadDocs(); }
    catch (error) { message.error(formatApiError(error)); } finally { setUploading(false); }
  };
  const chunkTotal = docs.reduce((acc, d) => acc + (d.chunk_count || 0), 0);

  return <>
    <PageHeader title="企业知识库" description="私域知识精准检索，每个回答都有原文依据，让企业知识真正可问、可查、可信" actions={<><Button icon={<DatabaseOutlined/>} onClick={() => void loadDocs()}>检索质量自检</Button><Button type="primary" icon={<CloudUploadOutlined/>} onClick={() => setUploadOpen(true)}>上传文档</Button></>}/>
    <div className="knowledge-layout">
      <section className="card chat-panel"><div className="chat-header"><div className="ai-logo"><BookOutlined/></div><div style={{ flex: 1 }}><div className="chat-title">企业知识问答</div><div className="chat-subtitle">● RAG 引擎 · 回答严格基于企业知识</div></div><Tag color="blue">混合检索</Tag><Button type="text" size="small" onClick={() => { setMessages([]); setSessionId(null); }}>新对话</Button></div>
        <div className="chat-body" ref={chatRef}>{messages.length === 0 ? <div className="chat-welcome"><div className="ai-logo"><RobotOutlined/></div><h3>您好，我是企业知识助手</h3><p>我可以帮您查询人才制度、岗位标准、培养方案等内容<br/>所有回答均附原文出处，找不到依据时会明确告知</p><div className="suggestion-grid">{suggestions.map((s) => <button className="suggestion-item" key={s} onClick={() => ask(s)}><ThunderboltOutlined style={{ color: '#6e82d8', marginRight: 7 }}/>{s}</button>)}</div></div> : <>{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="message-avatar" style={m.role === 'assistant' ? { background: '#eaf0ff', color: '#496ce0' } : { background: '#5273df', color: '#fff' }}>{m.role === 'assistant' ? <RobotOutlined/> : <UserOutlined/>}</div><div className="message-content"><div className="message-bubble">{m.text}</div>{m.citations && m.citations.length > 0 && <div className="citation-box"><div className="citation-title"><LinkOutlined/> 引用来源（{m.citations.length}）</div>{m.citations.map((c, index) => <div className="citation-item" key={index}><FilePdfOutlined style={{ color: '#df6464' }}/><span><b>{c.title}</b><br/><span className="muted">{c.chunk}</span></span><span className="citation-page">{c.page ? `第 ${c.page} 页` : '—'}</span></div>)}</div>}</div></div>)}{loading && <div className="message"><div className="message-avatar" style={{ background: '#eaf0ff', color: '#496ce0' }}><RobotOutlined/></div><div className="message-bubble">正在检索企业知识库并核对原文依据…</div></div>}</>}</div>
        <div className="chat-composer"><div className="composer-box"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder="输入您想咨询的企业制度或业务问题…"/><div className="composer-bottom"><span className="composer-hint">Enter 发送 · Shift + Enter 换行</span><Button type="primary" size="small" icon={<SendOutlined/>} loading={loading} onClick={() => ask()}>发送</Button></div></div><div style={{ textAlign: 'center', fontSize: 9, color: '#a0a8b7', marginTop: 9 }}><CheckCircleFilled className="text-success"/> 权限前置过滤 · 私域安全检索 · 无依据不回答</div></div>
      </section>
      <aside className="card doc-panel"><div className="chat-header"><div style={{ flex: 1 }}><div className="chat-title">知识文档</div><div className="section-card-subtitle">共 {docTotal} 份文档 · 当前页 {chunkTotal} 个知识片段</div></div><Button type="text" icon={<PlusOutlined/>} onClick={() => setUploadOpen(true)}/></div><div style={{ padding: 13, borderBottom: '1px solid #edf0f4' }}><Input.Search placeholder="搜索知识文档" size="small" onSearch={(value) => { setDocPage(1); setDocKeyword(value); }}/></div>
      {docsError && <Alert type="warning" showIcon message={docsError} style={{ margin: 12 }}/>}
      {docs.length === 0 ? <Empty description="暂无知识文档" style={{ padding: 30 }}/> : docs.map((d) => <div className="doc-list-item" key={d.id}><div className="doc-icon"><FilePdfOutlined/></div><div style={{ flex: 1, minWidth: 0 }}><div className="doc-title">{d.title}</div><div className="doc-meta">{d.category} · {d.chunk_count} 片段</div><div className="flex justify-between items-center" style={{ marginTop: 7 }}><StatusBadge status={d.status}/>{d.status === 'failed' && <Button type="link" size="small" onClick={async () => { try { await knowledgeApi.retry(d.id); message.success('已触发重试'); await loadDocs(); } catch (error) { message.error(formatApiError(error)); } }}>重试</Button>}</div></div></div>)}
      {docTotal > 20 && <Pagination size="small" current={docPage} pageSize={20} total={docTotal} showSizeChanger={false} onChange={setDocPage} style={{ padding: 12 }} />}
      </aside>
    </div>
    <Modal title="上传知识文档" open={uploadOpen} onCancel={() => setUploadOpen(false)} onOk={upload} confirmLoading={uploading} okText="上传"><Select style={{ width: '100%', marginBottom: 12 }} value={category} onChange={setCategory} options={categories}/><Upload.Dragger multiple beforeUpload={(file) => { setUploadFiles((old) => [...old, file]); return false; }} onRemove={(file) => setUploadFiles((old) => old.filter((f) => f.name !== file.name))} fileList={uploadFiles.map((f, i) => ({ uid: String(i), name: f.name }))}><p className="ant-upload-drag-icon"><CloudUploadOutlined/></p><p className="ant-upload-text">点击或拖拽文档到此区域</p></Upload.Dragger></Modal>
  </>;
}
