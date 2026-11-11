import { useId, useCallback, useEffect, useRef, useState } from 'react';
import './digital-human.css';

export type DigitalHumanState = 'idle' | 'waiting' | 'speaking' | 'listening' | 'thinking' | 'done';

interface DigitalHumanProps {
  name: string;
  title?: string;
  theme?: string;
  state?: DigitalHumanState;
  /** 可选：显式覆盖尺寸；未传时由使用方的 CSS 变量控制，便于响应式。 */
  size?: number;
  className?: string;
  showStatus?: boolean;
  /** 真人肖像图片 URL；传入后启用「真人数字人」照片模式，头像区域渲染真实照片，嘴形覆盖层跟随照片嘴部位置。 */
  photo?: string;
  /** 嘴部在照片上的相对位置 [x, y]（0~1），仅 photo 模式生效。默认 [0.5, 0.52] */
  mouthPosition?: [number, number];
  /** 当前正在播报的文本，用于按字数与标点估算唇形节奏；留空则仅用状态兜底动画。 */
  speakingText?: string;
  /** 是否正在语音播报；与 speakingText 配合实现“嘴跟着声音动”的唇形同步。 */
  speaking?: boolean;
  /** 数字人说话头视频 URL（ComfyUI 生成等）；有值时优先播放真人视频，嘴唇与语音真同步。 */
  videoSrc?: string;
  /** 视频是否已就绪可播放。 */
  videoReady?: boolean;
}

const DEFAULT_PHOTO = '/assets/digital-human-default.png';

const THEMES: Record<string, { a: string; b: string; deep: string; glow: string }> = {
  aurora: { a: '#6d9bff', b: '#a07bff', deep: '#233a78', glow: 'rgba(109,155,255,.45)' },
  cyan: { a: '#38d6ee', b: '#3b82f6', deep: '#123b5e', glow: 'rgba(56,214,238,.42)' },
  violet: { a: '#b18cff', b: '#f472b6', deep: '#3a245f', glow: 'rgba(177,140,255,.42)' },
  amber: { a: '#f7c65b', b: '#fb923c', deep: '#5b3a17', glow: 'rgba(247,198,91,.4)' },
};

const STATUS_TEXT: Record<DigitalHumanState, string> = {
  idle: '准备就绪',
  waiting: '等待你的回答',
  speaking: '正在提问…',
  listening: '正在聆听…',
  thinking: '正在分析…',
  done: '面试已结束',
};

/** 估算 TTS 播报时长（ms）：中文按每字 ~200ms，英文/符号按每字 ~100ms，并加上句首句尾静默。 */
function estimateDuration(text: string): number {
  if (!text) return 1500;
  const cn = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
  const other = Math.max(0, text.length - cn);
  return Math.min(Math.max(cn * 200 + other * 100 + 500, 1200), 45000);
}

