/**
 * 在线监考 · 行为采集 Hook（切屏 / 远程控制 / 投屏 / 多显示器 / 虚拟摄像头 / 复制粘贴）
 *
 * 标签：[Agent组] [P1]（防作弊：浏览器侧监考信号采集，服务端 app/ai/proctoring/integrity.py 判定）
 *
 * ================================ 为什么这么设计 ================================
 * 1. 浏览器不可能「证明」候选人没有用另一台设备作弊，但绝大多数作弊路径会在浏览器留下
 *    **可观测信号**：页面失焦 / 标签页切走 / 退出全屏 / 屏幕共享被停 / 显示器数量变化 /
 *    共享画面上出现远程控制工具条 / 摄像头切换成虚拟设备 / 粘贴大段答案…… 本 hook 把这些
 *    信号规范成事件，交给服务端统一打分（**前端不做任何判定**，避免被改脚本绕过）。
 * 2. 三类采集手段互补：
 *    - DOM 事件（visibilitychange / blur / fullscreenchange / copy / paste）：便宜、
 *      覆盖切屏与复制粘贴；但对「另一台设备」「远程软件窗口」无能为力。
 *    - 环境快照（screen.isExtended、显示器分辨率、摄像头 deviceId 漂移）：周期性采集，
 *      能发现外接投影/扩展屏与虚拟摄像头 —— 这是本方案对「投影/远程」的主线索。
 *    - 屏幕共享（getDisplayMedia，可选启用）：把候选人屏幕送进监考流，可在服务端做
 *      更重的分析；**默认不采样上传**，只用来判断「共享是否还在、是否切到了别的窗口」，
 *      兼顾隐私与合规（需候选人明确授权）。
 * 3. 心跳与事件分离：事件立即入队（切屏往往发生在失焦瞬间），心跳按固定间隔发送并顺带
 *    环境快照；心跳中断本身也是违规（候选人把页面关掉/断网骗过时间）。
 * 4. 一切都可降级：浏览器不支持某能力（isExtended / getDisplayMedia / enumerateDevices）
 *    时跳过该项，绝不因此阻塞考试 —— 与后端「检测不可用不阻塞」的策略一致。
 * 5. 上报失败静默重试：网络抖动不该让考生被判作弊，事件留在队列里等下次心跳。
 * =============================================================================
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type ProctorSubject = 'ai_interview' | 'exam' | 'interview' | 'assessment';

export interface ProctorEventPayload {
  type: string;
  detail?: string;
  ts?: number;
}

export interface ProctorPolicy {
  enabled: boolean;
  strict: boolean;
  requireScreenShare: boolean;
  allowMultiDisplay: boolean;
  heartbeatSeconds: number;
  snapshotIntervalSeconds: number;
  blockScore: number;
}

export interface ProctorRequirement {
  screen_share: boolean;
  single_display: boolean;
  no_remote_control: boolean;
  no_virtual_camera: boolean;
  fullscreen: boolean;
}

export interface ProctorServerState {
  enabled: boolean;
  strict: boolean;
  risk_level: string;
  blocked: boolean;
  block_reason: string;
  warning: string;
  requirements: ProctorRequirement;
  heartbeat_seconds: number;
  snapshot_interval_seconds: number;
  screen_shared: boolean;
  display_count: number;
  event_total: number;
}

export interface ProctorViolation {
  ts?: number | null;
  type: string;
  label?: string;
  detail?: string;
  score?: number;
  source?: string;
}

export interface ProctorAck {
  state: ProctorServerState;
  new_violations: ProctorViolation[];
  score: number;
  level: string;
}

/** 上报通道：由调用方注入（复用平台 request 封装，保持鉴权/错误口径一致）。 */
export interface ProctorTransport {
  heartbeat: (subject: ProctorSubject, token: string, body: Record<string, unknown>) => Promise<ProctorAck>;
  snapshot: (subject: ProctorSubject, token: string, body: Record<string, unknown>) => Promise<unknown>;
  state: (subject: ProctorSubject, token: string) => Promise<ProctorServerState>;
}

