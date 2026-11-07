import { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Empty, Input, Modal, Select, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, DeleteOutlined, DownloadOutlined, FileTextOutlined, HistoryOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { PageHeader, StatusBadge } from '../components/Common';
import { filesApi, knowledgeApi, type RevisionItem } from '../services/platform';
import { formatApiError } from '../services/api';
import type { KnowledgeDoc } from '../types';

/**
 * 文件管理。
 *
 * 定位（与后端分组一致）：
 *   「文件管理」管的是**文件本身**——上传、下载版本文件/原件、版本流转（编辑/发布/恢复/取消）、
 *   题库文件导入、索引删除。检索与切片（/knowledge/retrieve、/revisions/{id}/preview）
 *   仍归「知识库」，不在本页。
 *
 * 因此本页没有「所有文件列表」：后端 /files 不提供文件元数据分页查询
 * （files 表是上传时落库的元数据，按文档/版本维度查看才有业务含义，
 *  入口是「文档 → 版本列表」）。列表以文档为单位，展开看版本文件，
 *  这是文件能力真正被使用的路径。
 */

const CATEGORIES = [
  { value: 'resume', label: '简历' },
  { value: 'job_description', label: '岗位说明' },
  { value: 'assessment', label: '测评' },
  { value: 'policy', label: '制度' },
  { value: 'training', label: '培训' },
  { value: 'case', label: '案例' },
  { value: 'development_plan', label: '发展计划' },
];
const CATEGORY_LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.value, c.label]));