export default function DigitalHuman({
  name,
  title,
  theme = 'aurora',
  state = 'idle',
  size,
  className = '',
  showStatus = true,
  photo,
  mouthPosition = [0.5, 0.52],
  speakingText = '',
  speaking = false,
  videoSrc,
  videoReady = false,
}: DigitalHumanProps) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const palette = THEMES[theme] || THEMES.aurora;
  const isPhoto = Boolean(photo);
  const src = photo || DEFAULT_PHOTO;

  const style: Record<string, string> = {
    '--dh-a': palette.a,
    '--dh-b': palette.b,
    '--dh-deep': palette.deep,
    '--dh-glow': palette.glow,
  };
  if (size) style['--dh-size'] = `${size}px`;
  if (isPhoto) {
    style['--dh-mouth-x'] = `${mouthPosition[0] * 100}%`;
    style['--dh-mouth-y'] = `${mouthPosition[1] * 100}%`;
  }

  /* -------------------------------------------------------- 唇形同步（估算包络） */
  const [mouthOpen, setMouthOpen] = useState(0); // 0~1，嘴部张合程度
  const startRef = useRef(0);
  const frameRef = useRef(0);

  const stopLipSync = useCallback(() => {
    cancelAnimationFrame(frameRef.current);
    setMouthOpen(0);
  }, []);

  useEffect(() => {
    if (!speaking || !speakingText) { stopLipSync(); return; }
    const duration = estimateDuration(speakingText);
    const start = Date.now();
    startRef.current = start;
    const baseRate = 2.6; // 基准开合频率（Hz），随文本长度略微加快（句子越长语速越快）
    const rate = baseRate + Math.min(speakingText.length / 80, 1.6);
    const loop = () => {
      const now = Date.now();
      if (now - startRef.current > start + duration - 300) {
        // 接近结尾：嘴渐渐闭合
        const t = Math.min((now - (start + duration - 300)) / 300, 1);
        setMouthOpen(Math.max(0, 0.35 * (1 - t)));
        if (t < 1) { frameRef.current = requestAnimationFrame(loop); } else { setMouthOpen(0); }
        return;
      }
      const elapsed = (now - start) / 1000;
      // 正弦开合叠加微抖动，模拟真实嘴唇节奏；位置固定，不会飘
      const open = 0.3 + 0.55 * Math.abs(Math.sin(elapsed * Math.PI * rate))
        + 0.06 * Math.sin(elapsed * Math.PI * 17);
      setMouthOpen(Math.max(0, Math.min(1, open)));
      frameRef.current = requestAnimationFrame(loop);
    };
    frameRef.current = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(frameRef.current); };
  }, [speaking, speakingText, stopLipSync]);

  useEffect(() => stopLipSync, [stopLipSync]);

  /* -------------------------------------------------------- 渲染 */
  const lipOpen = Math.round(mouthOpen * 100) / 100;

  return (
    <div
      className={`dh-stage dh-${state} ${isPhoto ? 'dh-photo' : ''} ${className}`}
      style={style as unknown as React.CSSProperties}
      aria-label={`AI 数字人面试官 ${name}`}
    >
      <div className="dh-aurora" />
      <div className="dh-ring dh-ring-1" />
      <div className="dh-ring dh-ring-2" />
      <div className="dh-particles" aria-hidden="true">
        <i /><i /><i /><i /><i /><i />
      </div>

      {videoSrc && videoReady ? (
        /* ──────── 视频数字人模式：ComfyUI 生成的真人说话头视频 ──────── */
        <video
          className="dh-video"
          src={videoSrc}
          autoPlay
          playsInline
          onEnded={() => {}}
          aria-label={`${name}的数字人视频`}
        />
      ) : isPhoto ? (
        /* ──────── 真人照片模式：真实肖像 + 嘴部覆盖层 ──────── */
        <div className="dh-photo-wrap" aria-hidden="true">
          <img src={src} alt={name} loading="eager" decoding="sync" />
          <svg className="dh-lip-layer" viewBox="0 0 120 60" role="img" aria-label="嘴部动画">
            <defs>
              <linearGradient id={`dh-lip-inside-${uid}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6b2a2a" />
                <stop offset="100%" stopColor="#4a1818" />
              </linearGradient>
            </defs>
            {/* 口腔内部：张合时露出 */}
            <ellipse
              className="dh-lip-inside"
              cx="60" cy="36" rx="16" ry="10"
              fill={`url(#dh-lip-inside-${uid})`}
              style={{ transformOrigin: '60px 36px', transform: `scaleY(${1 + lipOpen * 2.2})` }}
            />
            {/* 上唇 */}
            <path
              className="dh-lip-upper"
              d="M40 30 C50 26 70 26 80 30 C70 36 50 36 40 30 Z"
              fill="#a94f4f"
            />
            {/* 下唇 */}
            <path
              className="dh-lip-lower"
              d="M40 34 C50 38 70 38 80 34 C70 42 50 42 40 34 Z"
              fill="#a94f4f"
            />
            {/* 嘴角笑线 */}
            <path d="M50 38 Q60 42 70 38" fill="none" stroke="#c08a72" strokeWidth="2" strokeLinecap="round" opacity=".55" />
          </svg>
        </div>
      ) : (
        /* ──────── 头像卡通模式（升级写实细节，嘴部随口型张合）──────── */
        <svg className="dh-figure" viewBox="0 0 260 300" role="img" aria-label={`${name}的虚拟形象`}>
          <defs>
            <linearGradient id={`dh-skin-${uid}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#f9ddd4" />
              <stop offset="45%" stopColor="#eec0a4" />
              <stop offset="100%" stopColor="#dfa386" />
            </linearGradient>
            <linearGradient id={`dh-hair-${uid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2a3352" />
              <stop offset="100%" stopColor="#12182b" />
            </linearGradient>
            <linearGradient id={`dh-suit-${uid}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={palette.deep} />
              <stop offset="55%" stopColor="#1b2748" />
              <stop offset="100%" stopColor="#0f172e" />
            </linearGradient>
            <linearGradient id={`dh-accent-${uid}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={palette.a} />
              <stop offset="100%" stopColor={palette.b} />
            </linearGradient>
            <radialGradient id={`dh-cheek-${uid}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#e89383" stopOpacity=".55" />
              <stop offset="100%" stopColor="#e89383" stopOpacity="0" />
            </radialGradient>
            <filter id={`dh-soft-${uid}`} x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="1.4" />
            </filter>
          </defs>

          {/* 肩部 / 西装 */}
          <path d="M24 300 C30 252 68 224 106 216 L154 216 C192 224 230 252 236 300 Z" fill={`url(#dh-suit-${uid})`} />
          <path d="M106 216 C118 240 142 240 154 216 L146 214 L134 236 L122 214 Z" fill="#e8eefc" opacity=".92" />
          <path d="M104 218 L130 248 L156 218" fill="none" stroke={palette.a} strokeWidth="2" opacity=".65" />

          {/* 颈部 */}
          <path d="M112 184 h36 v30 c0 14 -36 14 -36 0 z" fill={`url(#dh-skin-${uid})`} />
          <path d="M112 190 c12 10 24 10 36 0 v10 c-12 9 -24 9 -36 0 z" fill="#c98f74" opacity=".35" />

          {/* 耳朵 */}
          <ellipse cx="76" cy="132" rx="8" ry="13" fill="#e6b195" />
          <ellipse cx="184" cy="132" rx="8" ry="13" fill="#e6b195" />

          {/* 头 / 脸 */}
          <ellipse cx="130" cy="124" rx="54" ry="62" fill={`url(#dh-skin-${uid})`} />
          <ellipse cx="130" cy="140" rx="43" ry="42" fill="#f0c6ac" opacity=".18" filter={`url(#dh-soft-${uid})`} />

          {/* 头发 */}
          <path d="M74 126 C68 76 94 48 130 48 C168 48 193 76 187 128 C184 112 178 100 170 92 C156 78 138 74 120 80 C106 84 96 96 88 110 C82 118 78 122 74 126 Z" fill={`url(#dh-hair-${uid})`} />
          <path d="M74 126 C72 146 77 164 85 174 C80 152 79 136 82 120 Z" fill="#1a2138" opacity=".85" />
          <path d="M186 126 C189 148 184 166 176 176 C181 154 181 138 179 120 Z" fill="#1a2138" opacity=".85" />
          <path d="M98 90 C116 78 144 76 164 84" fill="none" stroke="#4c5a83" strokeWidth="2.2" opacity=".55" strokeLinecap="round" />
          <path d="M108 58 C98 66 91 78 89 92" fill="none" stroke="#4c5a83" strokeWidth="2" opacity=".4" strokeLinecap="round" />
          <path d="M150 52 C162 58 171 68 176 80" fill="none" stroke="#4c5a83" strokeWidth="2" opacity=".32" strokeLinecap="round" />

          {/* 眉毛（表情：thinking 时微皱、speaking 时微扬） */}
          <g className={`dh-brow ${state === 'thinking' ? 'dh-brow-think' : state === 'speaking' ? 'dh-brow-speak' : ''}`}>
            <path d="M96 109 C104 104 116 104 122 108" fill="none" stroke="#2e3550" strokeWidth="3.4" strokeLinecap="round" />
            <path d="M138 108 C146 104 158 104 164 109" fill="none" stroke="#2e3550" strokeWidth="3.4" strokeLinecap="round" />
          </g>

          {/* 眼睛（眨眼 + 表情：speaking 微笑眯眼、thinking 向上看） */}
          <g className="dh-eye" style={{ transformOrigin: '109px 122px' }}>
            <ellipse cx="109" cy="122" rx="10.8" ry="6.9" fill="#fff" />
            <circle className="dh-pupil" cx="110" cy="122" r="4.8" fill="#4a3a32" />
            <circle cx="110" cy="122" r="2.2" fill="#16151a" />
            <circle cx="112" cy="120" r="1.5" fill="#fff" opacity=".95" />
          </g>
          <g className="dh-eye" style={{ transformOrigin: '151px 122px' }}>
            <ellipse cx="151" cy="122" rx="10.8" ry="6.9" fill="#fff" />
            <circle className="dh-pupil" cx="150" cy="122" r="4.8" fill="#4a3a32" />
            <circle cx="150" cy="122" r="2.2" fill="#16151a" />
            <circle cx="152" cy="120" r="1.5" fill="#fff" opacity=".95" />
          </g>

          {/* 鼻子 */}
          <path d="M130 124 C128 134 125 140 121 144 C125 147 134 147 139 144 C135 140 132 134 130 124 Z" fill="#d59a7c" opacity=".55" />

          {/* 嘴：上下唇分离 + 口腔内部，随口型张合，位置固定不飘 */}
          <g className="dh-mouth" style={{ transformOrigin: '130px 158px', transform: `scale(${1 + lipOpen * 0.3}, ${1 + lipOpen * 1.6})` }}>
            <path d="M118 156 C124 152 136 152 142 156 C136 165 124 165 118 156 Z" fill="#a94f4f" />
            <ellipse className="dh-lip-inside" cx="130" cy="158" rx={9 + lipOpen * 6} ry={4 + lipOpen * 7} fill="#6b2a2a" />
            <path d="M121 157.5 C126 155.5 134 155.5 139 157.5 C134 159.5 126 159.5 121 157.5 Z" fill="#fff" opacity=".9" />
          </g>
          <path d="M120 168 C126 171 134 171 140 168" fill="none" stroke="#c08a72" strokeWidth="1.4" strokeLinecap="round" opacity=".6" />

          {/* 腮红 */}
          <ellipse cx="101" cy="143" rx="12" ry="7" fill={`url(#dh-cheek-${uid})`} />
          <ellipse cx="159" cy="143" rx="12" ry="7" fill={`url(#dh-cheek-${uid})`} />

          {/* 耳机 + 麦克风 */}
          <path d="M78 120 C74 70 100 43 132 43 C166 43 190 70 185 122" fill="none" stroke={`url(#dh-accent-${uid})`} strokeWidth="3.6" strokeLinecap="round" opacity=".95" />
          <rect x="68" y="118" width="13" height="27" rx="6" fill={`url(#dh-accent-${uid})`} />
          <rect x="179" y="118" width="13" height="27" rx="6" fill={`url(#dh-accent-${uid})`} />
          <path d="M184 144 C190 154 176 160 158 160" fill="none" stroke={`url(#dh-accent-${uid})`} strokeWidth="2.6" strokeLinecap="round" opacity=".9" />
          <circle cx="157" cy="160" r="4.2" fill={palette.a} />
          <circle cx="157" cy="160" r="7" fill="none" stroke={palette.a} strokeWidth="1" opacity=".35" />

          {/* 胸牌 */}
          <circle cx="184" cy="264" r="15" fill="rgba(255,255,255,.1)" stroke={palette.a} strokeWidth="1.4" />
          <text x="184" y="269" textAnchor="middle" fontSize="11" fontWeight="700" fill="#fff" fontFamily="Inter, sans-serif">AI</text>
        </svg>
      )}

      <div className="dh-waveform" aria-hidden="true">
        <i /><i /><i /><i /><i /><i /><i />
      </div>

      {showStatus && (
        <div className="dh-status">
          <span className="dh-status-dot" />
          <span>{STATUS_TEXT[state]}</span>
        </div>
      )}

      <div className="dh-nameplate">
        <strong>{name}</strong>
        {title && <span>{title}</span>}
      </div>
    </div>
  );
}
