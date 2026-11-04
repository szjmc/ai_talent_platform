import { useState } from 'react';
import { App, Button, Input, Tag } from 'antd';
import { ApartmentOutlined, BarChartOutlined, BookOutlined, CheckCircleFilled, FileSearchOutlined, HistoryOutlined, NodeIndexOutlined, PlusOutlined, RobotOutlined, SendOutlined, SolutionOutlined, TeamOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { PageHeader, PersonAvatar } from '../components/Common';
import { useAuth } from '../store/AuthContext';
import { formatApiError, streamAgentChat } from '../services/api';
import { sleep } from '../utils';

interface ChatMessage { role: 'user'|'assistant'; text: string; extra?: boolean }
const agents = [{ icon: <FileSearchOutlined/>, name: '人才档案解析', color: '#5273df' },{ icon: <SolutionOutlined/>, name: '岗位人才匹配', color: '#21a085' },{ icon: <BarChartOutlined/>, name: '人才数据决策', color: '#7c63cc' },{ icon: <BookOutlined/>, name: '智能培训赋能', color: '#d58a35' }];
export default function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]); const [input, setInput] = useState(''); const [loading, setLoading] = useState(false); const [conversationId, setConversationId] = useState<string>();
  const { demo } = useAuth(); const { message } = App.useApp();
  const send = async (text = input) => { if (!text.trim() || loading) return; setMessages((old) => [...old, { role: 'user', text }]); setInput(''); setLoading(true); try {
    if (!demo) {
      setMessages((old) => [...old, { role: 'assistant', text: '' }]);
      await streamAgentChat({ agent_type: 'assistant', conversation_id: conversationId, message: text, stream: true }, ({ event, data }) => {
        if (event === 'start' && data.conversation_id) setConversationId(String(data.conversation_id));
        if (event === 'delta' && data.text) setMessages((old) => old.map((m, i) => i === old.length - 1 ? { ...m, text: m.text + String(data.text) } : m));
        if (event === 'error') throw new Error(String(data.message || 'AI 调用失败'));
      });
    }
    else { await sleep(1200); setMessages((old) => [...old, { role: 'assistant', text: '已完成对张明远人才档案、近三次测评和当前岗位能力模型的综合分析。\n\n张明远当前综合评分 96 分，属于 A 级高潜人才。核心优势集中在 AI 算法工程、RAG 应用落地和复杂项目交付；近三次测评平均 92.5 分，学习能力与专业知识显著高于同层级人才。\n\n建议将其纳入「AI 算法专家」关键岗位继任者梯队，下一阶段重点补充团队管理与跨部门业务协同经验。', extra: true }]); }
  } catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); } };
  return <>
    <PageHeader title="AI 智能助手" description="一个入口，协同六大专属智能体与八大业务技能，让人才工作自然发生" demo={demo} actions={<Button icon={<PlusOutlined/>} onClick={() => { setMessages([]); setConversationId(undefined); }}>新建对话</Button>}/>
    <div className="assistant-layout" style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 18 }}>
      <aside><div className="card" style={{ padding: 15, marginBottom: 16 }}><div className="section-card-title mb-16">专属智能体</div>{agents.map((a) => <div key={a.name} className="flex items-center gap-12" style={{ padding: '10px 2px', cursor: 'pointer' }}><span className="activity-icon" style={{ background: `${a.color}16`, color: a.color }}>{a.icon}</span><span style={{ fontSize: 12 }}>{a.name}</span></div>)}</div><div className="card" style={{ padding: 15 }}><div className="flex justify-between items-center mb-16"><div className="section-card-title">最近对话</div><HistoryOutlined className="muted"/></div>{['张明远人才画像分析','技术团队人才结构盘点','高级后端岗位人才推荐','10 月培训完成率分析'].map((x, i) => <div key={x} style={{ fontSize: 11, color: i === 0 ? '#315efb' : '#6f7b8e', padding: '10px 7px', background: i === 0 ? '#f1f5ff' : 'transparent', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{x}</div>)}</div></aside>
      <section className="card chat-panel" style={{ minHeight: 710 }}><div className="chat-header"><div className="ai-logo"><RobotOutlined/></div><div style={{ flex: 1 }}><div className="chat-title">TalentOS Copilot</div><div className="chat-subtitle">● 在线 · Qwen Plus · 已连接 8 项业务技能</div></div><Tag color="purple">多智能体协同</Tag></div><div className="chat-body" style={{ maxHeight: 570 }}>{messages.length === 0 ? <div className="chat-welcome"><div className="ai-logo"><ThunderboltOutlined/></div><h3>今天想一起完成什么工作？</h3><p>我可以查人才、算匹配、找制度、分析数据，也可以为您自动执行复杂任务</p><div className="suggestion-grid">{['帮我分析张明远的人才画像','为 AI 算法专家岗位推荐候选人','分析一下技术团队的人才结构','查询高级工程师的晋升条件'].map((s) => <button className="suggestion-item" key={s} onClick={() => send(s)}>{s}</button>)}</div><div className="flex justify-between" style={{ maxWidth: 570, margin: '30px auto 0', padding: 15, background: '#f8f9fc', borderRadius: 9 }}>{['查人才','找岗位','算匹配','问制度','析数据'].map((x) => <Button type="text" size="small" key={x}>{x}</Button>)}</div></div> : <>{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="message-avatar" style={m.role === 'assistant' ? { background: '#eaf0ff', color: '#496ce0' } : { background: '#5273df', color: '#fff' }}>{m.role === 'assistant' ? <RobotOutlined/> : <UserOutlined/>}</div><div className="message-content"><div className="message-bubble">{m.text}</div>{m.extra && <><div className="card" style={{ padding: 16, marginTop: 10 }}><div className="talent-cell"><PersonAvatar name="张明远"/><div><div className="talent-cell-name">张明远 <Tag color="green">A 级高潜</Tag></div><div className="talent-cell-role">高级算法工程师 · 综合评分 96</div></div><Button type="link" size="small" style={{ marginLeft: 'auto' }}>查看完整画像</Button></div></div><div style={{ fontSize: 10, color: '#8d97a8', marginTop: 8 }}><CheckCircleFilled className="text-success"/> 已调用：人才档案查询、测评结果分析、岗位匹配 3 项技能 · 1.8s</div></>}</div></div>)}{loading && <div className="message"><div className="message-avatar" style={{ background: '#eaf0ff', color: '#496ce0' }}><RobotOutlined/></div><div className="message-bubble">正在调度智能体并查询相关业务数据…</div></div>}</>}</div><div className="chat-composer"><div className="composer-box"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="输入任务或问题，AI 将自动选择智能体与技能…"/><div className="composer-bottom"><span className="composer-hint">内容由 AI 生成，重要决策请结合业务实际</span><Button type="primary" size="small" icon={<SendOutlined/>} loading={loading} onClick={() => send()}>发送</Button></div></div></div></section>
    </div>
  </>;
}
