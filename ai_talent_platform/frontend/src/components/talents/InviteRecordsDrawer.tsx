import { useCallback, useEffect, useState } from 'react';
import { Button, Drawer, Empty, Input, List, Pagination, Segmented, Space, Tag, Typography, message } from 'antd';
import { CopyOutlined, ExportOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { copyToClipboard } from '../../utils/clipboard';
import { interviewerInviteUrl } from '../../utils/media';
import { formatApiError, invitesApi, type InviteInfo, type InviteType } from '../../services/api';

const TYPE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '笔试', value: 'written_test' },
  { label: '测评', value: 'assessment' },
  { label: '面试', value: 'interview' },
];

const TYPE_TAG: Record<string, { label: string; color: string }> = {
  written_test: { label: '笔试', color: 'blue' },
  assessment: { label: '测评', color: 'purple' },
  interview: { label: '面试', color: 'gold' },
};

const STATUS_TAG: Record<string, { label: string; color: string }> = {
  created: { label: '已创建', color: 'default' },
  sent: { label: '已发送', color: 'processing' },
  opened: { label: '候选人已打开', color: 'warning' },
  submitted: { label: '已交卷', color: 'success' },
  finished: { label: '面试完成', color: 'success' },
  expired: { label: '已过期', color: 'default' },
  cancelled: { label: '已取消', color: 'error' },
};

interface Props {
  open: boolean;
  /** 打开时预填的关键字（如人才姓名，用于按标题检索） */
  initialKeyword?: string;
  onClose: () => void;
}

/** 人才邀约记录抽屉：类型/关键字过滤 + 分页列表，含链接复制与打开（防止创建后链接丢失）。 */
export default function InviteRecordsDrawer({ open, initialKeyword = '', onClose }: Props) {
  const [type, setType] = useState('');
  const [keyword, setKeyword] = useState(initialKeyword);
  const [appliedKeyword, setAppliedKeyword] = useState(initialKeyword);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<InviteInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await invitesApi.list({
        type: (type || undefined) as InviteType | undefined,
        keyword: appliedKeyword || undefined,
        page,
        page_size: 10,
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (error) {
      message.error(formatApiError(error));
    } finally {
      setLoading(false);
    }
  }, [type, appliedKeyword, page]);

  // 打开时重置筛选为当前人才
  useEffect(() => {
    if (!open) return;
    setKeyword(initialKeyword);
    setAppliedKeyword(initialKeyword);
    setPage(1);
    setType('');
  }, [open, initialKeyword]);

  useEffect(() => { if (open) void load(); }, [open, load]);

  const copy = async (text: string) => {
    if (await copyToClipboard(text)) message.success('已复制链接');
    else message.warning('复制失败，请选中链接手动复制');
  };

  const applySearch = () => { setAppliedKeyword(keyword.trim()); setPage(1); };

  return (
    <Drawer open={open} onClose={onClose} width={580} title="人才邀约记录" destroyOnHidden>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space size={8} wrap>
          <Segmented
            options={TYPE_OPTIONS}
            value={type}
            onChange={v => { setType(v as string); setPage(1); }}
          />
          <Input.Search
            allowClear
            size="small"
            style={{ width: 200 }}
            placeholder="人才姓名 / 标题"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={applySearch}
            enterButton={<span>搜索</span>}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </Space>

        <List
          loading={loading}
          dataSource={items}
          locale={{ emptyText: <Empty description="暂无邀约记录（从档案卡「邀约」创建）" /> }}
          renderItem={item => {
            const typeTag = TYPE_TAG[item.type] ?? { label: item.type, color: 'default' };
            const statusTag = STATUS_TAG[item.status] ?? { label: item.status, color: 'default' };
            return (
              <List.Item key={item.id}>
                <div style={{ width: '100%' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                    <Space size={6} wrap>
                      <Typography.Text strong>{item.title || item.talent_name || '邀约'}</Typography.Text>
                      <Tag color={typeTag.color}>{typeTag.label}</Tag>
                      <Tag color={statusTag.color}>{statusTag.label}</Tag>
                    </Space>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {item.created_at ? dayjs(item.created_at).format('MM-DD HH:mm') : ''}
                    </Typography.Text>
                  </div>

                  <div style={{ margin: '6px 0', fontSize: 12, color: '#617982' }}>
                    {item.type === 'interview'
                      ? <>面试时间：{item.interview_time ? dayjs(item.interview_time).format('YYYY-MM-DD HH:mm') : '未设置'} · </>
                      : <>{item.question_count ?? '自动'} 题 · {item.duration_minutes} 分钟 · </>}
                    有效期至 {item.expires_at ? dayjs(item.expires_at).format('MM-DD HH:mm') : '—'}
                  </div>

                  <div style={{ fontSize: 12, marginBottom: 8 }}>
                    {item.send_results.length === 0
                      ? <Typography.Text type="secondary">未选发送渠道</Typography.Text>
                      : item.send_results.map((r, i) => (
                        <Tag key={i} color={r.ok ? 'green' : 'red'} style={{ marginInlineEnd: 4 }}>
                          {r.channel === 'sms' ? '短信' : '邮件'}：{r.ok ? '已发送' : `失败${r.error ? `（${r.error}）` : ''}`}
                        </Tag>
                      ))}
                  </div>

                  <Space.Compact style={{ width: '100%' }}>
                    <Input readOnly value={item.url} size="small" style={{ fontFamily: 'monospace', fontSize: 12 }} />
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copy(item.url)}>复制</Button>
                    <Button size="small" icon={<ExportOutlined />} href={item.url} target="_blank">打开</Button>
                  </Space.Compact>
                  {item.type === 'interview' && <Space.Compact style={{ width: '100%', marginTop: 6 }}>
                    <Input aria-label="面试官链接" readOnly value={interviewerInviteUrl(item.url)} size="small" style={{ fontFamily: 'monospace', fontSize: 12 }} />
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copy(interviewerInviteUrl(item.url))}>复制面试官链接</Button>
                  </Space.Compact>}
                </div>
              </List.Item>
            );
          }}
        />

        {total > 10 && (
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Pagination
              size="small"
              current={page}
              pageSize={10}
              total={total}
              onChange={p => setPage(p)}
              showTotal={t => `共 ${t} 条`}
            />
          </div>
        )}
      </Space>
    </Drawer>
  );
}
