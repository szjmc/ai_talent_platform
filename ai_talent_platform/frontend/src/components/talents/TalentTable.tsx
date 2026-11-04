import { Button, Pagination, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DeleteOutlined, EditOutlined, EnvironmentOutlined } from '@ant-design/icons';
import { PersonAvatar, PotentialTag, SkillTags, StatusBadge } from '../Common';
import type { Talent } from '../../types';
import { formatDate } from '../../utils';

interface Props {
  talents: Talent[];
  loading?: boolean;
  province?: string;
  onPortrait: (talent: Talent) => void;
  onEdit: (talent: Talent) => void;
  onDelete: (talent: Talent) => void;
}

/** 人才表格独立组件，仅通过 props 与页面/API 状态通信。 */
export default function TalentTable({ talents, loading, province, onPortrait, onEdit, onDelete }: Props) {
  const columns: ColumnsType<Talent> = [
    { title: '人才信息', dataIndex: 'name', width: 220, fixed: 'left', render: (_, record) => <div className="talent-cell"><PersonAvatar name={record.name} size={38} /><div><div className="talent-cell-name">{record.name}<span className="ml-2 text-[10px] font-normal text-[#9aa3b3]">ID {String(record.id).padStart(4, '0')}</span></div><div className="talent-cell-role">{record.current_position} · {record.work_years} 年经验</div></div></div> },
    { title: '工作地', dataIndex: 'work_city', width: 110, render: (_, record) => <span className="text-[#59667a]"><EnvironmentOutlined className="mr-1.5 text-[#8b96a8]" />{record.work_city || record.province || '—'}</span> },
    { title: '所属部门', dataIndex: 'organization_name', width: 125 },
    { title: '学历 / 院校', dataIndex: 'education', width: 155, render: (_, record) => <div>{record.education}<div className="talent-cell-role">{record.school}</div></div> },
    { title: '核心技能', dataIndex: 'skill_tags', render: (value) => <SkillTags tags={value} max={3} /> },
    { title: '状态', dataIndex: 'status', width: 90, render: (value) => <StatusBadge status={value} /> },
    { title: '潜力等级', dataIndex: 'potential_level', width: 85, render: (value) => <PotentialTag level={value} /> },
    { title: '建档日期', dataIndex: 'created_at', width: 115, render: (value) => formatDate(value) },
    { title: '操作', fixed: 'right', width: 145, render: (_, record) => <div className="flex gap-2"><Button type="link" size="small" onClick={() => onPortrait(record)}>画像</Button><Button type="text" size="small" icon={<EditOutlined />} onClick={() => onEdit(record)} /><Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => onDelete(record)} /></div> },
  ];
  return (
    <section className="card content-card talent-table-section">
      <div className="table-toolbar"><div className="table-title">人才列表<span className="table-count">{province ? `${province} · ` : ''}共 {talents.length} 条匹配记录</span></div><span className="text-[11px] text-[#8b95a7]">列表与上方简历、地图实时联动</span></div>
      <Table rowKey="id" columns={columns} dataSource={talents} loading={loading} pagination={false} scroll={{ x: 1370 }} />
      <div className="table-footer"><Pagination total={talents.length} pageSize={20} showSizeChanger={false} showTotal={(total) => `共 ${total} 条`} /></div>
    </section>
  );
}
