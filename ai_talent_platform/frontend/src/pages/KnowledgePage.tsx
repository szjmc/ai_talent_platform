import { useRef, useState } from 'react';
import { App, Button, Input, Progress, Select, Tag, Upload } from 'antd';
import { BookOutlined, CheckCircleFilled, CloudUploadOutlined, DatabaseOutlined, FilePdfOutlined, LinkOutlined, PlusOutlined, RobotOutlined, SendOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { PageHeader, StatusBadge } from '../components/Common';
import { demoKnowledge } from '../services/mockData';
import { useAuth } from '../store/AuthContext';
import { formatApiError, request } from '../services/api';
import { sleep } from '../utils';

interface Message { role: 'user'|'assistant'; text: string; citations?: { title: string; page: number; chunk: string }[] }
const suggestions = ['晋级到高级工程师需要满足哪些条件？','人才测评结果的有效期是多久？','如何申请专业技能培训？','关键岗位继任者的培养流程是什么？'];
export default function KnowledgePage() {
  const [messages, setMessages] = useState<Message[]>([]); const [input, setInput] = useState(''); const [loading, setLoading] = useState(false);
  const { demo } = useAuth(); const { message } = App.useApp(); const chatRef = useRef<HTMLDivElement>(null);
  const ask = async (text = input) => {
    if (!text.trim() || loading) return; setMessages((old) => [...old, { role: 'user', text }]); setInput(''); setLoading(true);
    try {
      if (!demo) { const res = await request<{ answer: string; citations: { doc_title: string; page: number; chunk: string }[] }>({ method: 'POST', url: '/knowledge/ask', data: { question: text, conversation_id: null } }); setMessages((old) => [...old, { role: 'assistant', text: res.answer, citations: res.citations.map((c) => ({ title: c.doc_title, page: c.page, chunk: c.chunk })) }]); }
      else { await sleep(1100); setMessages((old) => [...old, { role: 'assistant', text: '根据《职级晋升管理办法（2026 版）》第三章相关规定，晋级到高级工程师需同时满足以下条件：\n\n1. 任职要求：在中级工程师岗位任职满 2 年，且近两个年度绩效均为 B 及以上；\n2. 专业能力：具备独立负责复杂项目或核心模块的能力，通过高级专业技术测评（80 分及以上）；\n3. 业绩贡献：近 2 年至少主导完成 1 个公司级重点项目，或取得可量化的显著业务成果；\n4. 知识传承：完成不少于 16 学时的内部分享或人才带教任务。\n\n满足基本条件后，可由部门负责人提名，经专业技术委员会评审及人力资源部复核后正式聘任。', citations: [{ title: '职级晋升管理办法（2026 版）', page: 8, chunk: '第三章 第十二条：高级工程师任职资格与评审条件…' },{ title: '人才测评管理制度', page: 4, chunk: '高级专业技术测评结果有效期为 12 个月…' }] }]); }
      setTimeout(() => chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' }), 100);
    } catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); }
  };
  return <>
    <PageHeader title="企业知识库" description="私域知识精准检索，每个回答都有原文依据，让企业知识真正可问、可查、可信" demo={demo} actions={<><Button icon={<DatabaseOutlined/>}>检索质量自检</Button><Upload showUploadList={false} accept=".pdf,.doc,.docx,.txt,.md,.xlsx"><Button type="primary" icon={<CloudUploadOutlined/>} onClick={() => message.success('文档已上传，正在智能切片与向量化')}>上传文档</Button></Upload></>}/>
    <div className="knowledge-layout">
      <section className="card chat-panel"><div className="chat-header"><div className="ai-logo"><BookOutlined/></div><div style={{ flex: 1 }}><div className="chat-title">企业知识问答</div><div className="chat-subtitle">● RAG 引擎已就绪 · 回答严格基于企业知识</div></div><Tag color="blue">混合检索</Tag><Button type="text" size="small" onClick={() => setMessages([])}>新对话</Button></div>
        <div className="chat-body" ref={chatRef}>{messages.length === 0 ? <div className="chat-welcome"><div className="ai-logo"><RobotOutlined/></div><h3>您好，我是企业知识助手</h3><p>我可以帮您查询人才制度、岗位标准、培养方案等内容<br/>所有回答均附原文出处，找不到依据时会明确告知</p><div className="suggestion-grid">{suggestions.map((s) => <button className="suggestion-item" key={s} onClick={() => ask(s)}><ThunderboltOutlined style={{ color: '#6e82d8', marginRight: 7 }}/>{s}</button>)}</div></div> : <>{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="message-avatar" style={m.role === 'assistant' ? { background: '#eaf0ff', color: '#496ce0' } : { background: '#5273df', color: '#fff' }}>{m.role === 'assistant' ? <RobotOutlined/> : <UserOutlined/>}</div><div className="message-content"><div className="message-bubble">{m.text}</div>{m.citations && <div className="citation-box"><div className="citation-title"><LinkOutlined/> 引用来源（{m.citations.length}）</div>{m.citations.map((c) => <div className="citation-item" key={c.title}><FilePdfOutlined style={{ color: '#df6464' }}/><span><b>{c.title}</b><br/><span className="muted">{c.chunk}</span></span><span className="citation-page">第 {c.page} 页</span></div>)}</div>}</div></div>)}{loading && <div className="message"><div className="message-avatar" style={{ background: '#eaf0ff', color: '#496ce0' }}><RobotOutlined/></div><div className="message-bubble">正在检索企业知识库并核对原文依据…</div></div>}</>}</div>
        <div className="chat-composer"><div className="composer-box"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder="输入您想咨询的企业制度或业务问题…"/><div className="composer-bottom"><span className="composer-hint">Enter 发送 · Shift + Enter 换行</span><Button type="primary" size="small" icon={<SendOutlined/>} loading={loading} onClick={() => ask()}>发送</Button></div></div><div style={{ textAlign: 'center', fontSize: 9, color: '#a0a8b7', marginTop: 9 }}><CheckCircleFilled className="text-success"/> 权限前置过滤 · 私域安全检索 · 无依据不回答</div></div>
      </section>
      <aside className="card doc-panel"><div className="chat-header"><div style={{ flex: 1 }}><div className="chat-title">知识文档</div><div className="section-card-subtitle">共 286 份文档 · 18,420 个知识片段</div></div><Button type="text" icon={<PlusOutlined/>}/></div><div style={{ padding: 13, borderBottom: '1px solid #edf0f4' }}><Input.Search placeholder="搜索知识文档" size="small"/></div>{demoKnowledge.map((d) => <div className="doc-list-item" key={d.id}><div className="doc-icon"><FilePdfOutlined/></div><div style={{ flex: 1, minWidth: 0 }}><div className="doc-title">{d.title}</div><div className="doc-meta">{d.category} · {d.file_size} · {d.chunk_count} 片段</div><div className="flex justify-between items-center" style={{ marginTop: 7 }}><StatusBadge status={d.status}/><span className="muted" style={{ fontSize: 9 }}>{{ public: '全员可见', role: '角色可见', dept: '部门可见' }[d.permission_scope]}</span></div></div></div>)}<div style={{ padding: 17 }}><div className="flex justify-between" style={{ fontSize: 11, marginBottom: 7 }}><span>向量索引存储</span><span>68%</span></div><Progress percent={68} showInfo={false} size="small" strokeColor="#5b75dc"/><div className="muted" style={{ fontSize: 9, marginTop: 6 }}>6.8 GB / 10 GB · Milvus 私有集群</div></div></aside>
    </div>
  </>;
}
