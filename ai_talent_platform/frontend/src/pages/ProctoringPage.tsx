import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, App, Badge, Button, Col, Descriptions, Drawer, Empty, Image, Input, Row, Select, Space, Spin,
  Table, Tag, Timeline, Tooltip,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DisconnectOutlined, EyeOutlined, ReloadOutlined, SafetyCertificateOutlined, SearchOutlined,
  ThunderboltOutlined, WarningOutlined,
} from '@ant-design/icons';
import { PageHeader } from '../components/Common';
import { formatApiError, proctoringApi, type ProctorSessionReport, type ProctorSessionSummary } from '../services/api';
import { formatDate } from '../utils';
import './ai-interview.css';

/**
 * 在线监考中心（HR）：跨环节查看「切屏 / 远程控制 / 投屏 / 多显示器 / 虚拟摄像头」等作弊风险。
 *
 * 标签：[Agent组] [P1]（防作弊：风险会话列表 + 证据链复盘 + 人工复核）
 *
 * 设计要点：
 * - 列表口径与后端 assess_integrity 完全一致（risk_level / risk_advice 直接展示，前端不二次判定）；
 * - 证据链抽屉里给「事件时间线 + 可疑快照 + 命中项」，HR 不需要再翻日志；
 * - 复核动作（确认作弊 / 标记误报）落库留痕，且立即改变风险结论（确认=作废档，误报=清零）。
 */

const SUBJECT_LABEL: Record<string, string> = {
  ai_interview: 'AI 面试',
  exam: '笔试',
  interview: '视频面试',
  assessment: '测评',
};

const LEVEL_META: Record<string, { color: string; label: string }> = {
  clean: { color: 'green', label: '正常' },
  watch: { color: 'gold', label: '观察' },
  review: { color: 'orange', label: '需复核' },
  serious: { color: 'red', label: '严重嫌疑' },
  void: { color: 'magenta', label: '建议作废' },
};

const ADVICE_LABEL: Record<string, string> = {
  clean: '无需处理',
  keep: '继续观察',
  review: '人工复核',
  escalate: '追加问询 / 复测',
  void: '建议作废',
};

