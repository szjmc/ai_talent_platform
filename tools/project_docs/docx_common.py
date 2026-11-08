"""Word document layout helpers for the Chinese project documentation pack."""
from __future__ import annotations
import math, re
from pathlib import Path
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION, WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

BLUE="1F4E79"; LIGHT="EAF1F8"; BORDER="C9D6E4"; GRAY="667085"; AMBER="FFF4D6"
BODY_FONT="宋体"; HEAD_FONT="微软雅黑"; LATIN="Calibri"

def _font(run, size=10.5, bold=False, color=None, east=BODY_FONT, latin=LATIN):
    run.font.name=latin; run.font.size=Pt(size); run.bold=bold
    if color: run.font.color.rgb=RGBColor.from_string(color)
    rpr=run._element.get_or_add_rPr(); rf=rpr.find(qn('w:rFonts'))
    if rf is None: rf=OxmlElement('w:rFonts'); rpr.insert(0,rf)
    rf.set(qn('w:eastAsia'),east); rf.set(qn('w:ascii'),latin); rf.set(qn('w:hAnsi'),latin)
    return run

def _shade(cell, fill):
    tcpr=cell._tc.get_or_add_tcPr(); sh=OxmlElement('w:shd'); sh.set(qn('w:fill'),fill); tcpr.append(sh)

def _field(p, instr):
    r=p.add_run(); b=OxmlElement('w:fldChar'); b.set(qn('w:fldCharType'),'begin'); r._r.append(b)
    r=p.add_run(); t=OxmlElement('w:instrText'); t.set(qn('xml:space'),'preserve'); t.text=instr; r._r.append(t)
    r=p.add_run(); e=OxmlElement('w:fldChar'); e.set(qn('w:fldCharType'),'end'); r._r.append(e)

def _display_len(s):
    return sum(2 if ord(c)>127 else 1 for c in str(s))

def _wrap(s):
    s=str(s)
    # Permit long identifiers/paths to wrap in fixed-width tables without changing meaning.
    return re.sub(r'([/_\.])(?=\S)','\\1\u200b',s) if len(s)>28 else s