export interface UseProctoringOptions {
  subject: ProctorSubject;
  token: string;
  transport: ProctorTransport;
  /** 是否开始监考（例如笔试要等「开始答题」、AI 面试要等「开始面试」后才置 true）。 */
  active: boolean;
  /** 服务端下发的策略（来自 invitation visit / config 接口），缺省时用保守默认值。 */
  policy?: Partial<ProctorPolicy>;
  /** 需要屏幕共享时，候选人点「开启屏幕共享」后由页面调用；不传则不做共享采集。 */
  requireScreenShare?: boolean;
  /** 风险等级变化回调（页面用来弹阻断遮罩 / 提示条）。 */
  onLevelChange?: (ack: ProctorAck) => void;
  /** 被服务端阻断时回调（严格模式命中远程控制等硬性风险）。 */
  onBlocked?: (reason: string) => void;
}

const VIRTUAL_CAMERA_HINT = /virtual|obs|snap camera|manycam|xsplit|droidcam|epoccam|camtwist|虚拟/i;

/** 显示器数量估计：优先 screen.isExtended；否则用屏幕尺寸与 window.screenX/Y 偏差做启发式。 */
export function estimateDisplayCount(): number {
  const anyScreen = window.screen as Screen & { isExtended?: boolean };
  if (typeof anyScreen.isExtended === 'boolean') return anyScreen.isExtended ? 2 : 1;
  // 退化启发式：浏览器把窗口挪到副屏时 screenX / screenY 会超出主屏可用区域
  const legacy = window.screen as Screen & { availLeft?: number; availTop?: number };
  const left = legacy.availLeft ?? 0;
  const top = legacy.availTop ?? 0;
  const moved = window.screenX > left + 4 || window.screenY > top + 4
    || window.screenX < left - 4 || window.screenY < top - 4;
  const oversized = window.screen.width > 3200 || window.screen.height > 2000;
  return moved || oversized ? 2 : 1;
}

/** 读取当前摄像头设备（用于发现「换摄像头 / 虚拟摄像头」）。 */
async function inspectCameras(): Promise<{ id?: string; label?: string; virtual?: boolean }> {
  if (!navigator.mediaDevices?.enumerateDevices) return {};
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cams = devices.filter((d) => d.kind === 'videoinput');
    if (!cams.length) return {};
    const active = cams[0];
    return { id: active.deviceId, label: active.label, virtual: VIRTUAL_CAMERA_HINT.test(active.label || '') };
  } catch {
    return {};
  }
}

/** 把屏幕共享画面压成一张小图（只在触发可疑事件时抽样，隐私最小化）。 */
function grabFrame(video: HTMLVideoElement, width = 480): string | null {
  if (!video.videoWidth) return null;
  const scale = Math.min(1, width / video.videoWidth);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.55);
}

