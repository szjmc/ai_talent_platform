import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Empty, Input, Select, Tag } from 'antd';
import { BarChartOutlined, BookOutlined, FileSearchOutlined, HistoryOutlined, PlusOutlined, RobotOutlined, SendOutlined, SolutionOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { PageHeader } from '../components/Common';
import { agentsApi, talentsApi } from '../services/platform';
import { formatApiError, streamAgentChat } from '../services/api';
import type { AgentConversation, Talent } from '../types';
import './AssistantPage.css';

interface ChatMessage { role: 'user'|'assistant'; text: string }
const agents = [{ icon: <FileSearchOutlined/>, name: '人才档案解析', color: '#5273df' },{ icon: <SolutionOutlined/>, name: '岗位人才匹配', color: '#21a085' },{ icon: <BarChartOutlined/>, name: '人才数据决策', color: '#7c63cc' },{ icon: <BookOutlined/>, name: '智能培训赋能', color: '#d58a35' }];

export default function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]); const [input, setInput] = useState(''); const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>(); const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [talents, setTalents] = useState<Talent[]>([]); const [talentId, setTalentId] = useState<number>(); const [loadError, setLoadError] = useState('');
  const { message } = App.useApp();
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined); const searchVersion = useRef(0);
  const searchTalents = (keyword: string) => {
    clearTimeout(searchTimer.current); const version = ++searchVersion.current;
    searchTimer.current = setTimeout(() => { talentsApi.list({ page: 1, page_size: 100, keyword: keyword.trim() || undefined }).then(res => { if (version === searchVersion.current) setTalents(res.items || []); }).catch(() => { if (version === searchVersion.current) message.error('人才列表查询失败，请检查权限'); }); }, 300);
  };
  const loadConversations = useCallback(async () => {
    try { const res = await agentsApi.conversations({ page: 1, page_size: 10 }); setConversations(res.items || []); setLoadError(''); }
    catch (error) { setConversations([]); setLoadError(formatApiError(error)); }
  }, []);
  useEffect(() => { void loadConversations(); talentsApi.list({ page: 1, page_size: 100 }).then(res => setTalents(res.items || [])).catch(() => setTalents([])); return () => clearTimeout(searchTimer.current); }, [loadConversations]);

  const send = async (text = input) => {
    if (!text.trim() || loading) return; setMessages((old) => [...old, { role: 'user', text }]); setInput(''); setLoading(true);
    try {
      setMessages((old) => [...old, { role: 'assistant', text: '' }]);
      await streamAgentChat({ agent_type: talentId ? 'talent' : 'assistant', conversation_id: conversationId, talent_id: talentId, message: text, stream: true }, ({ event, data }) => {
        if (event === 'start' && data.conversation_id) setConversationId(String(data.conversation_id));
        if (event === 'talent' && data.talent_id) setTalentId(Number(data.talent_id));
        if (event === 'delta' && data.text) setMessages((old) => old.map((m, i) => i === old.length - 1 ? { ...m, text: m.text + String(data.text) } : m));
        if (event === 'error') throw new Error(String(data.message || 'AI 调用失败'));
      });
      void loadConversations();
    } catch (e) { const error = formatApiError(e); setMessages(old => old.map((m, i) => i === old.length - 1 && m.role === 'assistant' ? { ...m, text: m.text ? `${m.text}\n\n回答中断：${error}` : `回答失败：${error}` } : m)); } finally { setLoading(false); }
  };
  const openConversation = async (item: AgentConversation) => {
    try {
      const response = await agentsApi.messages(item.id, { page: 1, page_size: 100 });
      const lastPage = Math.max(1, Math.ceil(response.total / 100));
      const rows = lastPage > 1 ? (await agentsApi.messages(item.id, { page: lastPage, page_size: 100 })).items : response.items;
      if (lastPage > 1) message.info('显示最近一页会话记录');
      setConversationId(String(item.id)); setTalentId(undefined);
      setMessages(rows.map((row) => ({ role: row.role === 'user' ? 'user' : 'assistant', text: row.content || '' })));
    } catch (error) { message.error(formatApiError(error)); }
  };

  return <div className="assistant-page">
    <PageHeader title="AI 智能助手" description="一个入口，协同六大专属智能体与八大业务技能，让人才工作自然发生" actions={<Button icon={<PlusOutlined/>} disabled={loading} onClick={() => { setMessages([]); setConversationId(undefined); setTalentId(undefined); }}>新建对话</Button>}/>
    {loadError && <Alert type="warning" showIcon message="会话列表加载失败" description={loadError} style={{ marginBottom: 12 }} />}
    <div className="assistant-layout" style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 18 }}>
      <aside><div className="card" style={{ padding: 15, marginBottom: 16 }}><div className="section-card-title mb-16">专属智能体</div>{agents.map((a) => <div key={a.name} className="flex items-center gap-12" style={{ padding: '10px 2px', cursor: 'pointer' }}><span className="activity-icon" style={{ background: `${a.color}16`, color: a.color }}>{a.icon}</span><span style={{ fontSize: 12 }}>{a.name}</span></div>)}</div><div className="card" style={{ padding: 15 }}><div className="flex justify-between items-center mb-16"><div className="section-card-title">最近对话</div><HistoryOutlined className="muted"/></div>{conversations.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话"/> : conversations.map((x) => <div key={String(x.id)} onClick={() => { if (!loading) void openConversation(x); }} style={{ fontSize: 11, color: String(x.id) === conversationId ? '#315efb' : '#6f7b8e', padding: '10px 7px', background: String(x.id) === conversationId ? '#f1f5ff' : 'transparent', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{x.title || `会话 ${x.id}`}</div>)}</div></aside>
      <section className="card chat-panel" style={{ minHeight: 520 }}><div className="chat-header"><div className="ai-logo"><RobotOutlined/></div><div style={{ flex: 1 }}><div className="chat-title">TalentOS Copilot</div><div className="chat-subtitle">多智能体协同</div></div><Select aria-label="选择人才" allowClear showSearch filterOption={false} onSearch={searchTalents} placeholder="搜索人才（画像追问）" value={talentId} onChange={setTalentId} disabled={loading} style={{ width: 180, maxWidth: '50%' }} options={talents.map(t => ({ value: t.id, label: `${t.name} (#${t.id})` }))}/><Tag color="purple">多智能体协同</Tag></div><div className="chat-body" style={{ maxHeight: 570 }}>{messages.length === 0 ? <div className="chat-welcome"><div className="ai-logo"><ThunderboltOutlined/></div><h3>今天想一起完成什么工作？</h3><p>我可以查人才、算匹配、找制度、分析数据，也可以为您自动执行复杂任务</p><div className="suggestion-grid">{['帮我分析最新人才画像','为当前岗位推荐候选人','分析一下技术团队的人才结构','查询高级工程师的晋升条件'].map((s) => <button className="suggestion-item" key={s} onClick={() => send(s)}>{s}</button>)}</div><div className="flex justify-between" style={{ maxWidth: 570, margin: '30px auto 0', padding: 15, background: '#f8f9fc', borderRadius: 9 }}>{['查人才','找岗位','算匹配','问制度','析数据'].map((x) => <Button type="text" size="small" key={x} onClick={() => setInput(x)}>{x}</Button>)}</div></div> : <>{messages.map((m, i) => <div className={`message ${m.role}`} key={i}><div className="message-avatar" style={m.role === 'assistant' ? { background: '#eaf0ff', color: '#496ce0' } : { background: '#5273df', color: '#fff' }}>{m.role === 'assistant' ? <RobotOutlined/> : <UserOutlined/>}</div><div className="message-content"><div className="message-bubble">{m.text}</div></div></div>)}{loading && <div className="message"><div className="message-avatar" style={{ background: '#eaf0ff', color: '#496ce0' }}><RobotOutlined/></div><div className="message-bubble">正在调度智能体并查询相关业务数据…</div></div>}</>}</div><div className="chat-composer"><div className="composer-box"><textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="输入任务或问题，AI 将自动选择智能体与技能…"/><div className="composer-bottom"><span className="composer-hint">内容由 AI 生成，重要决策请结合业务实际</span><Button type="primary" size="small" icon={<SendOutlined/>} loading={loading} onClick={() => send()}>发送</Button></div></div></div></section>
    </div>
  </div>;
}