export default function FilesPage() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [keyword, setKeyword] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadCategory, setUploadCategory] = useState('policy');
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selected, setSelected] = useState<KnowledgeDoc | null>(null);
  const [revisions, setRevisions] = useState<RevisionItem[]>([]);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const { message, modal } = App.useApp();

  const load = useCallback(async (search?: string) => {
    setLoading(true); setLoadError('');
    try {
      const res = await knowledgeApi.docs({ page: 1, page_size: 50, keyword: search || undefined });
      setDocs((res.items || []).map((d) => ({ ...d, id: d.id ?? d.doc_id ?? 0 })));
      setTotal(res.total || 0);
    } catch (error) { setLoadError(formatApiError(error)); setDocs([]); setTotal(0); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const loadRevisions = useCallback(async (doc: KnowledgeDoc) => {
    setSelected(doc); setRevisionLoading(true);
    try {
      const res = await knowledgeApi.revisions(doc.id);
      setRevisions(res.items || []);
    } catch (error) { message.error(formatApiError(error)); setRevisions([]); }
    finally { setRevisionLoading(false); }
  }, [message]);

  const upload = async () => {
    if (!uploadFiles.length) { message.warning('请选择要上传的文件'); return; }
    setUploading(true);
    try {
      const res = await filesApi.upload(uploadFiles, uploadCategory);
      const batch = res as { failed_count?: number; total?: number };
      if (batch && typeof batch.total === 'number') {
        message.success(`批量上传完成：成功 ${(batch.total || 0) - (batch.failed_count || 0)} 份，失败 ${batch.failed_count || 0} 份，请确认每份文件的处理结果`);
      } else {
        message.success('文件已上传，请检查正文与切片预览后确认发布');
      }
      setUploadOpen(false); setUploadFiles([]); await load(keyword);
    } catch (error) { message.error(formatApiError(error)); }
    finally { setUploading(false); }
  };

  const retry = async (doc: KnowledgeDoc) => {
    try { await filesApi.retry(doc.id); message.success('已触发重建索引'); await load(keyword); }
    catch (error) { message.error(formatApiError(error)); }
  };

  const download = async (revisionId: number, name: string, source: boolean) => {
    try { await filesApi.downloadRevision(revisionId, name, source); }
    catch (error) { message.error(formatApiError(error)); }
  };

  const publish = (rev: RevisionItem) => modal.confirm({
    title: '确认发布该版本？',
    content: `发布后该版本成为可检索内容，旧版本向量由补偿任务延迟清理。版本号：${rev.version}`,
    onOk: async () => {
      try { await filesApi.publishRevision(rev.id ?? rev.revision_id ?? 0, rev.version); message.success('已发布'); await loadRevisions(selected!); await load(keyword); }
      catch (error) { message.error(formatApiError(error)); }
    },
  });

  const discard = (rev: RevisionItem) => modal.confirm({
    title: '取消该草稿？',
    content: '归档未发布草稿，保留版本文件与来源关系，不影响当前已发布版本。',
    okButtonProps: { danger: true },
    onOk: async () => {
      try { await filesApi.discardRevision(rev.id ?? rev.revision_id ?? 0, rev.version); message.success('草稿已取消'); await loadRevisions(selected!); }
      catch (error) { message.error(formatApiError(error)); }
    },
  });

  const restore = (rev: RevisionItem) => modal.confirm({
    title: '从该历史版本恢复为新草稿？',
    content: '会生成一个新的草稿版本，需重新检查切片并确认发布，不会直接切换检索内容。',
    onOk: async () => {
      try { await filesApi.restoreRevision(rev.id ?? rev.revision_id ?? 0, rev.version); message.success('已恢复为新草稿'); await loadRevisions(selected!); }
      catch (error) { message.error(formatApiError(error)); }
    },
  });

  const importQuestions = async (file: File) => {
    try {
      const res = await filesApi.uploadQuestions(file);
      message.success(`题库导入完成：${res.imported_count ?? 0} 道，失败 ${res.failed_count ?? 0} 道` + (res.sync_error ? '（索引同步异常，请查看结果）' : ''));
    } catch (error) { message.error(formatApiError(error)); }
    return false;
  };

  const columns: ColumnsType<KnowledgeDoc> = [
    { title: '文档 / 文件', dataIndex: 'title', render: (v, r) => (
      <div className="talent-cell"><div className="doc-icon"><FileTextOutlined /></div>
        <div><div className="talent-cell-name">{v}</div>
          <div className="talent-cell-role">文档 ID：{r.id}{r.file_id ? ` · 文件 ID：${r.file_id}` : ''} · {r.chunk_count} 个切片</div>
        </div></div>) },
    { title: '分类', dataIndex: 'category', width: 110, render: (v) => <Tag>{CATEGORY_LABEL[v] || v}</Tag> },
    { title: '索引状态', dataIndex: 'status', width: 100, render: (v) => <StatusBadge status={v} /> },
    { title: '操作', width: 210, fixed: 'right', render: (_, r) => <>
      <Button type="text" icon={<HistoryOutlined />} title="查看版本文件" onClick={() => void loadRevisions(r)} />
      {r.status === 'failed' && <Button type="text" icon={<ReloadOutlined />} title="重建索引" onClick={() => void retry(r)} />}
      <Button type="text" danger icon={<DeleteOutlined />} title="删除文档索引"
        onClick={() => modal.confirm({
          title: '删除该文档索引？',
          content: '仅逻辑删除文档索引并由补偿任务清理向量，不会物理删除原始文件。',
          okButtonProps: { danger: true },
          onOk: async () => {
            try { await filesApi.deleteIndex(r.file_id || r.id, r.category); message.success('删除已受理'); await load(keyword); }
            catch (error) { message.error(formatApiError(error)); }
          },
        })} />
    </> },
  ];

  const revisionColumns: ColumnsType<RevisionItem> = [
    { title: '版本', dataIndex: 'version', width: 78, render: (v) => <b>v{v}</b> },
    { title: '状态', dataIndex: 'status', width: 100, render: (v) => <StatusBadge status={v} /> },
    { title: '文件 ID', dataIndex: 'file_id', width: 90, render: (v) => v ?? '—' },
    { title: '创建时间', dataIndex: 'created_at', width: 158, render: (v) => v ? String(v).replace('T', ' ').slice(0, 19) : '—' },
    { title: '操作', width: 200, render: (_, r) => {
      const id = r.id ?? r.revision_id ?? 0;
      return <>
        <Button type="text" icon={<DownloadOutlined />} title="下载版本文件" onClick={() => void download(id, `${selected?.title || 'document'}-v${r.version}.docx`, false)} />
        <Button type="text" icon={<FileTextOutlined />} title="下载来源原件" onClick={() => void download(id, `${selected?.title || 'document'}-原件`, true)} />
        {r.status === 'draft' && <>
          <Button type="link" size="small" onClick={() => publish(r)}>发布</Button>
          <Button type="link" size="small" danger onClick={() => discard(r)}>取消</Button>
        </>}
        {r.status !== 'draft' && <Button type="link" size="small" onClick={() => restore(r)}>恢复</Button>}
      </>;
    } },
  ];

  return <>
    <PageHeader
      title="文件管理"
      description="文件上传、版本文件与索引维护（检索与切片预览在「知识库」）"
      actions={<>
        <Upload showUploadList={false} beforeUpload={(file) => { void importQuestions(file as unknown as File); return false; }}>
          <Button icon={<FileTextOutlined />}>导入题库文件</Button>
        </Upload>
        <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setUploadOpen(true)}>上传文件</Button>
      </>}
    />
    {loadError && <Alert type="warning" showIcon message="文件接口暂不可用" description={loadError} style={{ marginBottom: 16 }} />}

    <div className="metric-strip">
      <div className="metric-strip-item"><div className="metric-strip-label">文档 / 文件</div><div className="metric-strip-value">{total}<span className="metric-strip-extra">份</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">当前页切片</div><div className="metric-strip-value">{docs.reduce((acc, d) => acc + (d.chunk_count || 0), 0)}<span className="metric-strip-extra">片</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">索引失败</div><div className="metric-strip-value">{docs.filter((d) => d.status === 'failed').length}<span className="metric-strip-extra">当前页</span></div></div>
      <div className="metric-strip-item"><div className="metric-strip-label">已就绪</div><div className="metric-strip-value">{docs.filter((d) => d.status === 'published' || d.status === 'indexed').length}<span className="metric-strip-extra">当前页</span></div></div>
    </div>

    <div className="card filter-card"><div className="filter-row">
      <Input prefix={<SearchOutlined />} placeholder="搜索文件名 / 文档标题" style={{ width: 280 }} value={keyword} allowClear
        onChange={(e) => setKeyword(e.target.value)} onPressEnter={() => void load(keyword)} />
      <Button type="primary" ghost onClick={() => void load(keyword)}>查询</Button>
      <Button onClick={() => { setKeyword(''); void load(); }}>重置</Button>
      <div className="filter-spacer" />
      <span className="muted" style={{ fontSize: 11 }}>上传走 /files/upload · 版本文件走 /files/revisions</span>
    </div></div>

    <div className="card content-card">
      <div className="table-toolbar"><div className="table-title">文件与版本<span className="table-count">共 {total} 份</span></div></div>
      <Table rowKey="id" columns={columns} dataSource={docs} loading={loading} scroll={{ x: 1000 }}
        locale={{ emptyText: loadError ? '后端文件接口暂不可用' : '暂无文件，点击右上角上传' }} />
    </div>

    <Modal title="上传文件" open={uploadOpen} onCancel={() => setUploadOpen(false)} onOk={upload}
      confirmLoading={uploading} okText="上传" width={560}>
      <div style={{ marginBottom: 12 }}>
        <div className="description-item-label" style={{ marginBottom: 6 }}>知识分类（整批共用）</div>
        <Select style={{ width: '100%' }} value={uploadCategory} onChange={setUploadCategory} options={CATEGORIES} />
      </div>
      <Upload.Dragger multiple beforeUpload={(file) => { setUploadFiles((old) => [...old, file as unknown as File]); return false; }}
        onRemove={(file) => setUploadFiles((old) => old.filter((f) => f.name !== file.name))}
        fileList={uploadFiles.map((f, i) => ({ uid: String(i), name: f.name }))}>
        <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p>
        <p className="ant-upload-text">点击或拖拽文件到此区域</p>
        <p className="ant-upload-hint">单份保存为草稿（需预览确认后发布）；多份逐份自动解析并发布</p>
      </Upload.Dragger>
    </Modal>

    <Drawer width={720} open={!!selected} onClose={() => setSelected(null)}
      title={selected ? `${selected.title} · 版本文件` : '版本文件'}
      extra={<Tag icon={<HistoryOutlined />}>{revisions.length} 个版本</Tag>}>
      {revisions.length === 0 && !revisionLoading
        ? <Empty description="暂无版本记录" />
        : <Table rowKey={(r) => `${r.id ?? r.revision_id}-${r.version}`} size="small" loading={revisionLoading}
            columns={revisionColumns} dataSource={revisions} pagination={false} scroll={{ x: 640 }} />}
      <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        「发布」会把该版本切换为可检索内容；「恢复」基于历史正文生成新草稿，需重新确认发布。
        正文编辑与切片预览属于知识库流程（/knowledge/revisions）。
      </div>
    </Drawer>
  </>;
}
