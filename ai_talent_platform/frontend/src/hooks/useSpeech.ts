import { useCallback, useEffect, useRef, useState } from 'react';

interface SpeechRecognitionAlternativeLike { transcript: string; confidence: number }
interface SpeechRecognitionResultLike { isFinal: boolean; 0: SpeechRecognitionAlternativeLike; length: number }
interface SpeechRecognitionEventLike { resultIndex: number; results: { length: number; [index: number]: SpeechRecognitionResultLike } }
interface SpeechRecognitionErrorLike { error: string }
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

const ERRORS: Record<string, string> = {
  'not-allowed': '麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试',
  'service-not-allowed': '当前浏览器未启用语音识别服务，请改用文字作答',
  'no-speech': '没有听到声音，请靠近麦克风再试一次',
  'audio-capture': '未检测到麦克风设备',
  network: '语音识别服务网络异常，请改用文字作答',
};

function recognitionCtor(): SpeechRecognitionCtor | null {
  const scope = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return scope.SpeechRecognition || scope.webkitSpeechRecognition || null;
}

function pickVoice(language: string, voiceHint?: string) {
  const voices = window.speechSynthesis?.getVoices() || [];
  const prefix = language.slice(0, 2).toLowerCase();
  const candidates = voices.filter((voice) => voice.lang.toLowerCase().startsWith(prefix));
  if (!candidates.length) return null;
  const female = /xiaoxiao|xiaoyi|female|woman|女|aria|jenny|samantha|zira|ting/i;
  const male = /yunxi|yunyang|yunye|male|man|男|guy|david|mark/i;
  const pattern = voiceHint === 'male' ? male : voiceHint === 'female' ? female : null;
  return (pattern && candidates.find((voice) => pattern.test(voice.name))) || candidates[0];
}

/**
 * 浏览器原生语音能力封装：TTS（数字人播报）+ STT（候选人语音作答）。
 *
 * - 不依赖第三方云服务，候选人公开页无需登录、无需额外密钥；
 * - TTS 按语言 + 性别倾向挑选系统音色，播放前自动打断上一段并停止收音，避免回声；
 * - STT 使用 continuous + interimResults，最终文本通过 onFinal 回调交给答题框；
 * - 不支持时优雅降级为文字模式（supported 标记 + error 文案）。
 */
export function useSpeech(language: string, voiceHint?: string) {
  const [ttsSupported, setTtsSupported] = useState(false);
  const [sttSupported, setSttSupported] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [error, setError] = useState('');
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const languageRef = useRef(language);
  languageRef.current = language;

  useEffect(() => {
    setTtsSupported(typeof window !== 'undefined' && 'speechSynthesis' in window);
    setSttSupported(Boolean(recognitionCtor()));
  }, []);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
    }
  }, []);

  const stopListening = useCallback(() => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      try { recognition.stop(); } catch { /* 已停止 */ }
    }
    setListening(false); setInterim('');
  }, []);

  const speak = useCallback((text: string) => {
    const content = (text || '').trim();
    if (!content || typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    stopListening();
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(content);
    utterance.lang = languageRef.current || 'zh-CN';
    utterance.rate = 0.98;
    utterance.pitch = 1.03;
    const voice = pickVoice(utterance.lang, voiceHint);
    if (voice) utterance.voice = voice;
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
  }, [stopListening, voiceHint]);

  useEffect(() => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    const load = () => window.speechSynthesis.getVoices();
    load();
    window.speechSynthesis.addEventListener?.('voiceschanged', load);
    return () => window.speechSynthesis.removeEventListener?.('voiceschanged', load);
  }, []);

  useEffect(() => () => {
    stopListening();
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
  }, [stopListening]);

  const startListening = useCallback((onFinal: (text: string) => void) => {
    const Ctor = recognitionCtor();
    if (!Ctor) { setError('当前浏览器不支持语音识别，请使用 Chrome / Edge 或改用文字作答'); return; }
    stopSpeaking();
    stopListening();
    setError('');
    const recognition = new Ctor();
    recognition.lang = languageRef.current || 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      let finalText = '';
      let interimText = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript || '';
        if (result.isFinal) finalText += transcript;
        else interimText += transcript;
      }
      if (finalText) onFinal(finalText.trim());
      setInterim(interimText);
    };
    recognition.onerror = (event) => {
      const message = ERRORS[event.error] || `语音识别失败（${event.error}）`;
      setError(message);
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        recognitionRef.current = null;
        setListening(false);
      }
    };
    recognition.onend = () => {
      // continuous 模式下浏览器仍可能因静音自动结束：保留已识别文本，提示可再次点击麦克风。
      setListening(false);
      setInterim('');
      if (recognitionRef.current === recognition) recognitionRef.current = null;
    };
    recognitionRef.current = recognition;
    try {
      recognition.start();
      setListening(true);
    } catch {
      setError('麦克风启动失败，请稍后重试');
      setListening(false);
    }
  }, [stopListening, stopSpeaking]);

  return {
    ttsSupported, sttSupported, speaking, listening, interim, error, setError,
    speak, stopSpeaking, startListening, stopListening,
  };
}
