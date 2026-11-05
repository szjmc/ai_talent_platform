import { useState } from 'react';
import { App, Button, Input, Select, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, DeleteOutlined, DownloadOutlined, FileExcelOutlined, FileImageOutlined, FilePdfOutlined, FileWordOutlined, FolderOpenOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { PageHeader } from '../components/Common';
import { useAuth } from '../store/AuthContext';
import { formatApiError, request } from '../services/api';
import { formatFileSize, sleep } from '../utils';

const initial = [
  { id: 21, file_name: '张明远-高级算法工程师.pdf', biz_type: 'resume', file_size: 842660, content_type: 'application/pdf', created_by: '陈思琪', created_at: '2026-11-04 09:26' },
  { id: 20, file_name: '职级晋升管理办法（2026版）.pdf', biz_type: 'knowledge', file_size: 2516582, content_type: 'application/pdf', created_by: '林致远', created_at: '2026-11-01 10:18' },
  { id: 19, file_name: '10月人才结构分析报告.docx', biz_type: 'export', file_size: 1826611, content_type: 'application/docx', created_by: '周雨桐', created_at: '2026-10-31 18:20' },
  { id: 18, file_name: '关键岗位继任者培养方案.docx', biz_type: 'knowledge', file_size: 3250585, content_type: 'application/docx', created_by: '陈思琪', created_at: '2026-10-28 09:06' },
  { id: 17, file_name: '候选人简历扫描件.jpg', biz_type: 'resume', file_size: 2389920, content_type: 'image/jpeg', created_by: '陈思琪', created_at: '2026-10-26 14:32' },
];
type FileItem = (typeof initial)[number];
export default function FilesPage() {
  const [data, setData] = useState(initial); const { demo } = useAuth(); const { message, modal } = App.useApp();
  const columns: ColumnsType<FileItem> = [
    { title: '文件名称', dataIndex: 'file_name', render: (v, r) => <div className="talent-cell"><div className="doc-icon">{r.content_type.includes('image') ? <FileImageOutlined/> : r.file_name.endsWith('pdf') ? <FilePdfOutlined/> : <FileWordOutlined/>}</div><div><div className="talent-cell-name">{v}</div><div className="talent-cell-role">文件 ID：{r.id} · {r.content_type}</div></div></div> },
    { title: '业务类型', dataIndex: 'biz_type', width: 120, render: (v) => <Tag color={({ resume: 'blue', knowledge: 'purple', export: 'green' } as Record<string,string>)[v]}>{({ resume: '人才简历', knowledge: '知识文档', export: '导出报告' } as Record<string,string>)[v]}</Tag> },
    { title: '文件大小', dataIndex: 'file_size', width: 105, render: formatFileSize },
    { title: '上传人', dataIndex: 'created_by', width: 100 },
    { title: '上传时间', dataIndex: 'created_at', width: 150 },
    { title: '存储状态', width: 100, render: () => <span className="status-badge success">安全存储</span> },
    { title: '操作', width: 110, render: (_, r) => <><Button type="text" icon={<DownloadOutlined/>} onClick={() => message.success('已通过权限校验，开始安全下载')}/><Button type="text" danger icon={<DeleteOutlined/>} onClick={() => modal.confirm({ title: '确认删除该文件？', content: '元数据将软删除，存储文件进入延迟清理队列。', onOk: () => { setData((old) => old.filter((x) => x.id !== r.id)); message.success('文件已删除'); } })}/></> },
  ];
  return <><PageHeader title="文件中心" description="企业文件统一安全存储、权限访问与生命周期管理" demo={demo} actions={<Upload showUploadList={false} customRequest={async ({ file, onSuccess }) => { const f = file as File; if (!demo) { const form = new FormData(); form.append('file', f); form.append('biz_type', 'export'); await request({ method: 'POST', url: '/files/upload', data: form }); } else await sleep(500); setData((old) => [{ id: Date.now(), file_name: f.name, file_size: f.size, content_type: f.type || 'application/octet-stream', biz_type: 'export', created_by: '林致远', created_at: '2026-11-04 10:30' }, ...old]); onSuccess?.({}); message.success('文件上传成功'); }}><Button type="primary" icon={<CloudUploadOutlined/>}>上传文件</Button></Upload>}/>
    <div className="metric-strip"><div className="metric-strip-item"><div className="metric-strip-label">文件总数</div><div className="metric-strip-value">18,642<span className="metric-strip-extra">个</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">存储用量</div><div className="metric-strip-value">68.2<span className="metric-strip-extra">GB / 100 GB</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">今日上传</div><div className="metric-strip-value">286<span className="metric-strip-extra">个文件</span></div></div><div className="metric-strip-item"><div className="metric-strip-label">重复文件节省</div><div className="metric-strip-value">12.6<span className="metric-strip-extra">GB</span></div></div></div>
    <div className="card filter-card"><div className="filter-row"><Input prefix={<SearchOutlined/>} placeholder="搜索文件名称或文件 ID" style={{ width: 280 }}/><Select placeholder="业务类型" style={{ width: 130 }} options={[{ value: 'resume', label: '人才简历' },{ value: 'knowledge', label: '知识文档' },{ value: 'export', label: '导出报告' }]}/><div className="filter-spacer"/><span className="muted" style={{ fontSize: 11 }}><FolderOpenOutlined/> 私有对象存储 · SHA-256 完整性校验</span></div></div><div className="card content-card"><Table rowKey="id" columns={columns} dataSource={data} pagination={{ pageSize: 20, total: 18642 }} scroll={{ x: 1100 }}/></div></>;
}
