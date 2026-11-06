import { useCallback, useEffect, useState } from 'react';
import { App, Button, Empty, Input, Tag } from 'antd';
import { BarChartOutlined, BookOutlined, FileSearchOutlined, HistoryOutlined, PlusOutlined, RobotOutlined, SendOutlined, SolutionOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { PageHeader } from '../components/Common';
import { agentsApi } from '../services/platform';
import { formatApiError, streamAgentChat } from '../services/api';
import type { AgentConversation } from '../types';

interface ChatMessage { role: 'user'|'assistant'; text: string }
const agents = [{ icon: <FileSearchOutlined/>, name: '人才档案解析', color: '#5273df' },{ icon: <SolutionOutlined/>, name: '岗位人才匹配', color: '#21a085' },{ icon: <BarChartOutlined/>, name: '人才数据决策', color: '#7c63cc' },{ icon: <BookOutlined/>, name: '智能培训赋能', color: '#d58a35' }];

export default function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]); const [input, setInput] = useState(''); const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>(); const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const { message } = App.useApp();
  const loadConversations = useCallback(async () => {
    try { const res = await agentsApi.conversations({ page: 1, page_size: 10 }); setConversations(res.items || []); }
    catch { setConversations([]); }
  }, []);
  useEffect(() => { void loadConversations(); }, [loadConversations]);

  const send = async (text = input) => {
    if (!text.trim() || loading) return; setMessages((old) => [...old, { role: 'user', text }]); setInput(''); setLoading(true);
    try {
      setMessages((old) => [...old, { role: 'assistant', text: '' }]);
      await streamAgentChat({ agent_type: 'assistant', conversation_id: conversationId, message: text, stream: true }, ({ event, data }) => {
        if (event === 'start' && data.conversation_id) setConversationId(String(data.conversation_id));
        if (event === 'delta' && data.text) setMessages((old) => old.map((m, i) => i === old.length - 1 ? { ...m, text: m.text + String(data.text) } : m));
        if (event === 'error') throw new Error(String(data.message || 'AI 调用失败'));
      });
      void loadConversations();
    } catch (e) { message.error(formatApiError(e)); } finally { setLoading(false); }
  };
  const openConversation = async (item: AgentConversation) => {
    try {
      const rows = await agentsApi.messages(item.conversation_id) as { role?: string; content?: string }[];
      setConversationId(String(item.conversation_id));
      setMessages(rows.map((row) => ({ role: row.role === 'user' ? 'user' : 'assistant', text: row.content || '' })));
    } catch (error) { message.error(formatApiError(error)); }
  };

  return <>
    <PageHeader title="AI 智能助手" description="一个入口，协同六大专属智能体与八大业务技能，让人才工作自然发生" actions={<Button icon={<PlusOutlined/>} onClick={() => { setMessages([]); setConversationId(undefined); }}>新建对话</Button>}/>
    <div className="assistant-layout" style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 18 }}>
      <aside><div className="card" style={{ padding: 15, marginBottom: 16 }}><div className="section-card-title mb-16">专属智能体</div>{agents.map((a) => <div key={a.name} className="flex items-center gap-12" style={{ padding: '10px 2px', cursor: 'pointer' }}><span className="activity-icon" style={{ background: `${a.color}16`, color: a.color }}>{a.icon}</span><span style={{ fontSize: 12 }}>{a.name}</span></div>)}</div><div className="card" style={{ padding: 15 }}><div className="flex justify-between items-center mb-16"><div className="section-card-title">最近对话</div><HistoryOutlined className="muted"/></div>{conversations.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话"/> : conversations.map((x, i) => <div key={String(x.conversation_id)} onClick={() => openConversation(x)} style={{ fontSize: 11, color: i === 0 ? '#315efb' : '#6f7b8e', padding: '10px 7px', background: i === 0 ? '#f1f5ff' : 'transparent', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{x.title || `会话 ${x.conversation_id}`}</div>)}</div></aside>
      <section className="card chat-panel" style={{ minHeight: 710 }}><div className="chat-header"><div className="ai-logo"><RobotOutlined/></div><div style={{ flex: 1 }}><div className="chat-title">TalentOS Copilot</div><div className="chat-subtitle">● 在线 · 多智能体协同</div></div><Tag color="purple">多智能体协同</Tag></div><div className="chat-body" style={{ maxHeight: 570 }}>{messages.length === 0 ? <div className="chat-welcome"><div className="ai-logo"><ThunderboltOutlined/></div><h3>今天想一起完成什么工作？</h3><p>我可以查人才、算匹配、找制度、分析数据，也可以为您自动执行复杂任务</p><div className="suggestion-grid">{['帮我分析最新人才画像','为当前岗位推荐候选人','分析一下技术团队的人才结构','查询高级工程师的晋升条件'].map((s) => <button className="suggestion-item" key={s} onClick={() => send(s)}>{s}</button>)}</div><div className="flex justify-between" style={{ maxWidth: 570, margin: '30px auto 0', padding: 15, background: '#f8f9fc', borderRadius: 9 }}>{['查人才','找岗位','算匹配','问制度','析数据'].map((x) => <Button type="text" size="small" key={x}>{x}</Button>)}</div></div> : <>{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="message-avatar" style={m.role === 'assistant' ? { background: '#eaf0ff', color: '#496ce0' } : { background: '#5273df', color: '#fff' }}>{m.role === 'assistant' ? <RobotOutlined/> : <UserOutlined/>}</div><div className="message-content"><div className="message-bubble">{m.text}</div></div></div>)}{loading && <div className="message"><div className="message-avatar" style={{ background: '#eaf0ff', color: '#496ce0' }}><RobotOutlined/></div><div className="message-bubble">正在调度智能体并查询相关业务数据…</div></div>}</>}</div><div className="chat-composer"><div className="composer-box"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="输入任务或问题，AI 将自动选择智能体与技能…"/><div className="composer-bottom"><span className="composer-hint">内容由 AI 生成，重要决策请结合业务实际</span><Button type="primary" size="small" icon={<SendOutlined/>} loading={loading} onClick={() => send()}>发送</Button></div></div></div></section>
    </div>
  </>;
}