function levelTag(level: string) {
  const meta = LEVEL_META[level] || LEVEL_META.clean;
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

export default function ProctoringPage() {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<ProctorSessionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [level, setLevel] = useState('flagged');
  const [subject, setSubject] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');

  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<ProctorSessionReport | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const data = await proctoringApi.sessions({
        page: 1, page_size: 50, risk_level: level || undefined,
        subject_type: subject, keyword: keyword || undefined,
      });
      setRows(data.items || []); setTotal(data.total || 0);
    } catch (err) {
      setError(formatApiError(err)); setRows([]); setTotal(0);
    } finally { setLoading(false); }
  }, [level, subject, keyword]);

  useEffect(() => { void load(); }, [load]);

  const metrics = useMemo(() => ({
    total,
    serious: rows.filter((row) => row.risk_level === 'serious' || row.risk_level === 'void').length,
    blocked: rows.filter((row) => row.blocked).length,
    hardHits: rows.filter((row) => (row.hard_violations || []).length > 0).length,
  }), [rows, total]);

  const openDetail = async (row: ProctorSessionSummary) => {
    setOpen(true); setDetailLoading(true); setReport(null);
    try {
      setReport(await proctoringApi.report(row.id));
    } catch (err) {
      message.error(formatApiError(err));
    } finally { setDetailLoading(false); }
  };

  const review = (verdict: 'confirmed' | 'false_positive') => {
    if (!report) return;
    const isConfirm = verdict === 'confirmed';
    modal.confirm({
      title: isConfirm ? '确认该候选人存在作弊行为？' : '标记为误报？',
      content: isConfirm
        ? '确认后风险分将置为 100 并标记「建议作废」，该结论会同步到评分报告，请确保已核对事件时间线与快照。'
        : '标记误报后风险分清零（原始事件与快照仍保留，可追溯），适用于浏览器误报、设备切换等正常场景。',
      okText: isConfirm ? '确认作弊' : '标记误报',
      okButtonProps: { danger: isConfirm },
      onOk: async () => {
        setReviewing(true);
        try {
          const data = await proctoringApi.review(report.id, verdict, isConfirm ? 'HR 复核：确认作弊' : 'HR 复核：误报');
          setReport(data);
          message.success(isConfirm ? '已确认作弊' : '已标记为误报');
          void load();
        } catch (err) {
          message.error(formatApiError(err));
        } finally { setReviewing(false); }
      },
    });
  };

  const columns: ColumnsType<ProctorSessionSummary> = [
    {
      title: '候选人', dataIndex: 'candidate_name', width: 160,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <b>{value || '（未命名）'}</b>
          <span className="muted" style={{ fontSize: 12 }}>{row.position_title || '—'}</span>
        </Space>
      ),
    },
    {
      title: '环节', dataIndex: 'subject_type', width: 100,
      render: (value: string) => <Tag>{SUBJECT_LABEL[value] || value}</Tag>,
    },
    {
      title: '风险分', dataIndex: 'risk_score', width: 130, sorter: (a, b) => a.risk_score - b.risk_score,
      render: (value: number, row) => (
        <Space>
          <Badge status={value >= 60 ? 'error' : value >= 35 ? 'warning' : value >= 15 ? 'processing' : 'success'} />
          <b>{Number(value || 0).toFixed(1)}</b>
          {levelTag(row.risk_level)}
        </Space>
      ),
    },
    {
      title: '命中项', dataIndex: 'hard_violations', width: 220,
      render: (value: string[]) => (value?.length
        ? <Space size={4} wrap>{value.slice(0, 3).map((item) => <Tag key={item} color="red" icon={<ThunderboltOutlined />}>{item}</Tag>)}</Space>
        : <span className="muted">—</span>),
    },
    {
      title: '事件数', dataIndex: 'event_total', width: 90,
      render: (value: number) => (value > 0 ? <span>{value}</span> : <span className="muted">0</span>),
    },
    {
      title: '处置建议', dataIndex: 'risk_advice', width: 130,
      render: (value: string, row) => (row.blocked
        ? <Tag color="red" icon={<DisconnectOutlined />}>已阻断</Tag>
        : <Tag color={value === 'clean' ? 'default' : value === 'void' ? 'magenta' : 'orange'}>{ADVICE_LABEL[value] || value}</Tag>),
    },
    {
      title: '最近心跳', dataIndex: 'last_heartbeat_at', width: 170,
      render: (value: string | null) => <span className="muted">{value ? formatDate(value, true) : '—'}</span>,
    },
    {
      title: '操作', key: 'action', width: 110, fixed: 'right',
      render: (_, row) => <Button size="small" icon={<EyeOutlined />} onClick={() => void openDetail(row)}>证据链</Button>,
    },
  ];

  return (
    <div className="page-content">
      <PageHeader
        title="在线监考中心"
        description="汇总 AI 面试 / 笔试 / 视频面试的行为监考记录：切屏、失焦、退出全屏、多显示器、外接投影、远程控制、虚拟摄像头与断流。风险分只作复核依据，不直接扣分。"
        actions={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
      />

      <Row gutter={16} style={{ marginBottom: 16 }}>
        {[
          { label: '待处理会话', value: metrics.total, icon: <SafetyCertificateOutlined />, color: '#315efb' },
          { label: '严重 / 建议作废', value: metrics.serious, icon: <WarningOutlined />, color: '#df5252' },
          { label: '已阻断作答', value: metrics.blocked, icon: <DisconnectOutlined />, color: '#d46b08' },
          { label: '命中硬性风险', value: metrics.hardHits, icon: <ThunderboltOutlined />, color: '#7c3aed' },
        ].map((item) => (
          <Col xs={12} md={6} key={item.label}>
            <div className="card" style={{ padding: '16px 18px' }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>{item.icon} {item.label}</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: item.color }}>{item.value}</div>
            </div>
          </Col>
        ))}
      </Row>

      <div className="card" style={{ padding: 18 }}>
        <Space wrap style={{ marginBottom: 14 }}>
          <Select
            value={level} style={{ width: 150 }} onChange={setLevel}
            options={[
              { value: 'flagged', label: '只看需处理' },
              { value: '', label: '全部等级' },
              ...Object.entries(LEVEL_META).map(([value, meta]) => ({ value, label: meta.label })),
            ]}
          />
          <Select
            allowClear placeholder="全部环节" style={{ width: 150 }} value={subject} onChange={setSubject}
            options={Object.entries(SUBJECT_LABEL).map(([value, label]) => ({ value, label }))}
          />
          <Input.Search
            allowClear placeholder="按候选人 / 岗位 / token 搜索" style={{ width: 300 }}
            enterButton={<SearchOutlined />} value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => setKeyword(value)}
          />
        </Space>

        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}

        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          columns={columns}
          pagination={{ total, pageSize: 50, showSizeChanger: false }}
          scroll={{ x: 1080 }}
          locale={{ emptyText: <Empty description="暂无监考记录（候选人开始作答并上报心跳后出现）" /> }}
        />
      </div>

      <Drawer
        open={open} width={760} onClose={() => setOpen(false)} destroyOnClose
        title="监考证据链"
        extra={report && (
          <Space>
            <Button size="small" danger loading={reviewing} onClick={() => review('confirmed')}>确认作弊</Button>
            <Button size="small" loading={reviewing} onClick={() => review('false_positive')}>标记误报</Button>
          </Space>
        )}
      >
        {detailLoading && <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>}
        {!detailLoading && report && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type={['serious', 'void'].includes(report.risk_level) ? 'error' : report.risk_level === 'clean' ? 'success' : 'warning'}
              showIcon
              message={`风险 ${report.risk_score} 分 · ${LEVEL_META[report.risk_level]?.label || report.risk_level}`}
              description={report.message || '未发现异常行为'}
            />

            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="候选人">{report.candidate_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="环节">{SUBJECT_LABEL[report.subject_type] || report.subject_type}</Descriptions.Item>
              <Descriptions.Item label="岗位">{report.position_title || '—'}</Descriptions.Item>
              <Descriptions.Item label="处置建议">{ADVICE_LABEL[report.risk_advice] || report.risk_advice}</Descriptions.Item>
              <Descriptions.Item label="屏幕共享">{report.screen_shared ? '已共享' : report.require_screen_share ? '未共享（要求共享）' : '未共享'}</Descriptions.Item>
              <Descriptions.Item label="显示器数量">{report.display_count}</Descriptions.Item>
              <Descriptions.Item label="远程控制">{report.remote_control ? <Tag color="red">检测到</Tag> : '未检测到'}</Descriptions.Item>
              <Descriptions.Item label="心跳">{report.heartbeat_lost ? <Tag color="orange">曾中断</Tag> : '正常'}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{report.started_at ? formatDate(report.started_at, true) : '—'}</Descriptions.Item>
              <Descriptions.Item label="累计事件">{report.event_total}</Descriptions.Item>
            </Descriptions>

            <div>
              <div className="aii-section-title">事件分布</div>
              <Space size={6} wrap>
                {Object.entries(report.event_counts || {}).length === 0 && <span className="muted">暂无事件</span>}
                {Object.entries(report.event_counts || {}).map(([type, count]) => (
                  <Tag key={type} color={['remote_control', 'casting', 'impersonation'].includes(type) ? 'red' : 'default'}>
                    {report.event_labels?.[type] || type} × {count}
                  </Tag>
                ))}
              </Space>
            </div>

            <div>
              <div className="aii-section-title">可疑快照（{report.snapshots?.length || 0}）</div>
              {report.snapshots?.length ? (
                <Space wrap>
                  {report.snapshots.map((snap) => (
                    <div key={snap.id} style={{ textAlign: 'center' }}>
                      <Image src={snap.url} width={160} height={96} style={{ objectFit: 'cover', borderRadius: 8 }}
                        fallback="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" />
                      <div className="muted" style={{ fontSize: 11 }}>
                        {snap.source === 'screen' ? '屏幕' : '摄像头'} · {snap.trigger_type || '手动'} · {snap.captured_at ? formatDate(snap.captured_at, true) : ''}
                      </div>
                    </div>
                  ))}
                </Space>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无可疑快照（隐私最小化：仅命中风险时抽样留存）" />}
            </div>

            <div>
              <div className="aii-section-title">事件时间线</div>
              {report.timeline?.length ? (
                <Timeline
                  items={report.timeline.slice(-40).map((item, index) => ({
                    key: `${item.type}-${index}`,
                    color: ['remote_control', 'casting', 'impersonation', 'face_mismatch'].includes(item.type) ? 'red'
                      : ['tab_hidden', 'fullscreen_exit', 'multi_display'].includes(item.type) ? 'orange' : 'blue',
                    children: (
                      <div>
                        <Space size={6} wrap>
                          <b>{item.label || item.type}</b>
                          <Tag>{item.source === 'server' ? '服务端判定' : '客户端上报'}</Tag>
                          {item.score != null && <span className="muted">+{Number(item.score).toFixed(1)} 分</span>}
                          <span className="muted">{item.ts ? formatDate(new Date(item.ts * 1000).toISOString(), true) : ''}</span>
                        </Space>
                        {item.detail && <div className="muted" style={{ fontSize: 12 }}>{item.detail}</div>}
                      </div>
                    ),
                  }))}
                />
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无事件" />}
            </div>

            <Tooltip title="风险分仅用于人工复核参考，不自动扣减面试/笔试卷面分">
              <Alert type="info" showIcon message="复核建议" description={[
                '远程控制 / 投屏 / 疑似代考属硬性风险，建议直接联系候选人核实并安排复测；',
                '单次切屏多为误触（如输入法弹窗、通知），多次切屏且伴随粘贴长文本需重点核查；',
                '多显示器与屏幕共享中断若因设备故障导致，请标记为误报并记录原因。',
              ].map((text) => <div key={text}>· {text}</div>)} />
            </Tooltip>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
