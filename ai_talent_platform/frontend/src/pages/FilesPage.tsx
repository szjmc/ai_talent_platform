import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Input, Select, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, DeleteOutlined, DownloadOutlined, FileImageOutlined, FilePdfOutlined, FileWordOutlined, FolderOpenOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { PageHeader } from '../components/Common';
import { stubModulesApi } from '../services/platform';
import { formatApiError } from '../services/api';
import { formatFileSize } from '../utils';

interface FileItem { id: number; file_name: string; biz_type: string; file_size: number; content_type: string; created_by?: string | null; created_at?: string | null }
const BACKEND_PENDING = '后端文件管理接口尚未实现（当前 OpenAPI 仅保留占位），无法执行该操作。';

export default function FilesPage() {
  const [data, setData] = useState<FileItem[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(''); const { message, modal } = App.useApp();
  const load = useCallback(async () => {
    setLoading(true); setLoadError('');
    try { const res = await stubModulesApi.files() as { items?: FileItem[]; total?: number }; setData(res.items || []); setTotal(res.total || (res.items || []).length); }
    catch (error) { setLoadError(formatApiError(error)); setData([]); setTotal(0); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const columns: ColumnsType<FileItem> = [
    { title: '文件名称', dataIndex: 'file_name', render: (v, r) => <div className="talent-cell"><div className="doc-icon">{(r.content_type || '').includes('image') ? <FileImageOutlined/> : (r.file_name || '').endsWith('pdf') ? <FilePdfOutlined/> : <FileWordOutlined/>}</div><div><div className="talent-cell-name">{v}</div><div className="talent-cell-role">文件 ID：{r.id} · {r.content_type}</div></div></div> },
    { title: '业务类型', dataIndex: 'biz_type', width: 120, render: (v) => <Tag color={({ resume: 'blue', knowledge: 'purple', export: 'green' } as Record<string,string>)[v]}>{({ resume: '人才简历', knowledge: '知识文档', export: '导出报告' } as Record<string,string>)[v] || v}</Tag> },
    { title: '文件大小', dataIndex: 'file_size', width: 105, render: formatFileSize },
    { title: '上传人', dataIndex: 'created_by', width: 100, render: (v) => v || '—' },
    { title: '上传时间', dataIndex: 'created_at', width: 150, render: (v) => v || '—' },
    { title: '操作', width: 110, render: (_, r) => <><Button type="text" icon={<DownloadOutlined/>} onClick={() => message.error(BACKEND_PENDING)}/><Button type="text" danger icon={<DeleteOutlined/>} onClick={() => modal.confirm({ title: '确认删除该文件？', content: BACKEND_PENDING, onOk: () => { message.error(BACKEND_PENDING); } })}/></> },
  ];
  return <><PageHeader title="文件中心" description="企业文件统一安全存储、权限访问与生命周期管理" actions={<Upload showUploadList={false} customRequest={async ({ onSuccess }) => { message.error(BACKEND_PENDING); onSuccess?.({}); }}><Button type="primary" icon={<CloudUploadOutlined/>}>上传文件</Button></Upload>}/>
    {loadError && <Alert type="warning" showIcon message="文件中心暂不可用" description={loadError} style={{ marginBottom: 16 }}/>}
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">文件总数</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">当前页大小</div><div className="metric-strip-value">{formatFileSize(data.reduce((acc, f) => acc + (f.file_size || 0), 0))}<span className="metric-strip-extra">合计</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">简历文件</div><div className="metric-strip-value">{data.filter((f) => f.biz_type === 'resume').length}<span className="metric-strip-extra">当前页</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">知识文档</div><div className="metric-strip-value">{data.filter((f) => f.biz_type === 'knowledge').length}<span className="metric-strip-extra">当前页</span></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} placeholder="搜索文件名称或文件 ID" style={{ width: 280 }}/><Select placeholder="业务类型" style={{ width: 130 }} options={[{ value: 'resume', label: '人才简历' },{ value: 'knowledge', label: '知识文档' },{ value: 'export', label: '导出报告' }]}/><div className="filter-spacer"/><span className="muted" style={{ fontSize: 11 }}><FolderOpenOutlined/> 私有对象存储 · SHA-256 完整性校验</span></div></div><div className="card content-card"><Table rowKey="id" columns={columns} dataSource={data} loading={loading} pagination={{ pageSize: 20, total }} scroll={{ x: 1100 }} locale={{ emptyText: loadError ? '后端文件接口暂不可用' : '暂无文件数据' }}/></div></>;
}
