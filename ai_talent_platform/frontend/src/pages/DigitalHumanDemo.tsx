import { useState } from 'react';
import { Button, Radio, Space, Switch, Typography } from 'antd';
import DigitalHuman, { type DigitalHumanState } from '../components/ai/DigitalHuman';
import '../components/ai/digital-human.css';

const { Text } = Typography;

const PHOTO = '/assets/digital-human-default.png';

export default function DigitalHumanDemo() {
  const [state, setState] = useState<DigitalHumanState>('speaking');
  const [speaking, setSpeaking] = useState(true);
  const [photo, setPhoto] = useState(true);
  const [text, setText] = useState('您好，我是您的 AI 数字人面试官，请开始您的自我介绍。');

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <Text style={{ fontSize: 20, fontWeight: 700, marginBottom: 16 }}>AI 数字人预览</Text>
      <Text type="secondary" style={{ marginBottom: 20 }}>
        嘴形随播报文本节奏张合，位置固定不飘；照片模式为真人肖像，头像模式为升级写实 SVG。
      </Text>

      <Space style={{ marginBottom: 24 }} size="middle">
        <Radio.Group value={state} onChange={(e) => setState(e.target.value as DigitalHumanState)}>
          <Radio value="idle">待机</Radio>
          <Radio value="speaking">正在说话</Radio>
          <Radio value="listening">聆听</Radio>
          <Radio value="thinking">思考</Radio>
          <Radio value="done">结束</Radio>
        </Radio.Group>
        <Switch checked={speaking} onChange={setSpeaking} />
        <Text style={{ marginLeft: 8 }}>语音播报中（驱动唇形）</Text>
        <Switch checked={photo} onChange={setPhoto} />
        <Text style={{ marginLeft: 8 }}>真人照片模式</Text>
      </Space>

      <div style={{ display: 'flex', gap: 40, alignItems: 'flex-start', justifyContent: 'center', minHeight: 420 }}>
        <div style={{ width: 340, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <Text style={{ fontWeight: 600, marginBottom: 8 }}>头像模式（SVG）</Text>
          <DigitalHuman
            name="AI 面试官"
            title="资深招聘官 · 数字人"
            theme="aurora"
            state={state}
            size={300}
            showStatus
            speaking={speaking}
            speakingText={text}
          />
        </div>
        <div style={{ width: 340, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          <Text style={{ fontWeight: 600, marginBottom: 8 }}>真人照片模式</Text>
          <DigitalHuman
            name="AI 面试官"
            title="资深招聘官 · 数字人"
            theme="aurora"
            state={state}
            size={300}
            showStatus
            photo={photo ? PHOTO : undefined}
            mouthPosition={[0.5, 0.51]}
            speaking={speaking}
            speakingText={text}
          />
        </div>
      </div>

      <div style={{ marginTop: 24, border: '1px solid #e8eef6', borderRadius: 12, padding: 14, background: '#f8fafd' }}>
        <Text style={{ fontWeight: 600, marginBottom: 8 }}>播报文本（影响唇形节奏）：</Text>
        <Text type="secondary" style={{ whiteSpace: 'pre-wrap', marginBottom: 10 }}>{text}</Text>
        <input
          style={{ width: '100%', padding: 8, borderRadius: 8, border: '1px solid #dbe4f7', fontSize: 13, boxSizing: 'border-box' }}
          defaultValue={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
    </div>
  );
}
