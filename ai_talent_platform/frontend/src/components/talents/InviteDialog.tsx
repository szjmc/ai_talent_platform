import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Checkbox, DatePicker, Divider, Input, InputNumber, Modal, Segmented, Space, Typography, message } from 'antd';
import { CopyOutlined, LinkOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import type { Talent } from '../../types';
import { invitesApi, formatApiError, type InviteChannel, type InviteInfo, type InviteType } from '../../services/api';

interface Props {
  talent: Talent | null;
  initialKind?: InviteType;
  open: boolean;
  onClose: () => void;
}

const KIND_OPTIONS = [
  { label: '笔试邀约', value: 'written_test' },
  { label: '测评邀约', value: 'assessment' },
  { label: '面试邀约', value: 'interview' },
];

/** 邀约创建对话框：题量按简历解析（可覆盖）/ 面试设时间 -> 创建并按渠道发送链接。 */
export default function InviteDialog({ talent, initialKind = 'written_test', open, onClose }: Props) {
  const [kind, setKind] = useState<InviteType>(initialKind);
  const [channels, setChannels] = useState<InviteChannel[]>(['sms']);
  const [mobile, setMobile] = useState('');
  const [email, setEmail] = useState('');
  const [questionCount, setQuestionCount] = useState<number | null>(null);
  const [duration, setDuration] = useState(60);
  const [interviewTime, setInterviewTime] = useState<Dayjs | null>(null);
  const [saving, setSaving] = useState(false);
  const [created, setCreated] = useState<InviteInfo | null>(null);

  useEffect(() => {
    if (!open) return;
    setKind(initialKind);
    setCreated(null);
    setMobile(talent?.mobile || '');
    setEmail(talent?.email || '');
    setQuestionCount(null);
    setDuration(60);
    setInterviewTime(initialKind === 'interview' ? dayjs().add(1, 'day').hour(10).minute(0) : null);
    setChannels(['sms']);
  }, [open, initialKind, talent]);

  const isInterview = kind === 'interview';
  const canSubmit = useMemo(() => {
    if (channels.length === 0) return false;
    if (channels.includes('sms') && !mobile.trim()) return false;
    if (channels.includes('email') && !email.trim()) return false;
    if (isInterview && !interviewTime) return false;
    return true;
  }, [channels, mobile, email, isInterview, interviewTime]);

  const submit = async () => {
    if (!talent || !canSubmit) return;
    setSaving(true);
    try {
      const info = await invitesApi.create({
        type: kind,
        // 演示卡片人才不落库档案 -> 仅传快照（name/mobile/email），不传 talent_id
        talent_name: talent.name,
        mobile: mobile.trim(),
        email: email.trim(),
        channels,
        question_count: isInterview ? undefined : (questionCount ?? undefined),
        duration_minutes: isInterview ? undefined : duration,
        interview_time: isInterview && interviewTime ? interviewTime.toISOString() : undefined,
        title: `${talent.name} · ${KIND_OPTIONS.find(o => o.value === kind)?.label ?? '邀约'}`,
      });
      setCreated(info);
      const mock = info.send_results.filter(r => r.mode === 'mock' && r.ok).length;
      message.success(mock > 0 ? '邀约已创建（演示环境为模拟发送）' : '邀约已创建并发送');
    } catch (error) {
      message.error(formatApiError(error));
    } finally {
      setSaving(false);
    }
  };

  const copyText = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => message.success('已复制到剪贴板')).catch(() => message.info('请手动复制'));
  };

  const close = () => { setCreated(null); onClose(); };
  const interviewerUrl = created ? `${created.url}?role=interviewer` : '';

  return (
    <Modal open={open} onCancel={close} footer={null} width={560} title={created ? '邀约已创建' : `为 ${talent?.name ?? ''} 创建邀约`} destroyOnHidden>
      {!created ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <div className="invite-form-label">邀约类型</div>
            <Segmented block options={KIND_OPTIONS} value={kind} onChange={v => setKind(v as InviteType)} />
          </div>
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
              <Input value={mobile} onChange={e => setMobile(e.target.value)} placeholder="候选人手机号" />
            </div>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div className="invite-form-label">邮箱（邮件渠道必填）</div>
              <Input value={email} onChange={e => setEmail(e.target.value)} placeholder="候选人邮箱" />
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
          ) : (
            <div>
              <div className="invite-form-label">面试时间（候选人链接按此时段开放进场）</div>
              <DatePicker showTime style={{ width: '100%' }} value={interviewTime} onChange={setInterviewTime}
                disabledDate={d => d && d.isBefore(dayjs(), 'day')} placeholder="选择面试日期与时间" />
            </div>
          )}
          <Alert type="info" showIcon message="题库抽题规则" description="笔试/测评按简历解析（工作年限 + 技能标签）计算题量，从测评题库随机抽题组卷；链接默认 72 小时有效。" />
          <Button type="primary" block loading={saving} disabled={!canSubmit} onClick={submit}>创建并发送邀约</Button>
        </Space>
      ) : (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Alert type="success" showIcon message={`已创建：${created.title}`}
            description={`类型：${KIND_OPTIONS.find(o => o.value === created.type)?.label} · 状态：${created.status}${created.question_count ? ` · ${created.question_count} 题` : ''}`} />
          <div>
            <div className="invite-form-label"><LinkOutlined /> 候选人链接（短信 / 邮件正文即此链接）</div>
            <Input.Search readOnly value={created.url} enterButton={<><CopyOutlined /> 复制</>} onSearch={() => copyText(created.url)} />
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
                  {r.channel === 'sms' ? '短信' : '邮件'}：{r.ok ? (r.mode === 'mock' ? '模拟发送（演示环境未真实下发）' : '已发送') : `失败 ${r.error ?? ''}`}
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
