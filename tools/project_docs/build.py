"""Build the editable Word documentation pack, companion PPTX and audit artifacts."""
from __future__ import annotations
import csv, json, re, shutil, zipfile
from pathlib import Path
from docx import Document
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from .docx_common import Builder, h,h2,p,bullets,steps,table,code,note,sign,kv
from .content_management import specs as management_specs, S
from .content_technical import specs as technical_specs
from .content_delivery import specs as delivery_specs
from .content_data import TBD, FILL

ROOT=Path(__file__).resolve().parents[2]
PACK=ROOT/"deliverables/AI数字化人才平台_项目文档_Word版"
ZIP=ROOT/"deliverables/AI数字化人才平台_项目文档_Word版_V1.0_20261108.zip"

def safe(title):
    return re.sub(r'[\\/:*?"<>|]','_',title).replace(' ','_')

def set_font(run,size=18,bold=False,color="202020"):
    run.font.name="Calibri"; run.font.size=Pt(size); run.font.bold=bold; run.font.color.rgb=RGBColor.from_string(color)
    rpr=run._r.get_or_add_rPr(); rf=rpr.find(qn('a:ea'))
    if rf is None: rf=rpr.makeelement(qn('a:ea'),{}); rpr.append(rf)
    rf.set('typeface','微软雅黑')

def build_pptx(path):
    slides=[
        ("AI 数字化人才平台培训","智才云 TalentOS · 用户 / 管理员 / 运维","版本与日期待确认；AI辅助决策，重要结论人工复核"),
        ("目标与议程","学会核心闭环与安全边界","概览 → 人才岗位 → 匹配测评 → 培训知识 → 数据助手 → 管理运维"),
        ("平台概览","AI原生人才平台","Agent / RAG / Skills / NL2SQL / Dify；MySQL / PgSQL / Milvus / Redis"),
        ("角色与权限","五类角色与数据范围","super_admin / admin / hr / manager / employee；all / dept_sub / self"),
        ("登录与导航","安全登录与首页","登录 / 改密 / Ctrl K / 通知 / 个人中心；禁止共享账号与默认口令"),
        ("人才档案","简历到画像","上传 → 解析复核 → 查重 → 建档 → 画像 → 检索；敏感字段脱敏"),
        ("岗位管理","JD到确认画像","新建 → JD解析 → 复核 → 确认版本 → 开放匹配；未确认不可匹配"),
        ("人岗匹配","可解释匹配与复核","硬过滤 → 召回 → 打分 → 证据 → 解释 → 复核 → 预警；降级如实标注"),
        ("智能测评","组卷到能力报告","题库 → 组卷 → 作答监考 → 评分 → 短板报告；答案不泄露"),
        ("培训发展","个性化成长闭环","诊断 → 计划 → 课程 → 本人进度上报 → 考核复测；防刷"),
        ("知识库","发布与引用问答","草稿 → 切片预览 → 发布 → 索引 → 检索 → 引用；无依据拒答"),
        ("数据洞察","自然语言问数","问题 → 安全SQL → 图表 → 解读 → Word/PDF；只读白名单"),
        ("AI 助手","多轮对话与技能","SSE流式 / 引用 / 历史会话 / 技能调用；不输入口令密钥"),
        ("文件与审计","安全上传下载与留痕","类型大小 / 预签名 / 删除权限 / trace审计；导出按密级保存"),
        ("管理员","用户组织与配置","账号角色 / 组织防环 / 知识发布 / 模型配置；最小权限与变更审批"),
        ("运维","部署监控备份回滚","探针 / 指标 / 日志 / 备份恢复 / 故障排查；生产禁默认口令"),
        ("实操考核","六个练习闭环","EX-01~06；签到、考核、反馈归档；不合格补训"),
        ("支持与边界","求助与AI边界","trace_id + 脱敏截图；SLA按合同；降级不等于目标能力"),
    ]
    prs=Presentation(); prs.slide_width=Inches(13.333); prs.slide_height=Inches(7.5)
    for i,(title,sub,body) in enumerate(slides,1):
        s=prs.slides.add_slide(prs.slide_layouts[6])
        bg=s.background.fill; bg.solid(); bg.fore_color.rgb=RGBColor.from_string("F5F7FB" if i>1 else "172B59")
        bar=s.shapes.add_shape(1,Inches(0),Inches(0),prs.slide_width,Inches(0.12)); bar.fill.solid(); bar.fill.fore_color.rgb=RGBColor.from_string("315EFB"); bar.line.fill.background()
        tb=s.shapes.add_textbox(Inches(0.7),Inches(0.7 if i>1 else 2.0),Inches(12),Inches(1.2)); tf=tb.text_frame; tf.word_wrap=True
        r=tf.paragraphs[0].add_run(); r.text=title; set_font(r,34 if i>1 else 44,True,"172B59" if i>1 else "FFFFFF")
        tb=s.shapes.add_textbox(Inches(0.7),Inches(1.9 if i>1 else 3.3),Inches(12),Inches(0.8)); tf=tb.text_frame; tf.word_wrap=True
        r=tf.paragraphs[0].add_run(); r.text=sub; set_font(r,22,False,"315EFB" if i>1 else "B9C9E8")
        tb=s.shapes.add_textbox(Inches(0.7),Inches(3.0 if i>1 else 4.3),Inches(12),Inches(2.6)); tf=tb.text_frame; tf.word_wrap=True
        for j,line in enumerate(body.split("；")):
            para=tf.paragraphs[0] if j==0 else tf.add_paragraph(); para.space_after=Pt(12)
            r=para.add_run(); r.text="• "+line; set_font(r,20,False,"404050" if i>1 else "D8E2F7")
        tb=s.shapes.add_textbox(Inches(0.7),Inches(6.9),Inches(12),Inches(0.4)); tf=tb.text_frame
        r=tf.paragraphs[0].add_run(); r.text=f"AI 数字化人才平台 · 培训材料 V1.0 · {i}/{len(slides)}"; set_font(r,11,False,"98A2B3")
    path.parent.mkdir(parents=True,exist_ok=True); prs.save(path)

