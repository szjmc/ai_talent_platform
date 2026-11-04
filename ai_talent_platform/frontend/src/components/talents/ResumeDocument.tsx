import { MailOutlined, MobileOutlined, EnvironmentOutlined } from '@ant-design/icons';
import type { Talent } from '../../types';

/**
 * 使用真实人才字段渲染简历预览，不依赖外部图片服务。
 * 同一文档组件同时用于卡片缩略图和弹窗大图，保证预览内容一致。
 */
export default function ResumeDocument({ talent, detailed = false }: { talent: Talent; detailed?: boolean }) {
  return (
    <article className={`resume-document ${detailed ? 'resume-document-detailed' : ''}`} aria-label={`${talent.name}的简历预览`}>
      <header className="resume-doc-header">
        <div className="resume-doc-avatar">{talent.name.slice(-2)}</div>
        <div className="min-w-0 flex-1">
          <div className="resume-doc-name">{talent.name}<span>{talent.job_level || '专业人才'}</span></div>
          <div className="resume-doc-role">{talent.current_position}</div>
        </div>
        <div className="resume-doc-badge">TALENTOS</div>
      </header>
      <div className="resume-doc-contact">
        <span><MobileOutlined />{talent.mobile}</span>
        {detailed && <span><MailOutlined />{talent.email || '未填写邮箱'}</span>}
        <span><EnvironmentOutlined />{talent.work_city || talent.province || '中国'}</span>
      </div>
      <section className="resume-doc-section">
        <h4><span />职业概况</h4>
        <p>{talent.portrait_summary || `${talent.work_years} 年相关工作经验，现任${talent.current_position}。`}</p>
      </section>
      <section className="resume-doc-section">
        <h4><span />工作经历</h4>
        <div className="resume-doc-job">
          <div className="flex items-center justify-between gap-2"><b>{talent.current_company || '当前公司未填写'} · {talent.current_position || '职位未填写'}</b><time>{talent.work_years} 年经验</time></div>
          {talent.self_evaluation && <p>{talent.self_evaluation}</p>}
        </div>
      </section>
      <section className="resume-doc-section">
        <h4><span />教育背景</h4>
        <div className="resume-doc-job"><div className="flex items-center justify-between gap-2"><b>{talent.school} · {talent.major}</b><time>{talent.education}</time></div></div>
      </section>
      <section className="resume-doc-section resume-doc-skills">
        <h4><span />专业能力</h4>
        <div className="flex flex-wrap gap-1.5">{talent.skill_tags.map((skill) => <em key={skill}>{skill}</em>)}</div>
      </section>
      <footer className="resume-doc-footer"><span>AI 结构化人才档案</span><span>{talent.created_at ? `建档于 ${talent.created_at.slice(0, 10)}` : '人才档案预览'}</span></footer>
    </article>
  );
}
