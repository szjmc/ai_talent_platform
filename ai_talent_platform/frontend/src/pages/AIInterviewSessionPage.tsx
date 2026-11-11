import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Alert, App, Button, ConfigProvider, Divider, Input, Modal, Progress, Spin, Switch, Tooltip,
} from 'antd';
import {
  AudioOutlined, CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled,
  LockOutlined, MutedOutlined, ReloadOutlined, SafetyCertificateOutlined, SendOutlined,
  SoundOutlined, TeamOutlined, VideoCameraOutlined,
} from '@ant-design/icons';
import DigitalHuman, { type DigitalHumanState } from '../components/ai/DigitalHuman';
import { useSpeech } from '../hooks/useSpeech';
import { aiInterviewsApi } from '../services/platform';
import { formatApiError, proctoringApi } from '../services/api';
import { useProctoring } from '../hooks/useProctoring';
import type { AiInterviewPublicState } from '../types';
import './ai-interview-session.css';

type Phase = 'loading' | 'landing' | 'interview' | 'completed' | 'blocked';

const DIFFICULTY_LABEL: Record<string, string> = { junior: '初级', middle: '中级', senior: '高级' };

function formatClock(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

function closingText(language: string, name: string) {
  return language === 'en-US'
    ? `Thank you, ${name}. Your interview is complete and your responses have been submitted.`
    : `${name}，本次面试到这里就结束了，感谢你的时间，回答已成功提交。`;
}

export default function AIInterviewSessionPage() {
  const { token = '' } = useParams<{ token: string }>();
  const { message, modal } = App.useApp();
  const [phase, setPhase] = useState<Phase>('loading');
  const [state, setState] = useState<AiInterviewPublicState | null>(null);
  const [result, setResult] = useState<AiInterviewPublicState['result']>(null);
  const [error, setError] = useState('');
  const [answer, setAnswer] = useState('');
  const [mode, setMode] = useState<'voice' | 'text'>('text');
  const [voiceUsed, setVoiceUsed] = useState(false);
  const [voiceOn, setVoiceOn] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [testingMic, setTestingMic] = useState(false);
  /** ComfyUI 生成的数字人视频 URL；有值时 DigitalHuman 优先播放真人视频。 */
  const [videoSrc, setVideoSrc] = useState<string | undefined>(undefined);
  const videoCacheRef = useRef<Map<string, string>>(new Map());
  /** [防作弊] 监考提示与被阻断原因 */
  const [integrityNotice, setIntegrityNotice] = useState('');
  const [integrityBlocked, setIntegrityBlocked] = useState('');

  /* ------------------------------------------------------------ 行为监考（防作弊）
   * 切屏 / 失焦 / 退出全屏 / 多显示器 / 远程控制 / 虚拟摄像头 / 粘贴长答案
   * 全部交给服务端判定（见 app/ai/proctoring/integrity.py）。风险分不参与 AI 评分，
   * 只作为 HR 复核依据；严格模式命中硬性风险（远程/投屏/代考）会立刻中断面试。
   */
  const proctoring = useProctoring({
    subject: 'ai_interview',
    token,
    transport: proctoringApi,
    active: phase === 'interview',
    policy: {
      enabled: state?.proctoring?.enabled ?? true,
      strict: state?.proctoring?.strict ?? true,
      requireScreenShare: state?.proctoring?.require_screen_share ?? false,
      allowMultiDisplay: state?.proctoring?.allow_multi_display ?? false,
      heartbeatSeconds: state?.proctoring?.heartbeat_seconds ?? 15,
      snapshotIntervalSeconds: state?.proctoring?.snapshot_interval_seconds ?? 20,
    },
    onLevelChange: (ack) => {
      setIntegrityNotice(ack.state.warning || '');
      if (ack.new_violations.length) {
        const last = ack.new_violations[ack.new_violations.length - 1];
        message.warning(`监考提示：${last.label || last.detail || '检测到异常行为'}`);
      }
    },
    onBlocked: (reason) => {
      setIntegrityBlocked(reason);
      speech.stopSpeaking();
      speech.stopListening();
    },
  });

  const speech = useSpeech(state?.language || 'zh-CN', state?.interviewer?.voice_hint);
  const chatRef = useRef<HTMLDivElement | null>(null);
  const chatInnerRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);
  const questionStartedRef = useRef<number>(Date.now());
  const autoSubmittedRef = useRef(false);
  const submitRef = useRef<(auto?: boolean) => Promise<void>>(async () => {});
  const scrollChatToBottom = useCallback(() => {
    const node = chatRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, []);

  const language = state?.language || 'zh-CN';
  const interviewer = state?.interviewer;
  const progress = state?.progress;
  const currentQuestion = state?.current_question;
  const totalScore = result?.total_score ?? null;

  /* ------------------------------------------------------------ 初始化 */
  useEffect(() => {
    if (!token) { setPhase('blocked'); setError('面试链接不完整，请向 HR 索取完整链接'); return; }
    let alive = true;
    aiInterviewsApi.state(token).then((data) => {
      if (!alive) return;
      setState(data);
      if (data.status === 'in_progress') {
        setPhase('interview'); setRemaining(data.progress.remaining_seconds ?? null);
        window.setTimeout(scrollChatToBottom, 120);
        window.setTimeout(scrollChatToBottom, 420);
      }
      else if (data.status === 'completed') { setResult(data.result); setPhase('completed'); }
      else if (data.status === 'cancelled' || data.status === 'expired') { setPhase('blocked'); setError(data.status === 'expired' ? '面试链接已过期，请联系 HR 重新发送' : '本次面试已取消，如有疑问请联系 HR'); }
      else { setPhase('landing'); }
    }).catch((err) => {
      if (!alive) return;
      setError(formatApiError(err)); setPhase('blocked');
    });
    return () => { alive = false; };
  }, [token, scrollChatToBottom]);

  /* 字体/排版晚于首屏就绪时（如慢网络或 Web 字体），重新贴到底部，避免当前问题被输入区遮挡。 */
  useEffect(() => {
    if (phase !== 'interview') return;
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts;
    if (!fonts) return;
    const pin = () => { if (pinnedRef.current) scrollChatToBottom(); };
    fonts.ready.then(pin).catch(() => undefined);
    fonts.addEventListener('loadingdone', pin);
    return () => fonts.removeEventListener('loadingdone', pin);
  }, [phase, scrollChatToBottom]);

  /* ------------------------------------------------------------ 倒计时 */
  useEffect(() => {
    if (phase === 'interview' && state?.progress.remaining_seconds != null) setRemaining(state.progress.remaining_seconds);
  }, [phase, state?.progress.remaining_seconds]);

  useEffect(() => {
    if (phase !== 'interview' || remaining == null) return;
    const timer = window.setInterval(() => setRemaining((value) => (value == null ? value : Math.max(0, value - 1))), 1000);
    return () => window.clearInterval(timer);
  }, [phase, remaining == null]);

  /* ------------------------------------------------------------ 每题计时 */
  const questionKey = `${currentQuestion?.index ?? -1}:${currentQuestion?.kind ?? ''}`;
  useEffect(() => { questionStartedRef.current = Date.now(); }, [questionKey]);

  /* ------------------------------------------------------------ 消息流 */
  const messages = useMemo(() => {
    if (!state) return [] as { seq: number; role: string; kind: string; content: string }[];
    const list = [
      { seq: -1, role: 'interviewer', kind: 'greeting', content: state.greeting },
      ...state.transcript.map((item) => ({ seq: item.seq, role: item.role, kind: item.kind, content: item.content })),
    ];
    const last = list[list.length - 1];
    if (currentQuestion && last && last.role === 'interviewer' && last.content === currentQuestion.text) list.pop();
    return list;
  }, [state, currentQuestion]);

  useEffect(() => {
    if (phase !== 'interview') return;
    const node = chatRef.current;
    if (!node) return;
    // 直接定位到底部：候选人作答后必须立刻看到追问/下一题，不能依赖平滑滚动。
    // 再补一次延时定位，避免字体/换行在下一帧才完成导致 scrollHeight 偏小。
    pinnedRef.current = true;
    const toBottom = () => { node.scrollTop = node.scrollHeight; };
    const frame = window.requestAnimationFrame(toBottom);
    const timer = window.setTimeout(toBottom, 220);
    return () => { window.cancelAnimationFrame(frame); window.clearTimeout(timer); };
  }, [phase, messages.length, currentQuestion?.text]);

  useEffect(() => {
    if (phase !== 'interview') return;
    const node = chatRef.current;
    const inner = chatInnerRef.current;
    if (!node || !inner || typeof ResizeObserver === 'undefined') return;
    // 字体加载 / 换行变化会改变聊天内容高度；保持贴底，避免当前问题被输入框挡住。
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) node.scrollTop = node.scrollHeight;
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, [phase]);

  /* 离场提示统一由 useProctoring 注册（它同时会把「尝试关闭/刷新」作为监考事件上报，
   * 这里不再重复 addEventListener，否则会弹两次确认框。 */

  useEffect(() => { if (!speech.ttsSupported) setVoiceOn(false); }, [speech.ttsSupported]);

  /* ------------------------------------------------------------ 数字人视频生成（ComfyUI） */
  const generateVideoForText = useCallback(async (text: string): Promise<string | undefined> => {
    if (!text) return undefined;
    if (videoCacheRef.current.has(text)) return videoCacheRef.current.get(text);
    try {
      const data = await aiInterviewsApi.generateDigitalHumanVideo({
        text,
        persona_key: state?.interviewer?.key || 'default',
        voice: state?.interviewer?.voice_hint || undefined,
        language: state?.language || 'zh-CN',
      });
      if (data.ok && data.video_url) {
        videoCacheRef.current.set(text, data.video_url);
        return data.video_url;
      }
    } catch {
      // 数字人视频生成失败不阻断面试，静默降级到照片/头像模式
    }
    return undefined;
  }, [aiInterviewsApi, state?.interviewer?.key, state?.interviewer?.voice_hint, state?.language]);

  /** 播报文本并同步生成数字人视频；视频生成完成后切换为真人视频播放，失败则保持照片/头像模式。 */
  const speakWithVideo = useCallback((text: string) => {
    speech.speak(text);
    void generateVideoForText(text).then((url) => {
      if (url) setVideoSrc(url);
    });
  }, [speech, generateVideoForText]);

  /* ------------------------------------------------------------ 开始 */
  const start = async () => {
    setStarting(true); setError('');
    try {
      const data = await aiInterviewsApi.start(token);
      setState(data);
      setRemaining(data.progress.remaining_seconds ?? null);
      if (data.status === 'completed') { setResult(data.result); setPhase('completed'); return; }
      setPhase('interview');
      // [防作弊] 与笔试一致：开始即全屏；浏览器拒绝时不阻塞（服务端仍会记录 fullscreen_exit）
      void document.documentElement.requestFullscreen?.().catch(() => undefined);
      window.setTimeout(scrollChatToBottom, 120);
      window.setTimeout(scrollChatToBottom, 420);
      if (voiceOn) speakWithVideo(`${data.greeting} ${data.current_question?.text || ''}`.trim());
    } catch (err) { setError(formatApiError(err)); }
    finally { setStarting(false); }
  };

  /* ------------------------------------------------------------ 提交回答 */
  const submit = useCallback(async (auto = false) => {
    const text = answer.trim();
    if (!text && !auto) { message.warning('请先输入或说出你的回答'); return; }
    if (submitting) return;
    setSubmitting(true); setError('');
    speech.stopListening(); speech.stopSpeaking();
    if (!text) {
      // 倒计时结束且未作答：直接结束面试，由服务端按未答题目生成「样本不足」报告。
      try {
        const data = await aiInterviewsApi.finish(token);
        setState(data); setResult(data.result); setPhase('completed'); setRemaining(null);
        if (voiceOn) speakWithVideo(closingText(language, data.candidate_name || ''));
      } catch (err) { setError(formatApiError(err)); }
      finally { setSubmitting(false); }
      return;
    }
    const duration = Math.max(0, Math.round((Date.now() - questionStartedRef.current) / 1000));
    try {
      const res = await aiInterviewsApi.answer(token, { answer: text, duration_seconds: duration, input_mode: voiceUsed ? 'voice' : 'text' });
      if (res.state) setState(res.state);
      setAnswer(''); setVoiceUsed(false);
      window.setTimeout(scrollChatToBottom, 60);
      window.setTimeout(scrollChatToBottom, 320);
      if (res.completed) {
        const finalResult = res.result || res.state?.result || null;
        setResult(finalResult); setPhase('completed'); setRemaining(null);
        if (res.timed_out) message.warning('作答时间已到，系统已自动提交');
        if (voiceOn) speakWithVideo(closingText(language, res.state?.candidate_name || state?.candidate_name || ''));
      } else {
        const next = res.next_question;
        if (voiceOn) speakWithVideo([res.interviewer_reply, next?.text].filter(Boolean).join(' '));
      }
    } catch (err) {
      setError(formatApiError(err));
      if (!auto) message.error(formatApiError(err));
    } finally { setSubmitting(false); }
  }, [answer, submitting, token, voiceUsed, speech, voiceOn, language, state?.candidate_name, message, scrollChatToBottom]);

  useEffect(() => { submitRef.current = submit; }, [submit]);

  useEffect(() => {
    if (phase === 'interview' && remaining === 0 && !autoSubmittedRef.current) {
      autoSubmittedRef.current = true;
      void submitRef.current(true);
    }
  }, [phase, remaining]);

  /* ------------------------------------------------------------ 重听 / 结束 */
  const repeat = async () => {
    try {
      const data = await aiInterviewsApi.repeat(token);
      if (data.text) { if (!voiceOn) setVoiceOn(true); speakWithVideo(data.text); }
      else message.info('暂时没有可重听的问题');
    } catch (err) { message.error(formatApiError(err)); }
  };

  const finish = () => {
    modal.confirm({
      title: '提前结束面试？',
      content: '结束后将无法继续作答。已提交的回答仍会生成评估报告，未作答的题目会标记为未测。',
      okText: '结束并提交', cancelText: '继续作答', okButtonProps: { danger: true },
      onOk: async () => {
        setFinishing(true);
        try {
          const data = await aiInterviewsApi.finish(token);
          setState(data); setResult(data.result); setPhase('completed'); setRemaining(null);
          speech.stopListening();
          speakWithVideo(closingText(language, data.candidate_name || ''));
        } catch (err) { message.error(formatApiError(err)); }
        finally { setFinishing(false); }
      },
    });
  };

  const testMic = async () => {
    if (!navigator.mediaDevices?.getUserMedia) { message.warning('当前环境无法调用麦克风，请改用文字作答'); return; }
    setTestingMic(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
      message.success('麦克风可用，开始后可以选择语音作答');
    } catch { message.error('无法访问麦克风，请在浏览器中允许权限，或使用文字作答'); }
    finally { setTestingMic(false); }
  };

  const toggleVoiceMode = () => {
    if (mode === 'voice') { speech.stopListening(); setMode('text'); return; }
    if (!speech.sttSupported) { message.warning('当前浏览器不支持语音识别，推荐使用 Chrome / Edge'); return; }
    setMode('voice');
  };

  const dhState: DigitalHumanState = phase === 'completed' ? 'done'
    : speech.speaking ? 'speaking'
      : speech.listening ? 'listening'
        : submitting || starting ? 'thinking' : phase === 'interview' ? 'waiting' : 'idle';

  const answered = progress?.answered ?? 0;
  const total = progress?.total || state?.question_count || 0;
  const currentIndex = (progress?.current_index ?? 0) + 1;

  /* ------------------------------------------------------------ 渲染 */
  if (phase === 'loading') {
    return <div className="aic-loading"><Spin size="large" /><p>正在准备你的 AI 面试…</p></div>;
  }

  if (phase === 'blocked') {
    return (
      <div className="aic-page aic-blocked-page">
        <div className="aic-blocked-card">
          <div className="aic-blocked-icon"><CloseCircleFilled /></div>
          <h2>无法进入面试</h2>
          <p>{error || '面试链接无效或已失效，请联系 HR 重新发送。'}</p>
          <div className="aic-blocked-actions">
            <Button icon={<ReloadOutlined />} onClick={() => window.location.reload()}>重新加载</Button>
            <Button type="primary" ghost onClick={() => setPrivacyOpen(true)}>查看隐私说明</Button>
          </div>
          <p className="aic-blocked-help"><LockOutlined /> 如多次失败，请把当前页面链接完整复制给 HR 核对。</p>
        </div>
        <PrivacyModal open={privacyOpen} onClose={() => setPrivacyOpen(false)} />
      </div>
    );
  }

  /* [防作弊] 严格模式命中硬性风险（远程控制 / 投屏 / 疑似代考）：立即中断，
   * 已答内容保留并由服务端照常评估，但不再接受新的回答（后端 answer 接口同样会拒绝）。
   */
  if (phase === 'interview' && integrityBlocked) {
    return (
      <div className="aic-page">
        <ConfigProvider theme={{ token: { colorPrimary: '#315efb', borderRadius: 12 } }}>
          <div className="aic-blocked-page">
            <div className="aic-blocked-card">
              <div className="aic-blocked-icon"><CloseCircleFilled /></div>
              <h2>本次面试已中止</h2>
              <p>{integrityBlocked}</p>
              <ul style={{ textAlign: 'left', color: '#5b6472', fontSize: 13, lineHeight: 1.9 }}>
                <li>检测到严重违反面试纪律的行为（如切屏他处、远程控制、外接投影/投屏、非本人作答）；</li>
                <li>你已完成的回答会正常提交并由面试官复核；</li>
                <li>若属于误判（例如开考前忘记关闭 TeamViewer），请联系 HR 说明并申请复核。</li>
              </ul>
              <div className="aic-blocked-actions">
                <Button type="primary" ghost onClick={() => setPrivacyOpen(true)}>查看隐私说明</Button>
              </div>
            </div>
            <PrivacyModal open={privacyOpen} onClose={() => setPrivacyOpen(false)} />
          </div>
        </ConfigProvider>
      </div>
    );
  }

  return (
    <ConfigProvider theme={{ token: { colorPrimary: '#315efb', borderRadius: 12 } }}>
      <div className="aic-page">
        <div className="aic-bg-orb aic-orb-1" />
        <div className="aic-bg-orb aic-orb-2" />

        <header className="aic-topbar">
          <div className="aic-brand">
            <span className="aic-brand-mark"><VideoCameraOutlined /></span>
            <span>TalentOS</span><em>AI 面试</em>
          </div>
          <div className="aic-top-actions">
            <span className="aic-secure"><SafetyCertificateOutlined /> 加密传输 · 仅用于本次招聘评估</span>
            <Button type="text" size="small" onClick={() => setPrivacyOpen(true)}>隐私说明</Button>
          </div>
        </header>

        <main className={`aic-shell ${phase === 'landing' ? 'is-landing' : ''} ${phase === 'interview' ? 'is-live' : ''}`}>
          <aside className="aic-stage">
            <div className="aic-stage-glow" />
            <DigitalHuman
              name={interviewer?.name || 'AI 面试官'}
              title={interviewer?.title || '数字人面试官'}
              theme={interviewer?.avatar_theme || 'aurora'}
              photo={interviewer?.photo_url || undefined}
              videoSrc={videoSrc}
              videoReady={Boolean(videoSrc)}
              state={dhState}
              speaking={speech.speaking}
              speakingText={currentQuestion?.text || state?.greeting || undefined}
            />
            <div className="aic-stage-name">{interviewer?.name || 'AI 面试官'}</div>
            <div className="aic-stage-title">{interviewer?.title || '数字人面试官'}</div>
            {phase === 'interview' && <div className={`aic-timer ${remaining != null && remaining < 300 ? 'is-urgent' : ''}`}>
              <ClockCircleOutlined /><span>{remaining != null ? formatClock(remaining) : '--:--'}</span><em>剩余时间</em>
            </div>}
            {phase === 'landing' && <div className="aic-stage-note"><LockOutlined /> 你的回答仅用于本次招聘评估</div>}
            {phase === 'completed' && <div className="aic-stage-note is-done"><CheckCircleFilled /> 面试已完成</div>}
          </aside>

          {phase === 'landing' && (
            <section className="aic-panel aic-landing">
              <div className="aic-eyebrow">AI INTERVIEW · 首轮面试</div>
              <h1>{state?.title || 'AI 数字人面试'}</h1>
              <p className="aic-lead">
                {state?.candidate_name}，你好。我是 <b>{interviewer?.name}</b>，{interviewer?.title || '本次面试的 AI 面试官'}。
                我会用 <b>{total || state?.question_count || 0}</b> 道问题了解你的经历与思考方式，全程约 {state?.duration_minutes || 30} 分钟。
              </p>
              <div className="aic-chip-row">
                {state?.position_title && <span className="aic-chip"><TeamOutlined /> {state.position_title}</span>}
                <span className="aic-chip">{state?.question_count} 道题</span>
                <span className="aic-chip">约 {state?.duration_minutes} 分钟</span>
                <span className="aic-chip">{language === 'en-US' ? 'English' : '中文'} · {DIFFICULTY_LABEL[state?.difficulty || 'middle'] || state?.difficulty}</span>
              </div>
              <Divider />
              <div className="aic-brief">
                <div className="aic-brief-title">开始前，请花 30 秒确认</div>
                <ul>
                  <li><CheckCircleFilled /> 找一个安静、网络稳定的环境</li>
                  <li><CheckCircleFilled /> 允许浏览器使用麦克风；不方便说话也可以全程打字</li>
                  <li><CheckCircleFilled /> 回答会实时转写为文字提交，不会保存原始录音</li>
                  <li><CheckCircleFilled /> 计时开始后，时间到会自动提交当前回答</li>
                </ul>
              </div>
              <div className="aic-landing-controls">
                <div className="aic-voice-switch">
                  <span><SoundOutlined /> 数字人语音播报</span>
                  <Switch checked={voiceOn} disabled={!speech.ttsSupported} onChange={(value) => { setVoiceOn(value); if (!value) speech.stopSpeaking(); }} />
                  {!speech.ttsSupported && <small>当前浏览器不支持语音播报</small>}
                </div>
                <Button size="small" type="link" loading={testingMic} onClick={() => void testMic()}>测试麦克风</Button>
              </div>
              {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 14 }} />}
              <div className="aic-landing-footer">
                <Button type="primary" size="large" className="aic-start" loading={starting} onClick={() => void start()}>
                  开始面试 <span>→</span>
                </Button>
                <div className="aic-consent">点击开始即表示你同意将本次回答用于招聘评估；可随时联系 HR 撤回。</div>
              </div>
            </section>
          )}

          {phase === 'interview' && state && (
            <section className="aic-panel aic-interview">
              <div className="aic-chat-head">
                <div className="aic-chat-id">
                  <DigitalHuman
                    className="aic-mini"
                    name={interviewer?.name || 'AI 面试官'}
                    title={interviewer?.title}
                    theme={interviewer?.avatar_theme || 'aurora'}
                    photo={interviewer?.photo_url || undefined}
                    videoSrc={videoSrc}
                    videoReady={Boolean(videoSrc)}
                    state={dhState}
                    size={38}
                    showStatus={false}
                    speaking={speech.speaking}
                    speakingText={currentQuestion?.text || undefined}
                  />
                  <div>
                  <div className="aic-chat-title">第 {Math.min(currentIndex, total || currentIndex)} / {total || state.question_count} 题</div>
                  <div className="aic-chat-sub">{currentQuestion?.kind === 'follow_up' ? '面试官正在追问细节' : `已作答 ${answered} 题`}</div>
                  </div>
                </div>
                <div className="aic-chat-actions">
                  <Tooltip title="让数字人重读当前问题"><Button size="small" icon={<SoundOutlined />} onClick={() => void repeat()}>重听</Button></Tooltip>
                  <Button size="small" danger type="text" loading={finishing} onClick={finish}>结束面试</Button>
                </div>
              </div>
              <Progress percent={progress?.percent ?? 0} showInfo={false} size="small" strokeColor={{ '0%': '#315efb', '100%': '#7c6bff' }} className="aic-progress" />

              {/* [防作弊] 行为监考状态条：让候选人清楚知道「为什么被提醒」，减少误判争议 */}
              {proctoring.policy.enabled && (
                <Alert
                  style={{ marginBottom: 10 }}
                  type={proctoring.riskLevel === 'clean' ? 'success' : ['watch', 'review'].includes(proctoring.riskLevel) ? 'warning' : 'error'}
                  showIcon
                  message={
                    <span style={{ fontSize: 12 }}>
                      面试纪律监考中 · 风险 {proctoring.riskLevel}
                      {` · ${proctoring.fullscreen ? '全屏' : '非全屏'} `}
                      {` · ${proctoring.screenSharing ? '屏幕共享中' : proctoring.policy.requireScreenShare ? '未共享屏幕（必填）' : '未共享屏幕'}`}
                    </span>
                  }
                  description={
                    <span style={{ fontSize: 12 }}>
                      {integrityNotice || '请勿切换标签页/窗口、勿使用远程控制或投屏；离开面试页会被记录并同步给面试官。'}
                      {!proctoring.fullscreen && (
                        <Button size="small" type="link" style={{ padding: '0 4px' }} onClick={() => void proctoring.enterFullscreen()}>恢复全屏</Button>
                      )}
                      {proctoring.policy.requireScreenShare && !proctoring.screenSharing && (
                        <Button size="small" type="link" danger style={{ padding: '0 4px' }} onClick={() => void proctoring.startScreenShare()}>共享屏幕</Button>
                      )}
                    </span>
                  }
                />
              )}

              <div className="aic-chat" ref={chatRef} onScroll={(event) => {
                const node = event.currentTarget;
                pinnedRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
              }}>
                <div className="aic-chat-inner" ref={chatInnerRef}>
                {messages.map((item, index) => (
                  <div key={`${item.seq}-${index}`} className={`aic-msg ${item.role === 'candidate' ? 'is-candidate' : 'is-interviewer'}`}>
                    <div className="aic-msg-avatar">{item.role === 'candidate' ? (state.candidate_name || '我').slice(0, 1) : <VideoCameraOutlined />}</div>
                    <div>
                      <div className="aic-msg-meta">{item.role === 'candidate' ? '你' : interviewer?.name || 'AI 面试官'}{item.kind === 'follow_up' ? ' · 追问' : ''}</div>
                      <div className="aic-msg-bubble">{item.content}</div>
                    </div>
                  </div>
                ))}

                {currentQuestion && (
                  <div className="aic-question" aria-live="polite">
                    <div className="aic-question-top">
                      <span className="aic-question-badge">{currentQuestion.kind === 'follow_up' ? '追问' : `问题 ${currentIndex}`}</span>
                      <span className="aic-question-dim">{currentQuestion.dimension_label}</span>
                      {speech.speaking && <span className="aic-speaking"><i /><i /><i />正在播报</span>}
                    </div>
                    <div className="aic-question-text">{currentQuestion.text}</div>
                    <button type="button" className="aic-replay" onClick={() => speakWithVideo(currentQuestion.text)}><SoundOutlined /> 再听一遍</button>
                  </div>
                )}

                {submitting && <div className="aic-typing"><Spin size="small" /> 数字人正在分析你的回答…</div>}
                </div>
              </div>

              <div className="aic-composer">
                <div className="aic-mode-row">
                  <div className="aic-mode">
                    <button type="button" className={mode === 'text' ? 'active' : ''} onClick={() => { speech.stopListening(); setMode('text'); }}>文字作答</button>
                    <button type="button" className={mode === 'voice' ? 'active' : ''} onClick={toggleVoiceMode}><AudioOutlined /> 语音作答</button>
                  </div>
                  <span className="aic-answered-hint">{answer.trim().length} / 4000</span>
                </div>
                <Input.TextArea
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  placeholder={mode === 'voice' ? '点击麦克风开始说话，识别结果会出现在这里…' : '在这里输入你的回答，建议用「情境 - 行动 - 结果」讲清楚…'}
                  autoSize={{ minRows: 3, maxRows: 7 }}
                  maxLength={4000}
                  disabled={submitting || finishing}
                  onPressEnter={(event) => { if ((event.metaKey || event.ctrlKey) && !event.shiftKey) { event.preventDefault(); void submit(); } }}
                />
                {mode === 'voice' && <div className="aic-voice-bar">
                  <button type="button" className={`aic-mic ${speech.listening ? 'is-listening' : ''}`} onClick={() => {
                    if (speech.listening) speech.stopListening();
                    else { setVoiceUsed(true); speech.startListening((text) => setAnswer((prev) => `${prev}${prev ? ' ' : ''}${text}`)); }
                  }}>
                    {speech.listening ? <MutedOutlined /> : <AudioOutlined />}
                    {speech.listening ? '停止收音' : '按下说话'}
                  </button>
                  <div className="aic-voice-hint">
                    {speech.listening ? (speech.interim || '正在聆听，说完后点「停止收音」…') : '支持普通话 / English，识别完成后可修改文字再提交'}
                  </div>
                </div>}
                {speech.error && <Alert type="warning" showIcon message={speech.error} closable onClose={() => speech.setError('')} style={{ marginTop: 8 }} />}
                {error && <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />}
                <div className="aic-submit-row">
                  <span className="aic-submit-hint"><LockOutlined /> 回答仅用于本次招聘评估 · ⌘/Ctrl + Enter 提交</span>
                  <Button type="primary" icon={<SendOutlined />} loading={submitting} disabled={!answer.trim() || submitting || finishing} onClick={() => void submit()}>提交回答</Button>
                </div>
              </div>
            </section>
          )}

          {phase === 'completed' && (
            <section className="aic-panel aic-result">
              <div className="aic-result-head">
                <div className="aic-result-icon"><CheckCircleFilled /></div>
                <div>
                  <h1>面试已完成</h1>
                  <p>{state?.candidate_name}，感谢你的时间。你的回答已提交给招聘团队。</p>
                </div>
              </div>

              {result ? <>
                <div className="aic-score-row">
                  <Progress type="circle" percent={Math.round(totalScore || 0)} size={116} strokeWidth={9}
                    strokeColor={{ '0%': '#315efb', '100%': '#7c6bff' }}
                    format={() => <div className="aic-score-value"><b>{totalScore}</b><span>综合得分</span></div>} />
                  <div className="aic-score-side">
                    <div className="aic-score-level">{result.level}</div>
                    <p>{result.summary}</p>
                    <div className="aic-score-meta">完成 {result.answered_count}/{result.question_count} 题 · 评分方式：{result.scoring_method?.startsWith('rule+llm') ? '规则量化 + 大模型润色' : '规则量化'}</div>
                  </div>
                </div>

                <div className="aic-dim-grid">
                  {Object.entries(result.dimension_scores || {}).map(([key, value]) => (
                    <div className="aic-dim" key={key}>
                      <div className="aic-dim-top"><span>{result.dimension_labels[key] || key}</span><b>{value == null ? '未测' : value}</b></div>
                      <Progress percent={value == null ? 0 : Math.round(value)} showInfo={false} size="small" strokeColor={value == null ? '#cdd5e3' : '#315efb'} />
                    </div>
                  ))}
                </div>

                {!!result.strengths?.length && <div className="aic-result-block">
                  <div className="aic-result-title">表现亮点</div>
                  <div className="aic-tags">{result.strengths.map((item) => <span key={item} className="aic-tag good">{item}</span>)}</div>
                </div>}
                {!!result.suggestions?.length && <div className="aic-result-block">
                  <div className="aic-result-title">给你的建议</div>
                  <ul className="aic-suggestions">{result.suggestions.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>}
                <Alert type="info" showIcon message="以上结果由 AI 依据本次回答自动生成，最终录用结论以招聘团队人工复核为准。" />
              </> : <Alert type="info" showIcon message="面试已提交" description="评估报告正在生成，招聘团队会尽快查看并与你联系。" />}

              <div className="aic-result-footer"><LockOutlined /> 你可以安全关闭此页面</div>
            </section>
          )}
        </main>

        <footer className="aic-footer">TalentOS · AI 数字人面试 <span>本页面无需登录，请勿将链接转发给他人</span></footer>
        <PrivacyModal open={privacyOpen} onClose={() => setPrivacyOpen(false)} />
      </div>
    </ConfigProvider>
  );
}

function PrivacyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Modal open={open} onCancel={onClose} footer={<Button type="primary" onClick={onClose}>我知道了</Button>} title="隐私与数据说明" width={560}>
    <div className="aic-privacy">
      <p><b>我们收集什么？</b></p>
      <p>你在面试中的回答文本、作答用时、作答方式（语音转写或键盘输入），以及面试进度。语音识别在你的浏览器中完成，服务端只保存转写后的文字，不保存原始录音。</p>
      <p><b>用来做什么？</b></p>
      <p>仅用于本次招聘的面试评估与人工复核，不用于训练模型，也不会提供给与本次招聘无关的第三方。</p>
      <p><b>如何保存？</b></p>
      <p>面试数据保存在招聘方的业务系统中，按企业数据保留策略管理。你可以联系 HR 查询、更正或申请删除。</p>
      <p><b>自动评估说明？</b></p>
      <p>系统会依据回答完整度、结构、证据与岗位相关度生成量化参考分，仅作为招聘团队的辅助信息，不构成自动录用或拒绝决定。</p>
    </div>
  </Modal>;
}