export function useProctoring(options: UseProctoringOptions) {
  const { subject, token, transport, active, onLevelChange, onBlocked } = options;
  const policy: ProctorPolicy = {
    enabled: options.policy?.enabled ?? true,
    strict: options.policy?.strict ?? true,
    requireScreenShare: options.requireScreenShare ?? options.policy?.requireScreenShare ?? false,
    allowMultiDisplay: options.policy?.allowMultiDisplay ?? false,
    heartbeatSeconds: Math.max(5, options.policy?.heartbeatSeconds ?? 15),
    snapshotIntervalSeconds: Math.max(5, options.policy?.snapshotIntervalSeconds ?? 20),
    blockScore: options.policy?.blockScore ?? 85,
  };

  const [serverState, setServerState] = useState<ProctorServerState | null>(null);
  const [violations, setViolations] = useState<ProctorViolation[]>([]);
  const [riskLevel, setRiskLevel] = useState('clean');
  const [blocked, setBlocked] = useState(false);
  const [screenSharing, setScreenSharing] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [armed, setArmed] = useState(false);

  const queueRef = useRef<ProctorEventPayload[]>([]);
  const startedAtRef = useRef<number>(Date.now());
  const shareStreamRef = useRef<MediaStream | null>(null);
  const shareVideoRef = useRef<HTMLVideoElement | null>(null);
  const lastSnapshotRef = useRef<number>(0);
  const blockedRef = useRef(false);
  const policyRef = useRef(policy);
  policyRef.current = policy;
  // 回调放 ref：页面每次渲染都会传新的箭头函数，直接进依赖会让心跳 effect 每次渲染重建
  // （表现为「每敲一个字就发一次心跳」）。这里只在使用时取最新实现。
  const levelCbRef = useRef(onLevelChange);
  levelCbRef.current = onLevelChange;
  const blockedCbRef = useRef(onBlocked);
  blockedCbRef.current = onBlocked;

  /** 入队一条事件（同一毫秒内的重复事件合并，避免 visibility+blur 双计）。 */
  const push = useCallback((type: string, detail = '') => {
    if (!policyRef.current.enabled) return;
    const now = Date.now();
    const tail = queueRef.current[queueRef.current.length - 1];
    if (tail && tail.type === type && now - (tail.ts ?? 0) < 800) return;
    queueRef.current.push({ type, detail, ts: now });
    if (queueRef.current.length > 60) queueRef.current = queueRef.current.slice(-60);
  }, []);

  /* ------------------------------------------------------------ 屏幕共享 */
  const startScreenShare = useCallback(async (): Promise<boolean> => {
    if (!navigator.mediaDevices?.getDisplayMedia) return false;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 2 },
        audio: false,
      });
      shareStreamRef.current = stream;
      setScreenSharing(true);
      push('screen_share_started', '候选人已共享屏幕');
      const track = stream.getVideoTracks()[0];
      if (track) {
        track.addEventListener('ended', () => {
          setScreenSharing(false);
          push('screen_share_stopped', '候选人停止了屏幕共享');
          shareStreamRef.current = null;
        });
      }
      // 本地隐藏 video 元素，用于抽帧（不上传视频流本身）
      if (!shareVideoRef.current) {
        const video = document.createElement('video');
        video.muted = true;
        video.playsInline = true;
        shareVideoRef.current = video;
      }
      shareVideoRef.current.srcObject = stream;
      await shareVideoRef.current.play().catch(() => undefined);
      return true;
    } catch {
      push('no_screen_share', '候选人拒绝或取消了屏幕共享授权');
      return false;
    }
  }, [push]);

  const stopScreenShare = useCallback(() => {
    shareStreamRef.current?.getTracks().forEach((t) => t.stop());
    shareStreamRef.current = null;
    setScreenSharing(false);
  }, []);

  /* ------------------------------------------------------------ 全屏 */
  const enterFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      setFullscreen(true);
    } catch {
      push('fullscreen_exit', '浏览器拒绝进入全屏（可能被系统策略拦截）');
    }
  }, [push]);

  /* ------------------------------------------------------------ 事件监听 */
  useEffect(() => {
    if (!active || !policy.enabled) return;

    const onVisibility = () => {
      // 防御性：切换标签页后 requestAnimationFrame 会被节流，visible 恢复时补一条「返回」
      if (document.hidden) push('tab_hidden', '页面被切到后台（可能切换到其他标签页 / 应用）');
      else push('focus_returned', '返回考试页面');
    };
    const onBlur = () => push('window_blur', '考试窗口失去焦点（可能切换到其他窗口或远程软件）');
    const onFocus = () => push('focus_returned', '窗口重新获得焦点');
    const onFullscreen = () => {
      if (document.fullscreenElement) setFullscreen(true);
      else {
        setFullscreen(false);
        push('fullscreen_exit', '退出了考试全屏模式');
      }
    };
    const onCopy = () => push('copy_paste', '复制了页面内容');
    const onPaste = (event: ClipboardEvent) => {
      const text = event.clipboardData?.getData('text') || '';
      push('copy_paste', `粘贴了 ${text.length} 个字符`);
      if (text.length > 400) push('clipboard_burst', `一次性粘贴 ${text.length} 个字符（疑似外部答案）`);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // F12 / Ctrl+Shift+I / Ctrl+Shift+J：开发者工具（可改前端脚本绕过监考）
      const devtools = event.key === 'F12'
        || ((event.ctrlKey || event.metaKey) && event.shiftKey && ['i', 'j', 'c'].includes(event.key.toLowerCase()));
      if (devtools) push('proctor_disabled', '尝试打开开发者工具（可能篡改监考脚本）');
      // Ctrl/Cmd + P：打印试卷
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'p') push('tab_hidden', '尝试打印试卷');
    };
    const onResize = () => {
      const count = estimateDisplayCount();
      if (count >= 2 && !policyRef.current.allowMultiDisplay) {
        push('multi_display', `检测到扩展屏 / 多显示器（当前约 ${count} 块）`);
      }
    };
    const onDeviceChange = () => {
      push('device_added', '检测到音视频设备接入/拔出（可能是采集卡 / 投影设备）');
      void inspectCameras().then((cam) => {
        if (cam.virtual) push('virtual_camera', `摄像头疑似虚拟设备：${cam.label || ''}`);
      });
    };
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      push('tab_hidden', '尝试关闭或刷新考试页面');
      event.preventDefault();
      event.returnValue = '';
    };

    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    document.addEventListener('fullscreenchange', onFullscreen);
    document.addEventListener('copy', onCopy);
    document.addEventListener('paste', onPaste);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', onResize);
    navigator.mediaDevices?.addEventListener?.('devicechange', onDeviceChange);
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('fullscreenchange', onFullscreen);
      document.removeEventListener('copy', onCopy);
      document.removeEventListener('paste', onPaste);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('resize', onResize);
      navigator.mediaDevices?.removeEventListener?.('devicechange', onDeviceChange);
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [active, policy.enabled, push]);

  /* ------------------------------------------------------------ 心跳 */
  const sendHeartbeat = useCallback(async () => {
    if (!active || !policyRef.current.enabled || blockedRef.current) return;
    const events = queueRef.current.splice(0, queueRef.current.length);
    const camera = await inspectCameras();
    const displays = estimateDisplayCount();
    const body = {
      events,
      elapsed_seconds: Math.round((Date.now() - startedAtRef.current) / 1000),
      screen_shared: Boolean(shareStreamRef.current),
      display_count: displays,
      camera_id: camera.id,
      camera_label: camera.label,
      virtual_camera: camera.virtual,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      screen_width: window.screen.width,
      screen_height: window.screen.height,
      platform: navigator.platform,
      user_agent: navigator.userAgent.slice(0, 400),
    };
    try {
      const ack = await transport.heartbeat(subject, token, body);
      setServerState(ack.state);
      setRiskLevel(ack.level);
      if (ack.new_violations?.length) setViolations((prev) => [...ack.new_violations, ...prev].slice(0, 50));
      levelCbRef.current?.(ack);
      if (ack.state.blocked && !blockedRef.current) {
        blockedRef.current = true;
        setBlocked(true);
        blockedCbRef.current?.(ack.state.block_reason || '监考检测到异常，本次作答已停止');
      }
    } catch {
      // 网络失败：事件放回队列（最多保留 60 条），下次心跳补传
      queueRef.current = [...events, ...queueRef.current].slice(-60);
    }
  }, [active, subject, token, transport]);

  useEffect(() => {
    if (!active || !policy.enabled) return;
    startedAtRef.current = Date.now();
    setArmed(true);
    void sendHeartbeat();
    const timer = window.setInterval(() => void sendHeartbeat(), policy.heartbeatSeconds * 1000);
    return () => {
      window.clearInterval(timer);
      setArmed(false);
    };
  }, [active, policy.enabled, policy.heartbeatSeconds, sendHeartbeat]);

  /* ------------------------------------------------------------ 可疑快照（可选） */
  const captureSnapshot = useCallback(async (trigger: string, source: 'screen' | 'camera' = 'screen') => {
    if (!policyRef.current.enabled) return;
    const now = Date.now();
    if (now - lastSnapshotRef.current < policyRef.current.snapshotIntervalSeconds * 1000) return;
    const video = shareVideoRef.current;
    if (source === 'screen' && video) {
      const image = grabFrame(video);
      if (!image) return;
      lastSnapshotRef.current = now;
      await transport.snapshot(subject, token, { image, source: 'screen', trigger_type: trigger, ts: now }).catch(() => undefined);
    }
  }, [subject, token, transport]);

  /* 风险升高时抽样留存共享画面（默认 20s 一次，服务端还有配额上限） */
  useEffect(() => {
    if (!active || !policy.enabled || !screenSharing) return;
    if (!['watch', 'review', 'serious', 'void'].includes(riskLevel)) return;
    void captureSnapshot(`risk_${riskLevel}`);
  }, [active, policy.enabled, riskLevel, screenSharing, captureSnapshot]);

  /* 卸载时释放共享流 */
  useEffect(() => () => {
    shareStreamRef.current?.getTracks().forEach((t) => t.stop());
    shareStreamRef.current = null;
  }, []);

  return {
    /** 监考已启动（心跳与事件监听生效） */
    armed,
    policy,
    serverState,
    violations,
    riskLevel,
    blocked,
    screenSharing,
    fullscreen,
    startScreenShare,
    stopScreenShare,
    enterFullscreen,
    /** 主动上报事件（页面在特定动作上调用，例如「检测到远程工具条」） */
    reportEvent: push,
    /** 立即发一次心跳（交卷前建议调用，确保最后的事件不丢） */
    flush: sendHeartbeat,
  };
}