def validate_docx(path):
    d=Document(path); tables=len(d.tables); paras=len(d.paragraphs)
    headings=sum(1 for x in d.paragraphs if x.style.name.startswith('Heading 1'))
    assert paras>20 and tables>=3 and headings>=3, f"too thin: {path}"
    # table column consistency
    for t in d.tables:
        n=len(t.columns)
        for row in t.rows: assert len(row.cells)==n
    return dict(paragraphs=paras,tables=tables,headings=headings,bytes=path.stat().st_size)

def main():
    facts=json.loads((ROOT/"deliverables/project_facts.json").read_text(encoding="utf-8"))
    others=management_specs(facts)+technical_specs(facts)+delivery_specs(facts)
    others.sort(key=lambda s:s['id'])
    rows=[["AITP-00-01","项目文档总索引","00_总览","总索引","全部干系人"]]+[[s['id'],s['title'],s['folder'],s['status'],s['audience']] for s in others]
    index=S("AITP-00-01","项目文档总索引","00_总览",[
        h("交付包概览"),p(f"本包围绕《2026顶配版AI数字化人才平台详细技术与功能需求》与仓库代码基线 {facts['baseline']} 生成，共65份可编辑Word文档（含本索引），另附1份可编辑培训PPTX、静态快照、参考DDL与校验清单。文档按立项、规划、设计、执行测试、验收、收尾运维、合同商务分类。"),
        kv([["项目名称",facts['project']],["项目编号",facts['project_code']],["编制日期",facts['generated_date']],["代码基线",facts['baseline']],["Word文档数","65（含索引与指南）"],["附加材料","培训PPTX、文档清单CSV、端点CSV、ORM参考DDL、静态快照JSON、校验报告"],["文档状态","预填评审稿 / 待执行记录 / 合同草案；未确认项显式标注"]]),
        h("目录结构"),table(["目录","内容","份数"],[
            ["00_总览","索引、填写与审批指南","2"],
            ["01_立项与前期","建议书、可研、章程、干系人、初风险","5"],
            ["02_规划","总纲、范围、WBS/词典、SRS/清单/RTM、进度/成本/资源/质量/沟通/风险/采购/变更/配置","17"],
            ["03_设计","HLD、LLD、DBD、API、UI、部署、安全","7"],
            ["04_执行与测试","周报、月报、纪要、问题、测试计划/用例/报告/Bug/性能/安全、发布、上线、回滚","13"],
            ["05_验收","UAT方案、UAT记录、交付清单、终验报告","4"],
            ["06_收尾与运维移交","复盘、运维、用户、管理员、培训文档/PPT讲稿/签到、移交、归档","9"],
            ["07_合同商务","合同、补充、报价、需求确认、发票信息登记、付款、对账、NDA","8"],
            ["99_校验与清单","非Word附加材料：CSV/JSON/SQL/校验报告","附加"],
        ]),
        h("完整文档清单"),table(["编号","文档","目录","状态","使用对象"],rows),
        h("按对象选择"),table(["对象","优先阅读"],[["甲方 / 业务","01立项、章程、范围/SRS、UAT、终验、用户手册、交付清单"],["开发 / 运维","HLD、LLD、DBD、API、部署、安全、运维、回滚、管理员手册"],["管理层","项目计划、周报/月报、风险/问题台账、变更记录、复盘"],["商务 / 财务","合同、补充、报价、需求确认、付款、对账、NDA"],["QA / 安全","质量计划、测试计划/用例/报告、性能/安全、UAT"]]),
        h("现状与使用边界"),bullets(f"静态检查：{facts['checks']['syntax_files']}个Python文件语法无错误；ORM元数据MySQL {len(facts['models']['MySQL'])}表/PgSQL {len(facts['models']['PostgreSQL'])}表；Schema 23模块导入成功。",f"应用装配导入在文档编制最小依赖环境失败：{facts['checks']['application_import']['detail']}。完整运行环境未复核，列为上线前阻断核验项。","未运行应用业务测试、压测、安全测试、真实外部服务联调或UAT；报告类文档为待执行模板。","历史Markdown中的状态/表数量/测试数字可能过期；本包不将历史报告作为本次验收结论。","预算、人员、日期、金额、签字、测试结果均保留待确认/待填写；不得虚构。"),
        h("快速开始"),steps("解压后先读本索引与《文档填写与审批指南》。","按项目阶段复制对应Word，替换【待确认】并走评审审批。","技术文档结合99_校验与清单中的静态快照/端点/DDL核对当前代码。","执行类文档在活动发生后填写真实结果与证据，再签字归档。","小项目按指南中的精简版映射裁剪，但上线回滚、测试、验收、移交不可省。"),
        note("本包为可编辑模板与预填评审稿，不替代合同、法务意见、税务发票、等保测评或正式签字验收。"),
    ],status="总索引",audience="全部干系人",landscape=True)
    all_specs=[index]+others
    assert len(all_specs)==65, len(all_specs)
    if PACK.exists(): shutil.rmtree(PACK)
    PACK.mkdir(parents=True)
    manifest=[]
    for spec in all_specs:
        rel=Path(spec['folder'])/(spec['id']+"_"+safe(spec['title'])+".docx")
        path=PACK/rel; Builder(facts,spec).save(path); v=validate_docx(path)
        manifest.append(dict(id=spec['id'],title=spec['title'],folder=spec['folder'],file=str(rel),status=spec['status'],audience=spec['audience'],**v))
        print("OK",spec['id'],spec['title'],v['tables'],"tables",v['bytes'],"bytes")
    # Companion PPTX
    ppt=PACK/"06_收尾与运维移交/AITP-06-06_培训演示文稿.pptx"; build_pptx(ppt)
    # 99 artifacts
    audit=PACK/"99_校验与清单"; audit.mkdir(parents=True,exist_ok=True)
    with (audit/"文档清单.csv").open("w",newline="",encoding="utf-8-sig") as fp:
        w=csv.DictWriter(fp,fieldnames=list(manifest[0].keys())); w.writeheader(); w.writerows(manifest)
    with (audit/"端点静态清单.csv").open("w",newline="",encoding="utf-8-sig") as fp:
        w=csv.DictWriter(fp,fieldnames=["method","path","summary","handler","permissions","source","line"]); w.writeheader()
        for r in facts['routes']: w.writerow({**r,"permissions":",".join(r['permissions'])})
    ddl=["-- 当前ORM元数据编译的参考DDL，仅供评审；不是已批准生产迁移。",
         "-- 生产执行前必须DBA差异评审、备份、预生产演练与回滚准备；create_all不补列。"]
    for label,tables in facts['models'].items():
        ddl.append(f"\n-- ===== {label} =====")
        for t in tables: ddl.append(t['ddl']+";")
        for t in tables:
            for i in t['indexes']:
                # index DDL is emitted separately only when not already inline; keep as comment for review
                ddl.append(f"-- index {t['name']}.{i['name']} ({','.join(i['columns'])}) unique={i['unique']}")
    (audit/"当前ORM参考DDL.sql").write_text("\n".join(ddl),encoding="utf-8")
    shutil.copyfile(ROOT/"deliverables/project_facts.json",audit/"当前代码静态快照.json")
    # Validation report
    lines=["# 文档生成校验报告","",f"- 生成日期：{facts['generated_date']}",f"- 代码基线：{facts['baseline']}",f"- Word文档：{len(manifest)}；培训PPTX：1；附加CSV/JSON/SQL/报告若干。","",
           "## 静态检查（文档编制环境）",f"- Python：{facts['checks']['python']}",f"- 源码语法AST：{facts['checks']['syntax_files']}个文件，{len(facts['checks']['syntax_errors'])}个错误。",
           f"- ORM元数据：MySQL {len(facts['models']['MySQL'])}表，PostgreSQL {len(facts['models']['PostgreSQL'])}表。",
           "- Pydantic Schema：23个模块导入成功；端点静态线索%d条。"%len(facts['routes']),
           f"- 应用装配导入：{facts['checks']['application_import']['status']}；{facts['checks']['application_import']['detail']}",
           "- 边界：未运行应用业务测试、压测、安全测试、真实外部服务联调或UAT；完整运行环境未复核。","",
           "## Word结构校验","每份文档均重新打开校验：段落>20、表格>=3、一级标题>=3、表格列数一致。明细见文档清单.csv。","",
           "| 编号 | 文档 | 表格 | 段落 | 字节 |","|---|---|---:|---:|---:|"]
    for m in manifest: lines.append(f"| {m['id']} | {m['title']} | {m['tables']} | {m['paragraphs']} | {m['bytes']} |")
    lines += ["","## 来源文件哈希","| 来源 | SHA256 |","|---|---|"]
    for s in facts['sources']: lines.append(f"| {s['path']} | {s['sha256']} |")
    lines += ["","## 使用限制","- 模板中的【待确认】【待执行】【待填写】必须由责任人核实；空白签字不代表审批/验收/付款。",
              "- 合同/NDA/发票登记/付款/对账需法务财务审核；发票登记不是税务发票。",
              "- 历史Markdown状态与测试数字可能过期；本包不继承为本次验收结论。",
              "- 参考DDL不是生产迁移脚本；生产变更走DBA审批与回滚演练。"]
    (audit/"文档生成校验报告.md").write_text("\n".join(lines),encoding="utf-8")
    (PACK/"README_使用说明.txt").write_text(
        "AI数字化人才平台 项目文档 Word版 V1.0\n"
        f"生成日期：{facts['generated_date']}；代码基线：{facts['baseline']}\n"
        "Word文档65份（含总索引与填写指南），另附培训PPTX与99_校验与清单附加材料。\n"
        "先读 00_总览/AITP-00-01_项目文档总索引.docx 与 AITP-00-02_文档填写与审批指南.docx。\n"
        "所有【待确认】【待执行】【待填写】须责任人核实；报告/签字/金额不得虚构。\n"
        "合同/NDA/发票登记需法务财务审核；发票登记不是税务发票；参考DDL不是生产迁移。\n"
        "未运行应用业务测试/压测/安全/真实联调/UAT；历史报告不作为本次验收结论。\n",encoding="utf-8")
    # Zip
    if ZIP.exists(): ZIP.unlink()
    with zipfile.ZipFile(ZIP,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for item in sorted(PACK.rglob("*")):
            if item.is_file(): z.write(item,Path(PACK.name)/item.relative_to(PACK))
    with zipfile.ZipFile(ZIP) as z:
        assert z.testzip() is None
        names=z.namelist(); docx=[n for n in names if n.endswith('.docx')]
        assert len(docx)==65, len(docx)
    print("PACK",PACK,"ZIP",ZIP,"files",len(names),"docx",len(docx),"zip_bytes",ZIP.stat().st_size)
    (ROOT/"deliverables/build_manifest.json").write_text(json.dumps(dict(pack=str(PACK),zip=str(ZIP),word_count=len(manifest),manifest=manifest),ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__": main()