class Builder:
    def __init__(self, facts, spec):
        self.facts=facts; self.spec=spec; self.doc=Document(); self.nav=[]; self.h1=0
        self._styles(); self._page(self.doc.sections[0]); self._header_footer()
        self._cover(); self._control()
        if spec.get("landscape"):
            sec=self.doc.add_section(WD_SECTION.NEW_PAGE); self._page(sec,True); self._header_footer(sec)
        self._body()
    def _styles(self):
        st=self.doc.styles
        for name,size,font,color in [('Normal',10.5,BODY_FONT,'202020'),('Title',26,HEAD_FONT,BLUE),
                                     ('Heading 1',15,HEAD_FONT,BLUE),('Heading 2',12,HEAD_FONT,'2E74B5'),('Heading 3',10.5,HEAD_FONT,'404040')]:
            s=st[name]; s.font.name=LATIN; s.font.size=Pt(size); s.font.bold=name!='Normal'; s.font.color.rgb=RGBColor.from_string(color)
            s.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),font)
            pf=s.paragraph_format; pf.space_before=Pt(10 if name.startswith('Heading') else 0); pf.space_after=Pt(6); pf.line_spacing=1.22
            if name.startswith('Heading'): pf.keep_with_next=True
        for name in ['List Bullet','List Number']:
            st[name].font.name=LATIN; st[name].font.size=Pt(10.5)
            st[name].element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),BODY_FONT)
        settings=self.doc.settings.element
        uf=OxmlElement('w:updateFields'); uf.set(qn('w:val'),'true'); settings.append(uf)
    def _page(self,sec,landscape=False):
        sec.orientation=WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
        sec.page_width=Cm(29.7 if landscape else 21.0); sec.page_height=Cm(21.0 if landscape else 29.7)
        sec.top_margin=Cm(2.0); sec.bottom_margin=Cm(1.8); sec.left_margin=Cm(2.0); sec.right_margin=Cm(2.0)
        sec.header_distance=Cm(0.9); sec.footer_distance=Cm(0.9)
    def _header_footer(self,sec=None):
        sec=sec or self.doc.sections[0]
        h=sec.header.paragraphs[0]; h.text=""; h.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        _font(h.add_run(f"{self.spec['id']}  |  AI 数字化人才平台"),8,color=GRAY,east=HEAD_FONT)
        f=sec.footer.paragraphs[0]; f.text=""; f.alignment=WD_ALIGN_PARAGRAPH.CENTER
        _font(f.add_run("受控文档  ·  V1.0  ·  第 "),8,color=GRAY)
        _field(f,"PAGE"); _font(f.add_run(" 页 / 共 "),8,color=GRAY); _field(f,"NUMPAGES"); _font(f.add_run(" 页"),8,color=GRAY)
    def _cover(self):
        d=self.doc
        p=d.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        _font(p.add_run(f"{self.spec['id']}  /  {self.spec['category']}"),9,color=GRAY,east=HEAD_FONT)
        p=d.add_paragraph(); p.paragraph_format.space_before=Pt(70)
        _font(p.add_run("AI 数字化人才平台"),16,bold=True,color=BLUE,east=HEAD_FONT)
        p=d.add_paragraph(); p.style=d.styles['Title']; p.paragraph_format.space_after=Pt(10)
        _font(p.add_run(self.spec['title']),26,bold=True,color=BLUE,east=HEAD_FONT)
        p=d.add_paragraph(); _font(p.add_run(self.spec.get('subtitle','项目管理与技术交付文档 · 可编辑 Word 版')),11,color=GRAY,east=HEAD_FONT)
        p=d.add_paragraph(); p.paragraph_format.space_before=Pt(18)
        _font(p.add_run(f"文档状态：{self.spec['status']}"),11,bold=True,color="9C6500",east=HEAD_FONT)
        self.table(["控制项","内容"],[
            ["项目名称",self.facts['project']],["项目编号",self.facts['project_code']],
            ["文档编号",self.spec['id']],["版本 / 编制日期",f"V1.0 / {self.facts['generated_date']}"],
            ["代码基线",self.facts['baseline'][:12]+"（完整值见文档控制页）"],
            ["编制单位 / 编制人","【待确认】"],["使用对象",self.spec.get('audience','项目组、甲方、开发 / 运维 / 管理人员')],
            ["密级","内部资料；含个人信息或商务条款时按最小授权分发"],
        ],[3.4,13.2])
        p=d.add_paragraph(); p.paragraph_format.space_before=Pt(18)
        _font(p.add_run("使用提示：本文件为结合项目资料预填的可编辑模板。标注【待确认】【待执行】【待填写】的内容须由责任人核实；空白签字栏不代表已审批、已测试、已验收或已付款。"),9,color=GRAY)
        d.add_page_break()
    def _control(self):
        d=self.doc
        d.add_heading("文档控制与审批",level=1)
        self.table(["版本","日期","修订说明","编制 / 修订人"],[["V1.0",self.facts['generated_date'],"首次生成：依据原始需求、仓库代码与既有文档预填；未确认项显式留空。","项目组【待确认】"],["【待填写】","【待填写】","【变更摘要 / 变更单号】","【待填写】"]],[1.5,2.6,9.0,3.5])
        d.add_heading("审批记录",level=2)
        self.table(["环节","姓名 / 角色","意见","签字","日期"],[
            ["编制","【待确认】","【待填写】","",""],["审核","【技术 / 业务 / 安全负责人】","【待填写】","",""],
            ["批准","【授权审批人 / CCB】","【待填写】","",""]],[1.6,4.2,6.0,2.4,2.4])
        d.add_heading("内容导航",level=2)
        for b in self.spec['blocks']:
            if b[0]=='h': self.nav.append(b[1])
        for i,t in enumerate(self.nav,1):
            p=d.add_paragraph(); _font(p.add_run(f"{i:02d}  {t}"),10)
            p.paragraph_format.space_after=Pt(3)
        d.add_heading("依据与边界",level=2)
        self.p("原始需求：《2026顶配版AI数字化人才平台详细技术与功能需求》；代码基线："+self.facts['baseline']+"。")
        self.p("仓库既有 Markdown 文档用于追溯设计意图；其中历史状态、表数量、测试数字可能过期。本包正文以“目标需求 / 静态代码发现 / 待验证”分层表述，不将历史报告直接作为本次验收证据。")
        self.note("本文件不替代合同、法务意见、税务发票、等级保护测评报告或正式签字验收单。涉及生产变更、付款、个人信息处理与对外承诺的内容，必须走组织审批。")
        d.add_page_break()
    def _body(self):
        for b in self.spec['blocks']: self.block(b)
    def block(self,b):
        kind=b[0]
        if kind=='h': self.h1+=1; self.doc.add_heading(f"{self.h1}  {b[1]}",level=1)
        elif kind=='h2': self.doc.add_heading(b[1],level=2)
        elif kind=='h3': self.doc.add_heading(b[1],level=3)
        elif kind=='p': self.p(b[1])
        elif kind=='bullets':
            for x in b[1]: self.doc.add_paragraph(str(x),style='List Bullet')
        elif kind=='steps':
            for x in b[1]: self.doc.add_paragraph(str(x),style='List Number')
        elif kind=='table': self.table(b[1],b[2],b[3] if len(b)>3 else None)
        elif kind=='code': self.code(b[1])
        elif kind=='note': self.note(b[1])
        elif kind=='pagebreak': self.doc.add_page_break()
        elif kind=='sign': self.signatures(b[1] if len(b)>1 else None)
        else: raise ValueError(kind)
    def p(self,text):
        p=self.doc.add_paragraph(); _font(p.add_run(str(text)),10.5); return p
    def note(self,text):
        p=self.doc.add_paragraph(); p.paragraph_format.space_before=Pt(4); p.paragraph_format.space_after=Pt(8)
        ppr=p._p.get_or_add_pPr(); sh=OxmlElement('w:shd'); sh.set(qn('w:fill'),AMBER); ppr.append(sh)
        _font(p.add_run("说明："),9.5,bold=True,color="9C6500",east=HEAD_FONT); _font(p.add_run(str(text)),9.5)
    def code(self,text):
        for line in str(text).splitlines() or [""]:
            p=self.doc.add_paragraph(); p.paragraph_format.space_after=Pt(0); p.paragraph_format.line_spacing=1.0
            ppr=p._p.get_or_add_pPr(); sh=OxmlElement('w:shd'); sh.set(qn('w:fill'),'F4F6F8'); ppr.append(sh)
            _font(p.add_run(line or " "),8.5,latin='Consolas',east=BODY_FONT)
        self.doc.add_paragraph().paragraph_format.space_after=Pt(2)
    def table(self,headers,rows,widths=None):
        if not rows: rows=[["【待填写】"]*len(headers)]
        n=len(headers); t=self.doc.add_table(rows=1,cols=n); t.alignment=WD_TABLE_ALIGNMENT.CENTER; t.autofit=False
        sec=self.doc.sections[-1]; avail=(sec.page_width-sec.left_margin-sec.right_margin)/360000
        if widths is None:
            scores=[]
            for i,h in enumerate(headers):
                lens=sorted(_display_len(r[i]) if i<len(r) else 0 for r in rows)
                p80=lens[min(len(lens)-1,int(len(lens)*0.8))]
                scores.append(max(_display_len(h),min(p80,46),8)+2)
            widths=[avail*s/sum(scores) for s in scores]
        else:
            widths=[avail*w/sum(widths) for w in widths]
        # Table grid
        for i,w in enumerate(widths): t.columns[i].width=Cm(w)
        tblpr=t._tbl.tblPr
        borders=OxmlElement('w:tblBorders')
        for edge in ['top','left','bottom','right','insideH','insideV']:
            e=OxmlElement('w:'+edge); e.set(qn('w:val'),'single'); e.set(qn('w:sz'),'4'); e.set(qn('w:color'),BORDER); borders.append(e)
        tblpr.append(borders)
        margins=OxmlElement('w:tblCellMar')
        for edge,val in [('top','60'),('left','90'),('bottom','60'),('right','90')]:
            e=OxmlElement('w:'+edge); e.set(qn('w:w'),val); e.set(qn('w:type'),'dxa'); margins.append(e)
        tblpr.append(margins)
        hdr=t.rows[0]; trpr=hdr._tr.get_or_add_trPr(); rep=OxmlElement('w:tblHeader'); rep.set(qn('w:val'),'true'); trpr.append(rep)
        for i,h in enumerate(headers):
            c=hdr.cells[i]; c.width=Cm(widths[i]); _shade(c,BLUE); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p=c.paragraphs[0]; p.paragraph_format.space_after=Pt(2); _font(p.add_run(str(h)),9,bold=True,color='FFFFFF',east=HEAD_FONT)
        for ri,row in enumerate(rows):
            cells=t.add_row().cells
            trpr=t.rows[-1]._tr.get_or_add_trPr(); cs=OxmlElement('w:cantSplit'); trpr.append(cs)
            for i in range(n):
                val=row[i] if i<len(row) else ""
                c=cells[i]; c.width=Cm(widths[i]); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.TOP
                if ri%2==1: _shade(c,'F5F8FC')
                p=c.paragraphs[0]; p.paragraph_format.space_after=Pt(2); p.paragraph_format.line_spacing=1.12
                _font(p.add_run(_wrap(val)),8.7 if n>=6 else 9.2)
        self.doc.add_paragraph().paragraph_format.space_after=Pt(2)
        return t
    def signatures(self,roles=None):
        roles=roles or ["甲方代表","乙方项目经理","技术负责人","验收 / 审批人"]
        self.table(["角色","姓名","意见 / 结论","签字","日期"],[[r,"【待确认】","【待填写】","",""] for r in roles],[3,3,6,2.3,2.3])
    def save(self,path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        cp=self.doc.core_properties; cp.title=self.spec['title']; cp.subject=self.facts['project']; cp.author="项目组（待确认）"
        cp.keywords=f"{self.spec['id']},AI数字化人才平台,Word模板,V1.0"; cp.comments="依据仓库需求与代码基线生成；待确认项需责任人核实。"
        self.doc.save(path)

# Block constructors keep content definitions short and readable.
def h(t): return ('h',t)
def h2(t): return ('h2',t)
def p(t): return ('p',t)
def bullets(*x): return ('bullets',list(x))
def steps(*x): return ('steps',list(x))
def table(headers,rows,widths=None): return ('table',headers,rows,widths)
def code(t): return ('code',t)
def note(t): return ('note',t)
def sign(roles=None): return ('sign',roles)
def kv(rows): return table(["项目","内容"],rows,[3,14])
def blanks(headers,n=4): return table(headers,[["【待填写】"]*len(headers) for _ in range(n)])
def h3(t): return ('h3',t)
