import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Input, Pagination, Select, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, DeleteOutlined, DownloadOutlined, FileImageOutlined, FilePdfOutlined, FileWordOutlined, FolderOpenOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile } from 'antd/es/upload/interface';
import { PageHeader } from '../components/Common';
import { auditApi, filesApi, type FileStats } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatFileSize } from '../utils';
import type { FileItem } from '../types';

const BIZ_LABEL: Record<string, string> = { resume: '人才简历', knowledge: '知识文档', avatar: '头像', export: '导出报告', interview: '面试材料', other: '其他' };
const BIZ_COLOR: Record<string, string> = { resume: 'blue', knowledge: 'purple', avatar: 'cyan', export: 'green', interview: 'gold', other: 'default' };

function fileIcon(item: FileItem) {
  const type = item.content_type || '';
  if (type.startsWith('image/')) return <FileImageOutlined />;
  if ((item.extension || '').toLowerCase() === '.pdf') return <FilePdfOutlined />;
  return <FileWordOutlined />;
}

export default function FilesPage() {
  const [data, setData] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [bizType, setBizType] = useState<string | undefined>();
  const [stats, setStats] = useState<FileStats | null>(null);
  const { message, modal } = App.useApp();

  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try {
      const res = await filesApi.list({ page, page_size: pageSize, keyword: keyword || undefined, biz_type: bizType });
      setData(res.items || []); setTotal(res.total || 0);
    } catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, [page, pageSize, keyword, bizType]);

  const loadStats = useCallback(async () => {
    try { setStats(await filesApi.stats()); } catch { setStats(null); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadStats(); }, [loadStats]);

  const refresh = async () => { await Promise.all([load(), loadStats()]); };

  const upload = async (file: File) => {
    try {
      const saved = await filesApi.upload(file, { biz_type: 'other' });
      message.success(saved.duplicated ? '已存在相同文件，直接复用已有记录' : '文件上传成功');
      await refresh();
    } catch (error) { message.error(formatApiError(error)); }
    return false; // 阻止 antd 默认上传行为
  };

  const download = async (record: FileItem) => {
    try { await filesApi.download(record.id, record.file_name); }
    catch (error) { message.error(formatApiError(error)); }
  };

  const remove = (record: FileItem) => modal.confirm({
    title: '确认删除该文件？',
    content: `「${record.file_name}」将被软删除，物理对象由后台定时清理；删除后不可恢复。`,
    okButtonProps: { danger: true },
    onOk: async () => {
      try { await filesApi.remove(record.id); message.success('文件已删除'); await refresh(); }
      catch (error) { message.error(formatApiError(error)); }
    },
  });

  const traceLog = async (record: FileItem) => {
    try {
      const res = await auditApi.logs({ page: 1, page_size: 10, keyword: record.file_name });
      modal.info({
        title: `「${record.file_name}」相关审计记录`,
        width: 620,
        content: res.items.length
          ? <div style={{ maxHeight: 320, overflow: 'auto' }}>{res.items.map((log) => (
              <div key={log.id} style={{ padding: '6px 0', borderBottom: '1px solid #f0f0f0', fontSize: 12 }}>
                <b>{log.username || '系统'}</b> · {log.action} · {log.created_at || '—'}
                <div style={{ color: '#8c8c8c' }}>{log.request_method} {log.request_path} · {log.status}</div>
              </div>))}</div>
          : <span>暂无相关审计记录（仅管理员可查）。</span>,
      });
    } catch (error) { message.warning(formatApiError(error)); }
  };

  const columns: ColumnsType<FileItem> = useMemo(() => [
    { title: '文件名称', dataIndex: 'file_name', render: (v, r) => <div className="talent-cell"><div className="doc-icon">{fileIcon(r)}</div><div><div className="talent-cell-name">{v}</div><div className="talent-cell-role">文件 ID：{r.id} · {r.content_type || '未知类型'}{r.is_public ? ' · 公开' : ''}</div></div></div> },
    { title: '业务类型', dataIndex: 'biz_type', width: 120, render: (v) => <Tag color={BIZ_COLOR[v]}>{BIZ_LABEL[v] || v}</Tag> },
    { title: '文件大小', dataIndex: 'file_size', width: 105, render: formatFileSize },
    { title: '上传人', dataIndex: 'uploader_name', width: 110, render: (v) => v || '—' },
    { title: '下载次数', dataIndex: 'download_count', width: 95, render: (v) => v ?? 0 },
    { title: '上传时间', dataIndex: 'created_at', width: 160, render: (v) => v ? String(v).replace('T', ' ').slice(0, 19) : '—' },
    { title: '操作', width: 150, fixed: 'right', render: (_, r) => <>
      <Button type="text" icon={<FolderOpenOutlined />} title="查看审计链路" onClick={() => void traceLog(r)} />
      <Button type="text" icon={<DownloadOutlined />} title="下载" onClick={() => void download(r)} />
      <Button type="text" danger icon={<DeleteOutlined />} title="删除" onClick={() => remove(r)} />
    </> },
  ], [data, total]); // eslint-disable-line react-hooks/exhaustive-deps

  const pageSizeTotal = data.reduce((acc, f) => acc + (f.file_size || 0), 0);

  return <>
    <PageHeader title="文件中心" description="企业文件统一安全存储、权限访问与生命周期管理"
      actions={<Upload showUploadList={false} beforeUpload={(file: UploadFile & File) => { void upload(file as unknown as File); return false; }}>
        <Button type="primary" icon={<CloudUploadOutlined />}>上传文件</Button>
      </Upload>} />
    {loadError && <Alert type="warning" showIcon message="文件中心暂不可用" description={loadError} style={{ marginBottom: 16 }} />}
    <div className="metric-strip">
      <div className="metric-strip-item"><div className="metric-strip-label">文件总数</div><div className="metric-strip-value">{stats?.total_files ?? total}<span className="metric-strip-extra">个</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">总占用</div><div className="metric-strip-value">{formatFileSize(stats?.total_size || 0)}<span className="metric-strip-extra">合计</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">当前页大小</div><div className="metric-strip-value">{formatFileSize(pageSizeTotal)}<span className="metric-strip-extra">本页</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">知识文档</div><div className="metric-strip-value">{stats?.by_biz_type?.knowledge ?? data.filter((f) => f.biz_type === 'knowledge').length}<span className="metric-strip-extra">个</span></div></div>
    </div>
    <div className="card filter-card"><div className="filter-row">
      <Input prefix={<SearchOutlined />} placeholder="搜索文件名称或文件 ID" style={{ width: 280 }} value={keyword} allowClear
        onChange={(e) => { setKeyword(e.target.value); setPage(1); }} onPressEnter={() => void load()} />
      <Select placeholder="业务类型" style={{ width: 140 }} allowClear value={bizType}
        onChange={(v) => { setBizType(v); setPage(1); }}
        options={Object.entries(BIZ_LABEL).map(([value, label]) => ({ value, label }))} />
      <Button type="primary" ghost onClick={() => void load()}>查询</Button>
      <Button onClick={() => { setKeyword(''); setBizType(undefined); setPage(1); }}>重置</Button>
      <div className="filter-spacer" />
      <span className="muted" style={{ fontSize: 11 }}><FolderOpenOutlined /> 私有对象存储 · SHA-256 秒传与完整性校验</span>
    </div></div>
    <div className="card content-card">
      <div className="table-toolbar"><div className="table-title">文件列表<span className="table-count">共 {total} 个</span></div></div>
      <Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={false} scroll={{ x: 1150 }}
        locale={{ emptyText: loadError ? '后端文件接口暂不可用' : '暂无文件数据' }} />
      <div className="table-footer"><Pagination current={page} pageSize={pageSize} total={total} showSizeChanger
        onChange={(p, size) => { setPage(p); setPageSize(size); }} /></div>
    </div>
  </>;
}
