import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { Alert, Button, Result, Space, Spin, Tag, Tooltip, Typography, message } from 'antd';
import {
  AudioOutlined, AudioMutedOutlined, DisconnectOutlined, PhoneFilled, SafetyCertificateOutlined,
  UserOutlined, VideoCameraOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { formatApiError, invitesApi, inviteWsUrl, proctoringApi, type InviteVisit } from '../services/api';
import { useProctoring } from '../hooks/useProctoring';

type Role = 'candidate' | 'interviewer';
type WsStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

interface RoomMessage {
  type: string;
  role?: Role;
  peer_connected?: boolean;
  from?: Role;
  sdp?: RTCSessionDescriptionInit;
  candidate?: RTCIceCandidateInit;
  code?: number;
  message?: string;
}

/**
 * 面试间（候选人 / 面试官共用，凭同一 token + role 进房）：
 * getUserMedia（摄像头+麦克风）-> WebSocket 信令（offer/answer/ice）-> RTCPeerConnection 双向视频。
 * 面试官：`/invite/:token/room?role=interviewer`（由邀约详情页生成入口链接）。
 */
export default function InterviewRoomPage() {
  const { token = '' } = useParams();
  const location = useLocation();
  const role: Role = new URLSearchParams(location.search).get('role') === 'interviewer' ? 'interviewer' : 'candidate';

  const [visit, setVisit] = useState<InviteVisit | null>(null);
  const [loading, setLoading] = useState(true);
  const [phase, setPhase] = useState<'gate' | 'room' | 'ended' | 'failed'>('gate');
  const [failText, setFailText] = useState('');
  const [wsStatus, setWsStatus] = useState<WsStatus>('idle');
  const [peerConnected, setPeerConnected] = useState(false);
  const [micOn, setMicOn] = useState(true);
  const [camOn, setCamOn] = useState(true);
  const [elapsed, setElapsed] = useState(0);
  const [sysNotice, setSysNotice] = useState('');

  const localVideoRef = useRef<HTMLVideoElement>(null);
  const remoteVideoRef = useRef<HTMLVideoElement>(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteStreamRef = useRef<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pendingIceRef = useRef<RTCIceCandidateInit[]>([]);
  const makingOfferRef = useRef(false);
  const [integrityNotice, setIntegrityNotice] = useState('');

  /* ------------------------------------------------------- 行为监考（仅候选人端）
   * 视频面试同样存在「切屏查答案 / 第三人提示 / 远程代面」，这里复用同一套
   * useProctoring：切屏、失焦、退出全屏、多显示器、远程控制、虚拟摄像头都会被记录。
   * 面试官端不采集（避免把面试官自己的操作判成候选人违规）。
   */
  const proctoring = useProctoring({
    subject: 'interview',
    token,
    transport: proctoringApi,
    active: role === 'candidate' && phase === 'room',
    policy: {
      enabled: true,
      strict: false,           // 视频面试由真人主导，命中风险只提示不阻断
      requireScreenShare: false,
      allowMultiDisplay: false,
      heartbeatSeconds: 20,
      snapshotIntervalSeconds: 30,
    },
    onLevelChange: (ack) => {
      if (ack.state.warning) setIntegrityNotice(ack.state.warning);
      if (ack.new_violations.length) {
        const last = ack.new_violations[ack.new_violations.length - 1];
        setSysNotice(`监考提示：${last.label || last.detail || '检测到异常行为'}`);
      }
    },
  });

  /* ------------------------------------------------------------------ 落地信息 */
  useEffect(() => {
    invitesApi.visit(token).then(info => {
      if (info.type !== 'interview') { setFailText('该邀约不是面试'); setPhase('failed'); return; }
      setVisit(info);
      const start = info.interview_time ? dayjs(info.interview_time) : null;
      if (start && start.isAfter(dayjs().add(15, 'minute'))) setSysNotice('尚未到进场时间（可提前 15 分钟进入）');
      setPhase('gate');
      setLoading(false);
    }).catch(error => { setFailText(formatApiError(error)); setPhase('failed'); setLoading(false); });
    return () => { /* unmount 时统一清理 */ };
  }, [token]);

  /* 计时 */
  useEffect(() => {
    if (phase !== 'room') return;
    const t = setInterval(() => setElapsed(prev => prev + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  /* ------------------------------------------------------------------ 清理 */
  const teardown = useCallback((keepNotice = false) => {
    try { wsRef.current?.send(JSON.stringify({ type: 'leave' })); } catch { /* noop */ }
    wsRef.current?.close(); wsRef.current = null;
    pcRef.current?.close(); pcRef.current = null;
    localStreamRef.current?.getTracks().forEach(t => t.stop());
    localStreamRef.current = null;
    remoteStreamRef.current = null;
    setPeerConnected(false);
    setWsStatus('closed');
    if (!keepNotice) setSysNotice('');
  }, []);

  useEffect(() => () => { teardown(); }, [teardown]);

  /* ------------------------------------------------------------------ WebRTC */
  const send = useCallback((msg: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }, []);

  const ensurePeer = useCallback(() => {
    if (pcRef.current) return pcRef.current;
    const pc = new RTCPeerConnection({ iceServers: visit?.ice_servers?.length ? visit.ice_servers : [] });
    pcRef.current = pc;
    localStreamRef.current?.getTracks().forEach(track => pc.addTrack(track, localStreamRef.current!));
    pc.onicecandidate = event => { if (event.candidate) send({ type: 'ice', candidate: event.candidate.toJSON() }); };
    pc.ontrack = event => {
      if (!remoteStreamRef.current) remoteStreamRef.current = new MediaStream();
      remoteStreamRef.current.addTrack(event.track);
      if (remoteVideoRef.current) remoteVideoRef.current.srcObject = remoteStreamRef.current;
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') setPeerConnected(false);
      if (pc.connectionState === 'connected') setPeerConnected(true);
    };
    return pc;
  }, [visit, send]);

  /** 面试官为 offer 方（imperfect 双方只有一方发起，避免 glare）。 */
  const makeOffer = useCallback(async () => {
    if (makingOfferRef.current) return;
    makingOfferRef.current = true;
    try {
      const pc = ensurePeer();
      const offer = await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true });
      await pc.setLocalDescription(offer);
      send({ type: 'offer', sdp: pc.localDescription });
    } catch (error) {
      console.warn('createOffer failed', error);
    } finally { makingOfferRef.current = false; }
  }, [ensurePeer, send]);

  const flushIce = useCallback(async () => {
    const pc = pcRef.current;
    if (!pc) return;
    for (const c of pendingIceRef.current.splice(0)) {
      try { await pc.addIceCandidate(c); } catch (e) { console.warn('addIceCandidate', e); }
    }
  }, []);

  const handleSignal = useCallback(async (msg: RoomMessage) => {
    switch (msg.type) {
      case 'joined': {
        setWsStatus('open');
        if (msg.peer_connected) {
          setPeerConnected(true);
          if (role === 'interviewer') void makeOffer();
        }
        break;
      }
      case 'peer-joined': {
        setPeerConnected(true);
        if (role === 'interviewer') void makeOffer();
        break;
      }
      case 'peer-left': {
        setPeerConnected(false);
        setSysNotice('对方已离开面试间，等待重新加入…');
        break;
      }
      case 'offer': {
        const pc = ensurePeer();
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp!));
        await flushIce();
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        send({ type: 'answer', sdp: pc.localDescription });
        break;
      }
      case 'answer': {
        const pc = pcRef.current;
        if (pc) { await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp!)); await flushIce(); }
        break;
      }
      case 'ice': {
        if (msg.candidate) {
          const pc = pcRef.current;
          if (pc && pc.remoteDescription) { try { await pc.addIceCandidate(msg.candidate); } catch (e) { console.warn(e); } }
          else pendingIceRef.current.push(msg.candidate);
        }
        break;
      }
      case 'error': {
        setWsStatus('error');
        setFailText(msg.message || '无法进入面试间');
        if (msg.code === 4403) { message.warning(msg.message || '未到面试时间'); setPhase('gate'); }
        else { setPhase('failed'); }
        break;
      }
      default: break;
    }
  }, [role, makeOffer, ensurePeer, flushIce, send]);

  /* ------------------------------------------------------------------ 进房 */
  const enterRoom = useCallback(async () => {
    setSysNotice('');
    // getUserMedia 仅在安全上下文（HTTPS 或 localhost）暴露；用局域网 IP 以 http 访问时
    // navigator.mediaDevices 为 undefined，直接调用会抛
    // "Cannot read properties of undefined (reading 'getUserMedia')"。
    if (!navigator.mediaDevices?.getUserMedia) {
      const insecure = window.isSecureContext === false;
      setFailText(insecure
        ? '当前页面通过 HTTP + 非 localhost 地址访问，浏览器已禁用摄像头/麦克风。请改用 http://localhost:5173 打开本页，或以 HTTPS 模式启动前端（前端目录执行 VITE_DEV_HTTPS=1 npm run dev 后访问 https://<地址>:5173，首次需信任自签名证书）。'
        : '当前浏览器不支持媒体设备访问（navigator.mediaDevices 不可用）。请使用较新版本的 Chrome / Edge 并检查浏览器权限设置。');
      setPhase('failed');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 1280, height: 720, facingMode: 'user' }, audio: { echoCancellation: true },
      });
      localStreamRef.current = stream;
      if (localVideoRef.current) { localVideoRef.current.srcObject = stream; await localVideoRef.current.play().catch(() => undefined); }
    } catch (error) {
      setFailText('无法获取摄像头/麦克风权限：' + (error instanceof Error ? error.message : String(error)));
      setPhase('failed');
      return;
    }
    setPhase('room');
    setWsStatus('connecting');
    const ws = new WebSocket(inviteWsUrl(token, role));
    wsRef.current = ws;
    ws.onmessage = event => {
      let msg: RoomMessage;
      try { msg = JSON.parse(event.data as string) as RoomMessage; } catch { return; }
      void handleSignal(msg);
    };
    ws.onopen = () => setWsStatus('open');
    ws.onclose = event => {
      setWsStatus(event.code === 1000 || event.code === 1005 ? 'closed' : 'error');
      if (event.code === 4401) { setFailText('邀约无效或已过期'); setPhase('failed'); }
      else if (event.code === 4403) { setSysNotice('未到面试时间或已超出面试时段'); setPhase('gate'); }
      else if (event.code === 4409) { setFailText('该角色已在面试间中（请关闭其他标签页）'); setPhase('failed'); }
      else if (event.code === 1011) { setFailText('服务不可用，请稍后重试'); setPhase('failed'); }
      else if (phaseRef.current === 'room') setSysNotice('连接已断开，可点击重新连接');
    };
    ws.onerror = () => setWsStatus('error');
  }, [token, role, handleSignal]);

  const reconnect = useCallback(() => { teardown(); setTimeout(() => void enterRoom(), 200); }, [teardown, enterRoom]);

  /* ------------------------------------------------------------------ 控制 */
  const toggleMic = () => {
    const on = !micOn;
    localStreamRef.current?.getAudioTracks().forEach(t => { t.enabled = on; });
    setMicOn(on);
  };
  const toggleCam = () => {
    const on = !camOn;
    localStreamRef.current?.getVideoTracks().forEach(t => { t.enabled = on; });
    setCamOn(on);
  };
  const hangUp = () => { teardown(); setPhase('ended'); };

  /* ------------------------------------------------------------------ 渲染 */
  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`;
  const gateReady = !visit?.interview_time || !dayjs(visit.interview_time).isAfter(dayjs().add(15, 'minute'));

  if (loading) {
    return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}><Spin size="large" tip="加载面试间…" /></div>;
  }
  if (phase === 'failed') {
    return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f1f5f4' }}>
      <Result status="warning" title="无法进入面试间" subTitle={failText}
        extra={<Button type="primary" href={`/invite/${token}`}>返回邀约页</Button>} />
    </div>;
  }
  if (phase === 'ended') {
    return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f1f5f4' }}>
      <Result status="success" title="面试已结束"
        subTitle={`通话时长 ${mmss} · 感谢参与`}
        extra={<Button type="primary" href={`/invite/${token}`}>返回</Button>} />
    </div>;
  }
  if (phase === 'gate') {
    return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f1f5f4', padding: 24 }}>
      <div style={{ background: '#fff', borderRadius: 14, padding: '30px 28px', maxWidth: 520, width: '100%', boxShadow: '0 14px 44px rgba(26,62,73,.1)' }}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            面试间准备 {role === 'interviewer' && <Tag color="gold">面试官</Tag>}
          </Typography.Title>
          {visit?.interview_time && (
            <Alert type={gateReady ? 'success' : 'info'} showIcon
              message={gateReady
                ? `面试时间：${dayjs(visit.interview_time).format('YYYY-MM-DD HH:mm')}（已开放进场）`
                : `面试时间 ${dayjs(visit.interview_time).format('YYYY-MM-DD HH:mm')}，可提前 15 分钟进场`}
              description={gateReady ? undefined : `当前为 ${dayjs().format('HH:mm')}，进场通道尚未开放`} />
          )}
          <Alert type="warning" showIcon icon={<VideoCameraOutlined />} message="进入后将开启摄像头与麦克风"
            description="与对方建立连接后即可双向视频；请检查网络与光线。" />
          <Button type="primary" size="large" block icon={<PhoneFilled />} disabled={!gateReady} onClick={() => void enterRoom()}>
            开启设备并进入面试间
          </Button>
        </Space>
      </div>
    </div>;
  }

  /* room */
  return (
    <div style={{ minHeight: '100vh', background: '#0f1a1c', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', color: '#cfe0dd' }}>
        <Space>
          <Typography.Text strong style={{ color: '#e8f3f0' }}>面试间</Typography.Text>
          <Tag color={wsStatus === 'open' ? 'success' : wsStatus === 'error' ? 'error' : 'processing'}>
            {wsStatus === 'open' ? '信令已连接' : wsStatus === 'connecting' ? '连接中…' : wsStatus === 'error' ? '连接异常' : '已断开'}
          </Tag>
          <Tag color={peerConnected ? 'success' : 'default'}>{peerConnected ? '对方在场' : '等待对方加入'}</Tag>
          <Tag>{role === 'interviewer' ? '面试官' : '候选人'}</Tag>
          {role === 'candidate' && proctoring.policy.enabled && (
            <Tooltip title={integrityNotice || '切屏 / 退出全屏 / 多显示器 / 远程控制 / 投屏都会被记录并同步给面试官'}>
              <Tag color={proctoring.riskLevel === 'clean' ? 'green' : ['watch', 'review'].includes(proctoring.riskLevel) ? 'gold' : 'red'}>
                <SafetyCertificateOutlined /> 监考 {proctoring.riskLevel}
              </Tag>
            </Tooltip>
          )}
        </Space>
        <Space>
          {sysNotice && <Typography.Text style={{ color: '#e8c98a' }}>{sysNotice}</Typography.Text>}
          <Tag color="blue">{mmss}</Tag>
        </Space>
      </div>
      <div style={{ flex: 1, position: 'relative', margin: '0 20px', borderRadius: 14, overflow: 'hidden', background: '#152426', minHeight: 360 }}>
        <video ref={remoteVideoRef} autoPlay playsInline
          style={{ width: '100%', height: '100%', objectFit: 'cover', position: 'absolute', inset: 0, background: '#152426' }} />
        {!peerConnected && (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#7fa39d' }}>
            <Space direction="vertical" size={8} style={{ textAlign: 'center' }}>
              <Spin />
              <span>{wsStatus === 'open' ? '等待对方进入面试间…' : '正在建立信令连接…'}</span>
            </Space>
          </div>
        )}
        <video ref={localVideoRef} autoPlay playsInline muted
          style={{ position: 'absolute', right: 16, bottom: 16, width: 200, height: 140, objectFit: 'cover', borderRadius: 10, border: '2px solid rgba(255,255,255,.35)', background: '#000', zIndex: 2 }} />
        {!camOn && <div style={{ position: 'absolute', right: 16, bottom: 16, width: 200, height: 140, borderRadius: 10, background: 'rgba(0,0,0,.6)', color: '#fff', display: 'grid', placeItems: 'center', zIndex: 3 }}>
          <Space direction="vertical" size={4} style={{ textAlign: 'center' }}><UserOutlined style={{ fontSize: 28 }} /><span style={{ fontSize: 12 }}>摄像头已关闭</span></Space>
        </div>}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 14, padding: '18px 0 26px' }}>
        <Button shape="circle" size="large" icon={micOn ? <AudioOutlined /> : <AudioMutedOutlined />} onClick={toggleMic} title={micOn ? '静音' : '取消静音'} style={{ background: micOn ? '#fff' : '#c0483c', color: micOn ? '#123' : '#fff' }} />
        <Button shape="circle" size="large" icon={<VideoCameraOutlined />} onClick={toggleCam} title={camOn ? '关闭摄像头' : '开启摄像头'} style={{ background: camOn ? '#fff' : '#c0483c', color: camOn ? '#123' : '#fff' }} />
        <Button shape="circle" size="large" icon={<DisconnectOutlined />} onClick={reconnect} title="重新连接" />
        <Button shape="circle" size="large" danger icon={<PhoneFilled />} onClick={hangUp} title="离开面试间" />
      </div>
      {wsStatus === 'error' && (
        <Alert type="error" banner message="信令连接异常，点击上方「重新连接」重试" style={{ margin: '0 20px' }} />
      )}
    </div>
  );
}
