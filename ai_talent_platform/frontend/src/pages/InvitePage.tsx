import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Checkbox, Input, Modal, Radio, Result, Space, Spin, Tag, Typography, message } from 'antd';
import {
  CheckCircleOutlined, ClockCircleOutlined, EyeOutlined, SafetyCertificateOutlined, VideoCameraOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  formatApiError, invitesApi,
  type ExamQuestion, type ExamResult, type ExamStart, type InviteVisit,
} from '../services/api';

type Phase = 'loading' | 'intro' | 'camera' | 'exam' | 'result' | 'failed';

const KIND_TEXT: Record<string, string> = { written_test: '笔试', assessment: '测评', interview: '视频面试', ai_interview: 'AI 数字人面试' };

/**
 * 候选人公开落地页（无需登录，凭 token 访问）：
 * - 笔试/测评：阅读须知 -> 授权摄像头 -> 开始答题（抽题快照、倒计时、监考帧上报）-> 交卷出分；
 * - 面试：查看面试时间 -> 进入面试间（/invite/:token/room）。
 */
export default function InvitePage() {
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>('loading');
  const [visit, setVisit] = useState<InviteVisit | null>(null);
  const [exam, setExam] = useState<ExamStart | null>(null);
  const [result, setResult] = useState<ExamResult | null>(null);
  const [failText, setFailText] = useState('');
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [remaining, setRemaining] = useState(0);
  const [violations, setViolations] = useState<{ total: number; latest: string }>({ total: 0, latest: '' });
  const [submitting, setSubmitting] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const captureTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ---------------------------------------------------------------- 落地信息 */
  const loadVisit = useCallback(async () => {
    try {
      const info = await invitesApi.visit(token);
      if (info.type === 'ai_interview') {
        // 兼容旧链接 / 手动拼 /invite/{token}：AI 面试统一由数字人页面承接
        navigate(`/ai-interview/${encodeURIComponent(token)}`, { replace: true });
        return;
      }
      setVisit(info);
      setPhase('intro');
    } catch (error) {
      setFailText(formatApiError(error));
      setPhase('failed');
    }
  }, [token, navigate]);

  useEffect(() => { loadVisit(); }, [loadVisit]);

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    if (captureTimer.current) { clearInterval(captureTimer.current); captureTimer.current = null; }
  }, []);
  useEffect(() => () => stopCamera(), [stopCamera]);

  /* ---------------------------------------------------------------- 摄像头 */
  const startCamera = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
      streamRef.current = stream;
      if (videoRef.current) { videoRef.current.srcObject = stream; await videoRef.current.play().catch(() => undefined); }
      setPhase('camera');
    } catch (error) {
      message.error('无法获取摄像头权限，请在浏览器地址栏允许摄像头后重试');
      setFailText(error instanceof Error ? error.message : '摄像头不可用');
    }
  }, []);

  /* ---------------------------------------------------------------- 监考帧 */
  const captureAndReport = useCallback(() => {
    const video = videoRef.current;
    if (!video || video.readyState < 2 || !video.videoWidth) return;
    const scale = Math.min(1, 320 / video.videoWidth);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const image = canvas.toDataURL('image/jpeg', 0.6);
    invitesApi.examFrame(token, image).then(check => {
      setViolations(prev => ({
        total: check.total_violations,
        latest: check.violations.length ? check.violations[check.violations.length - 1].detail : prev.latest,
      }));
    }).catch(() => undefined); // 监考上报失败不阻断作答
  }, [token]);

  /* ---------------------------------------------------------------- 开始考试 */
  const beginExam = useCallback(async () => {
    try {
      const started = await invitesApi.examStart(token);
      if (started.status === 'submitted') {
        const r = await invitesApi.examResult(token);
        setResult(r); setPhase('result'); return;
      }
      setExam(started);
      setAnswers({});
      setViolations({ total: 0, latest: '' });
      setPhase('exam');
      if (started.deadline) setRemaining(Math.max(0, dayjs(started.deadline).diff(dayjs(), 'second')));
      if (started.proctor_enabled && streamRef.current) {
        captureAndReport();
        captureTimer.current = setInterval(captureAndReport, Math.max(2, started.frame_interval_seconds) * 1000);
      }
    } catch (error) {
      message.error(formatApiError(error));
      try { const r = await invitesApi.examResult(token); setResult(r); setPhase('result'); }
      catch { setFailText(formatApiError(error)); setPhase('failed'); }
    }
  }, [token, captureAndReport]);

  /* ---------------------------------------------------------------- 倒计时 */
  useEffect(() => {
    if (phase !== 'exam') return;
    const timer = setInterval(() => {
      setRemaining(prev => {
        if (prev <= 1) { clearInterval(timer); return 0; }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [phase]);

  /* ---------------------------------------------------------------- 交卷 */
  const submitExam = useCallback(async (auto = false) => {
    if (submitting) return;
    const unanswered = (exam?.questions ?? []).filter(q =>
      q.type === 'multiple' ? !(answers[String(q.id)] as string[] | undefined)?.length : !answers[String(q.id)]);
    const doSubmit = async () => {
      setSubmitting(true);
      try {
        const payload: Record<string, unknown> = {};
        Object.entries(answers).forEach(([qid, value]) => { if (Array.isArray(value) ? value.length : value !== undefined && value !== '') payload[qid] = value; });
        const r = await invitesApi.examSubmit(token, payload);
        setResult(r); stopCamera(); setPhase('result');
      } catch (error) {
        message.error(formatApiError(error));
        try { const r = await invitesApi.examResult(token); setResult(r); setPhase('result'); } catch { /* 保持当前页 */ }
      } finally { setSubmitting(false); }
    };
    if (auto) { void doSubmit(); return; }
    if (unanswered.length > 0) {
      Modal.confirm({
        title: '确认交卷？',
        content: `还有 ${unanswered.length} 题未作答，交卷后不可修改。`,
        okText: '仍然交卷', cancelText: '继续作答',
        onOk: doSubmit,
      });
    } else {
      Modal.confirm({ title: '确认交卷？', content: '交卷后将立即出分（文本题待人工评阅）。', okText: '交卷', cancelText: '再检查一下', onOk: doSubmit });
    }
  }, [answers, exam, submitting, stopCamera, token]);

  /* ---------------------------------------------------------------- 倒计时归零自动交卷 */
  useEffect(() => {
    if (phase === 'exam' && remaining === 0 && exam) { message.warning('考试时间到，已自动交卷'); void submitExam(true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remaining, phase]);

  const setAnswer = (qid: number, value: string | string[]) => {
    setAnswers(prev => ({ ...prev, [String(qid)]: value }));
  };

  /* 摄像头画面在 intro->exam 切换时元素会重建，重新挂载 stream */
  useEffect(() => {
    if (phase !== 'camera' && phase !== 'exam') return;
    const video = videoRef.current;
    if (video && streamRef.current && !video.srcObject) {
      video.srcObject = streamRef.current;
      void video.play().catch(() => undefined);
    }
  }, [phase]);

  /* ---------------------------------------------------------------- 渲染 */
  const answeredCount = (exam?.questions ?? []).filter(q =>
    Array.isArray(answers[String(q.id)]) ? (answers[String(q.id)] as string[]).length > 0 : Boolean(answers[String(q.id)])).length;
  const kindText = visit ? KIND_TEXT[visit.type] ?? '邀约' : '邀约';
  const mmss = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;

  const shell = (children: ReactNode) => (
    <div style={{ minHeight: '100vh', background: 'linear-gradient(160deg,#f3f7f6 0%,#e9f1ef 100%)', padding: '48px 16px', display: 'flex', justifyContent: 'center' }}>
      <div style={{ width: '100%', maxWidth: 860 }}>{children}</div>
    </div>
  );

  if (phase === 'loading') {
    return shell(<div style={{ textAlign: 'center', paddingTop: 120 }}><Spin size="large" tip="正在加载邀约…" /></div>);
  }
  if (phase === 'failed') {
    return shell(<Result status="warning" title="邀约无法打开" subTitle={failText || '链接无效或已过期'} extra={<Button type="primary" onClick={() => loadVisit()}>重试</Button>} />);
  }
  if (phase === 'result' && result) {
    return shell(
      <Result
        status={result.score !== null && result.score !== undefined ? 'success' : 'info'}
        icon={<CheckCircleOutlined />}
        title={result.status === 'submitted' ? '已交卷' : result.status}
        subTitle={`${kindText}成绩：${result.score ?? '—'} / ${result.total_score ?? '—'} 分${result.pending_manual ? '（含主观题，待人工评阅）' : ''}`}
        extra={[
          <div key="meta" style={{ textAlign: 'center', marginBottom: 16 }}>
            {result.violations_count > 0
              ? <Alert type="warning" showIcon style={{ textAlign: 'left' }} message={`监考记录到 ${result.violations_count} 次异常行为，将随答卷一并复核`} />
              : <Alert type="success" showIcon style={{ textAlign: 'left' }} message="监考全程未记录到异常行为" />}
          </div>,
          <Button key="done" type="primary" href="/">完成</Button>,
        ]}
      />,
    );
  }

  /* -------- 面试落地 -------- */
  if (visit?.type === 'interview') {
    const start = visit.interview_time ? dayjs(visit.interview_time) : null;
    const canEnter = !start || !start.isAfter(dayjs().add(15, 'minute'));
    const diff = start ? start.diff(dayjs()) : 0;
    return shell(
      <div style={{ background: '#fff', borderRadius: 14, padding: '34px 30px', boxShadow: '0 14px 44px rgba(26,62,73,.1)' }}>
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>面试邀约</Typography.Title>
            <Typography.Text type="secondary">{visit.title}</Typography.Text>
          </div>
          <Space size={16} wrap>
            {visit.candidate_name && <Tag color="blue">候选人：{visit.candidate_name}</Tag>}
            {start && <Tag color="geekblue"><ClockCircleOutlined /> {start.format('YYYY-MM-DD HH:mm')}</Tag>}
            <Tag>链接有效期至 {visit.expires_at ? dayjs(visit.expires_at).format('YYYY-MM-DD HH:mm') : '—'}</Tag>
          </Space>
          {!canEnter && start && (
            <Alert type="info" showIcon message={`面试尚未开放进场，距离开始还有 ${Math.max(0, Math.floor(diff / 60000))} 分钟（可提前 15 分钟进入测试设备）`} />
          )}
          <Alert type="warning" showIcon icon={<VideoCameraOutlined />}
            message="进入面试间将开启摄像头与麦克风" description="请确认环境安静、光线充足；面试官进入后即可双向视频通话。" />
          <Button type="primary" size="large" block disabled={!canEnter}
            onClick={() => navigate(`/invite/${token}/room`)}>
            进入面试间
          </Button>
        </Space>
      </div>,
    );
  }

  /* -------- 笔试须知 -------- */
  if (phase === 'intro') {
    return shell(
      <div style={{ background: '#fff', borderRadius: 14, padding: '34px 30px', boxShadow: '0 14px 44px rgba(26,62,73,.1)' }}>
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>{kindText}邀约</Typography.Title>
            <Typography.Text type="secondary">{visit?.title}</Typography.Text>
          </div>
          <Space size={16} wrap>
            {visit?.candidate_name && <Tag color="blue">候选人：{visit.candidate_name}</Tag>}
            {visit?.question_count && <Tag color="green">{visit.question_count} 题</Tag>}
            <Tag color="orange"><ClockCircleOutlined /> 时长 {visit?.duration_minutes ?? 60} 分钟</Tag>
            <Tag>链接有效至 {visit?.expires_at ? dayjs(visit.expires_at).format('MM-DD HH:mm') : '—'}</Tag>
          </Space>
          <Alert type="info" showIcon icon={<EyeOutlined />} message="考试须知"
            description={<ul style={{ margin: 0, paddingLeft: 18 }}>
              <li>全程需打开摄像头：系统通过 {visit?.proctor_engine === 'disabled' ? '基础采集（当前监考引擎未启用）' : `AI 视觉检测（${visit?.proctor_engine}）`} 判断是否存在代考、多人、遮挡等异常；</li>
              <li>题量按您的简历解析结果自动组卷，从平台题库随机抽题；</li>
              <li>倒计时结束将自动交卷，请合理安排时间。</li>
            </ul>} />
          <Button type="primary" size="large" block icon={<SafetyCertificateOutlined />} onClick={startCamera}>第一步：开启摄像头</Button>
        </Space>
      </div>,
    );
  }

  /* -------- 摄像头预览确认 -------- */
  if (phase === 'camera') {
    return shell(
      <div style={{ background: '#fff', borderRadius: 14, padding: '34px 30px', boxShadow: '0 14px 44px rgba(26,62,73,.1)' }}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>摄像头画面确认</Typography.Title>
          <video ref={videoRef} autoPlay playsInline muted
            style={{ width: '100%', maxHeight: 340, objectFit: 'cover', borderRadius: 10, background: '#101818' }} />
          <Alert type="success" showIcon message="摄像头已就绪，考试过程中将持续采样上报（每帧本地截取，不上传视频流）" />
          <Space size={12}>
            <Button size="large" onClick={() => { stopCamera(); setPhase('intro'); }}>返回</Button>
            <Button type="primary" size="large" block onClick={beginExam}>开始答题</Button>
          </Space>
        </Space>
      </div>,
    );
  }

  /* -------- 答题 -------- */
  return shell(
    <div style={{ background: '#fff', borderRadius: 14, padding: '24px 26px', boxShadow: '0 14px 44px rgba(26,62,73,.1)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10, marginBottom: 16 }}>
        <Space>
          <Tag color="blue">{kindText}作答中</Tag>
          <Tag color={remaining < 300 ? 'red' : 'orange'} icon={<ClockCircleOutlined />}>{mmss}</Tag>
          <Tag>进度 {answeredCount}/{exam?.questions.length ?? 0}</Tag>
        </Space>
        <video ref={videoRef} autoPlay playsInline muted
          style={{ width: 132, height: 88, objectFit: 'cover', borderRadius: 8, background: '#101818', border: '1px solid #d9e4e4' }} />
      </div>
      {violations.total > 0 && (
        <Alert type="error" showIcon style={{ marginBottom: 14 }}
          message={`监考告警（累计 ${violations.total} 次）`}
          description={violations.latest || '请确保本人独立作答、画面中仅您一人'} />
      )}
      <Space direction="vertical" size={18} style={{ width: '100%' }}>
        {(exam?.questions ?? []).map(q => {
          const qid = String(q.id);
          const value = answers[qid];
          if (q.type === 'single') {
            return (
              <div key={qid} style={{ borderBottom: '1px dashed #e3ebeb', paddingBottom: 14 }}>
                <Typography.Paragraph strong style={{ marginBottom: 10 }}>{q.seq}. {q.content} <Tag color="blue">单选</Tag></Typography.Paragraph>
                <Radio.Group value={value} onChange={e => setAnswer(q.id, e.target.value)}
                  options={(q.options ?? []).map(o => ({ label: `${o.key}. ${o.text}`, value: o.key }))} />
              </div>
            );
          }
          if (q.type === 'multiple') {
            return (
              <div key={qid} style={{ borderBottom: '1px dashed #e3ebeb', paddingBottom: 14 }}>
                <Typography.Paragraph strong style={{ marginBottom: 10 }}>{q.seq}. {q.content} <Tag color="purple">多选</Tag></Typography.Paragraph>
                <Checkbox.Group value={(value as string[]) ?? []}
                  onChange={v => setAnswer(q.id, v as string[])}
                  options={(q.options ?? []).map(o => ({ label: `${o.key}. ${o.text}`, value: o.key }))} />
              </div>
            );
          }
          return (
            <div key={qid} style={{ borderBottom: '1px dashed #e3ebeb', paddingBottom: 14 }}>
              <Typography.Paragraph strong style={{ marginBottom: 10 }}>{q.seq}. {q.content} <Tag>简答</Tag></Typography.Paragraph>
              <Input.TextArea rows={4} value={(value as string) ?? ''} maxLength={2000}
                onChange={e => setAnswer(q.id, e.target.value)} placeholder="请输入你的回答…" showCount />
            </div>
          );
        })}
        <Button type="primary" size="large" block loading={submitting} disabled={remaining === 0}
          onClick={() => submitExam(false)}>交卷并出分</Button>
      </Space>
    </div>,
  );
}
