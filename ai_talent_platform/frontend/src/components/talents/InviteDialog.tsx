import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Checkbox, DatePicker, Divider, Input, InputNumber, Modal, Segmented, Select, Space, Typography, message } from 'antd';
import { CopyOutlined, LinkOutlined, RobotOutlined, VideoCameraOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import type { Talent } from '../../types';
import { invitesApi, formatApiError, type InviteChannel, type InviteInfo, type InviteType } from '../../services/api';

interface Props {
  talent: Talent | null;
  initialKind?: InviteType;
  open: boolean;
  onClose: () => void;
  /** 创建成功并关闭对话框时回调（用于打开邀约记录抽屉，防止链接只出现一次就丢失） */
  onCreated?: (info: InviteInfo) => void;
}

const KIND_OPTIONS = [
  { label: '笔试邀约', value: 'written_test' },
  { label: '测评邀约', value: 'assessment' },
  { label: '面试邀约', value: 'interview' },
];

const INTERVIEW_MODES = [
  { label: 'AI 数字人面试', value: 'ai' },
  { label: '视频面试', value: 'video' },
];

const AI_PERSONAS = [
  { value: 'friendly_hr', label: '晓岚 · 资深招聘官', desc: '亲和引导，关注真实经历' },
  { value: 'tech_lead', label: '陈拓 · 技术负责人', desc: '深挖技术深度与工程判断' },
  { value: 'strict_expert', label: '沈砚 · 首席面试专家', desc: '高标准、重证据的结构化面试' },
  { value: 'global_interviewer', label: 'Ava · Global Talent Partner', desc: 'Bilingual competency interview' },
];

/** 后端可能按请求 Host 生成 localhost 链接；预览域名下改写为当前 origin，保证候选人能直接打开。 */
function resolvePublicUrl(url: string) {
  if (!url) return '';
  try {
    const parsed = new URL(url, window.location.origin);
    const local = new Set(['localhost', '127.0.0.1', '0.0.0.0']);
    if (local.has(parsed.hostname) && !local.has(window.location.hostname)) {
      return `${window.location.origin}${parsed.pathname}${parsed.search}`;
    }
    return parsed.toString();
  } catch { return url; }
}

/** 列表接口返回脱敏手机号/邮箱（138****1234）：不要回填，留空时后端按 talent_id 取档案真实联系方式。 */
function usableContact(value?: string | null) {
  return value && !value.includes('*') ? value : '';
}

const TYPE_LABEL: Record<string, string> = {
  written_test: '笔试', assessment: '测评', interview: '视频面试', ai_interview: 'AI 数字人面试',
};

/** 邀约创建对话框：题量按简历解析（可覆盖）/ 面试设时间 -> 创建并按渠道发送链接。 */
export default function InviteDialog({ talent, initialKind = 'written_test', open, onClose, onCreated }: Props) {
  const [kind, setKind] = useState<InviteType>(initialKind);
  const [channels, setChannels] = useState<InviteChannel[]>(['sms']);
  const [mobile, setMobile] = useState('');
  const [email, setEmail] = useState('');
  const [questionCount, setQuestionCount] = useState<number | null>(null);
  const [duration, setDuration] = useState(60);
  const [interviewTime, setInterviewTime] = useState<Dayjs | null>(null);
  const [interviewMode, setInterviewMode] = useState<'ai' | 'video'>('ai');
  const [persona, setPersona] = useState('friendly_hr');
  const [aiQuestionCount, setAiQuestionCount] = useState(6);
  const [aiDuration, setAiDuration] = useState(30);
  const [aiExpiresHours, setAiExpiresHours] = useState(72);
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<InviteInfo | null>(null);

  useEffect(() => {
    if (!open) return;
    setKind(initialKind);
    setCreated(null);
    setMobile(usableContact(talent?.mobile));
    setEmail(usableContact(talent?.email));
    setQuestionCount(null);
    setDuration(60);
    setInterviewTime(initialKind === 'interview' ? dayjs().add(1, 'day').hour(10).minute(0) : null);
    setChannels(['sms']);
    setInterviewMode('ai');
    setPersona('friendly_hr');
    setAiQuestionCount(6);
    setAiDuration(30);
    setAiExpiresHours(72);
  }, [open, initialKind, talent]);

  const isInterview = kind === 'interview';
  const isAiInterview = isInterview && interviewMode === 'ai';
  const canSubmit = useMemo(() => {
    if (channels.length === 0) return false;
    // 档案里的手机号/邮箱在后端按 talent_id 解析（前端拿到的是脱敏值），因此有档案时允许留空
    const knownContact = Boolean(talent?.id);
    if (mobile.includes('*') || email.includes('*')) return false;
    if (channels.includes('sms') && !mobile.trim() && !knownContact) return false;
    if (channels.includes('email') && !email.trim() && !knownContact) return false;
    if (isInterview && interviewMode === 'video' && !interviewTime) return false;
    return true;
  }, [channels, mobile, email, isInterview, interviewMode, interviewTime, talent]);

  const submit = async () => {
    if (!talent || !canSubmit) return;
    setSaving(true);
    try {
      const inviteType: InviteType = isAiInterview ? 'ai_interview' : kind;
      const info = await invitesApi.create({
        type: inviteType,
        talent_id: talent.id,
        talent_name: talent.name,
        mobile: mobile.trim(),
        email: email.trim(),
        channels,
        question_count: isAiInterview ? aiQuestionCount : isInterview ? undefined : (questionCount ?? undefined),
        duration_minutes: isAiInterview ? aiDuration : isInterview ? undefined : duration,
        expires_hours: isAiInterview ? aiExpiresHours : undefined,
        interview_time: isInterview && interviewMode === 'video' && interviewTime ? interviewTime.toISOString() : undefined,
        interviewer_persona: isAiInterview ? persona : undefined,
        language: isAiInterview ? 'zh-CN' : undefined,
        difficulty: isAiInterview ? 'middle' : undefined,
        title: `${talent.name} · ${
          isAiInterview ? 'AI 数字人面试' : isInterview ? '视频面试' : KIND_OPTIONS.find(o => o.value === kind)?.label ?? '邀约'
        }`,
      });
      setCreated(info);
      const failed = (info.send_results || []).filter(r => !r.ok).length;
      if (failed > 0) message.warning(`邀约已创建，${failed} 个渠道发送失败，请查看发送结果`);
      else message.success('邀约已创建并发送');
    } catch (error) {
      message.error(formatApiError(error));
    } finally {
      setSaving(false);
    }
  };

  const copyText = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => message.success('已复制到剪贴板')).catch(() => message.info('请手动复制'));
  };

  const close = () => {
    const finished = created;
    setCreated(null);
    onClose();
    if (finished) onCreated?.(finished); // 关闭后打开记录抽屉，保留链接入口
  };
  const publicUrl = created ? resolvePublicUrl(created.url) : '';
  const interviewerUrl = created ? `${publicUrl}?role=interviewer` : '';

  return (
    <Modal open={open} onCancel={close} footer={null} width={620} title={created ? '邀约已创建' : `为 ${talent?.name ?? ''} 创建邀约`} destroyOnHidden>
      {!created ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <div className="invite-form-label">邀约类型</div>
            <Segmented block options={KIND_OPTIONS} value={kind} onChange={v => setKind(v as InviteType)} />
          </div>
          {isInterview && (
            <div>
              <div className="invite-form-label">面试形式</div>
              <Segmented
                block
                options={[
                  { label: <span><RobotOutlined /> AI 数字人面试</span>, value: 'ai' },
                  { label: <span><VideoCameraOutlined /> 视频面试</span>, value: 'video' },
                ]}
                value={interviewMode}
                onChange={v => setInterviewMode(v as 'ai' | 'video')}
              />
            </div>
          )}
          <div>
            <div className="invite-form-label">发送渠道（链接经短信 / 邮箱送达）</div>
            <Checkbox.Group
              value={channels}
              onChange={v => setChannels(v as InviteChannel[])}
              options={[{ label: '手机短信', value: 'sms' }, { label: '电子邮箱', value: 'email' }]}
            />
          </div>
          <Space size={12} wrap>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="invite-form-label">手机号（短信渠道必填）</div>
              <Input
                value={mobile}
                onChange={e => setMobile(e.target.value)}
                placeholder={talent?.id ? '留空则使用人才档案中的手机号' : '候选人手机号'}
              />
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="invite-form-label">邮箱（邮件渠道必填）</div>
              <Input
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder={talent?.id ? '留空则使用人才档案中的邮箱' : '候选人邮箱'}
              />
            </div>
          </Space>
          {!isInterview ? (
            <Space size={12} wrap>
              <div>
                <div className="invite-form-label">题量（空 = 按简历解析自动出题）</div>
                <InputNumber min={1} max={100} value={questionCount} onChange={v => setQuestionCount(v)} placeholder="自动" style={{ width: 140 }} />
              </div>
              <div>
                <div className="invite-form-label">作答时长（分钟）</div>
                <InputNumber min={5} max={600} value={duration} onChange={v => setDuration(v ?? 60)} style={{ width: 140 }} />
              </div>
            </Space>
          ) : isAiInterview ? (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div>
                <div className="invite-form-label">数字人面试官</div>
                <Select
                  style={{ width: '100%' }}
                  value={persona}
                  onChange={setPersona}
                  options={AI_PERSONAS.map(item => ({ value: item.value, label: `${item.label} — ${item.desc}` }))}
                />
              </div>
              <Space size={12} wrap>
                <div>
                  <div className="invite-form-label">问题数量</div>
                  <InputNumber min={3} max={10} value={aiQuestionCount} onChange={v => setAiQuestionCount(v ?? 6)} style={{ width: 130 }} />
                </div>
                <div>
                  <div className="invite-form-label">面试时长（分钟）</div>
                  <InputNumber min={5} max={120} value={aiDuration} onChange={v => setAiDuration(v ?? 30)} style={{ width: 150 }} />
                </div>
                <div>
                  <div className="invite-form-label">链接有效期</div>
                  <Select
                    style={{ width: 140 }}
                    value={aiExpiresHours}
                    onChange={setAiExpiresHours}
                    options={[
                      { value: 24, label: '24 小时' },
                      { value: 48, label: '48 小时' },
                      { value: 72, label: '72 小时' },
                      { value: 168, label: '7 天' },
                    ]}
                  />
                </div>
              </Space>
            </Space>
          ) : (
            <div>
              <div className="invite-form-label">面试时间（候选人链接按此时段开放进场）</div>
              <DatePicker showTime style={{ width: '100%' }} value={interviewTime} onChange={setInterviewTime}
                disabledDate={d => d && d.isBefore(dayjs(), 'day')} placeholder="选择面试日期与时间" />
            </div>
          )}
          {isAiInterview ? (
            <Alert
              type="info" showIcon icon={<RobotOutlined />} message="AI 数字人面试无需预约"
              description="候选人点击链接后可在有效期内随时开始；数字人自动提问、追问并生成可复核的评估报告。"
            />
          ) : isInterview ? (
            <Alert
              type="info" showIcon icon={<VideoCameraOutlined />} message="视频面试按约定时间进场"
              description="候选人按面试时间进入面试间；创建后会同时生成面试官入口，用于面试官以 interviewer 身份进房。"
            />
          ) : (
            <Alert type="info" showIcon message="题库抽题规则" description="笔试/测评按简历解析（工作年限 + 技能标签）计算题量，从测评题库随机抽题组卷；链接默认 72 小时有效。" />
          )}
          <Button type="primary" block loading={saving} disabled={!canSubmit} onClick={submit}>创建并发送邀约</Button>
        </Space>
      ) : (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Alert type="success" showIcon message={`已创建：${created.title}`}
            description={<span>
              类型：{TYPE_LABEL[created.type] || created.type} · 状态：{created.status}
              {created.question_count ? ` · ${created.question_count} 题` : ''}
              {created.duration_minutes ? ` · ${created.duration_minutes} 分钟` : ''}
            </span>} />
          <div>
            <div className="invite-form-label">
              <LinkOutlined /> {created.type === 'ai_interview' ? '候选人 AI 面试链接（点击即进入数字人面试）' : '候选人链接（短信 / 邮件正文即此链接）'}
            </div>
            <Input.Search readOnly value={publicUrl} enterButton={<><CopyOutlined /> 复制</>} onSearch={() => copyText(publicUrl)} />
          </div>
          {created.type === 'interview' && (
            <div>
              <div className="invite-form-label">面试官入口（面试官打开后以 interviewer 身份进房）</div>
              <Input.Search readOnly value={interviewerUrl} enterButton={<><CopyOutlined /> 复制</>} onSearch={() => copyText(interviewerUrl)} />
            </div>
          )}
          <div>
            <div className="invite-form-label">发送结果</div>
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {created.send_results.map((r, i) => (
                <Typography.Text key={i} type={r.ok ? 'success' : 'danger'} delete={!r.ok}>
                  {r.channel === 'sms' ? '短信' : '邮件'}：{r.ok ? '已发送' : `失败 ${r.error ?? ''}`}
                </Typography.Text>
              ))}
            </Space>
          </div>
          <Divider style={{ margin: '4px 0' }} />
          <Button block onClick={close}>完成</Button>
        </Space>
      )}
    </Modal>
  );
}
