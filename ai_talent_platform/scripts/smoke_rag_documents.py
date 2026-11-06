"""多格式与扫描件解析回归；临时样例自动清理。
标签：[RAG组] [P1]。"""
import tempfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from docx import Document as Word
from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas
from app.ai.rag.document_loader import load
from app.ai.rag.document_splitter import split_document

def main():
 with tempfile.TemporaryDirectory(prefix='rag-doc-test-') as temp:
  root=Path(temp)
  word=Word();word.add_paragraph('Before table');table=word.add_table(rows=1,cols=1);table.cell(0,0).text='Python FastAPI';word.add_paragraph('After table');word.save(root/'sample.docx')
  docs=load(str(root/'sample.docx'));text=docs[0].page_content
  assert text.index('Before')<text.index('FastAPI')<text.index('After')
  print('WORD_PARAGRAPH_TABLE_ORDER_PASS',flush=True)
  book=Workbook();book.active.title='技术';book.active.append(['Python','FastAPI']);sheet=book.create_sheet('培训');sheet.append(['Milvus','课程']);book.save(root/'sample.xlsx');book.close()
  docs=load(str(root/'sample.xlsx'))
  assert {d.metadata['sheet'] for d in docs}=={'技术','培训'}
  assert all('工作表：' in d.page_content and 'row' in d.metadata for d in docs)
  assert all('sheet' in c.metadata for c in split_document(docs))
  print('EXCEL_SHEET_ROW_SOURCE_PASS',flush=True)
  image=Image.new('RGB',(1400,400),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',48)
  draw.text((50,70),'技术文档 Python FastAPI',font=font,fill='black');draw.text((50,170),'培训课程 MySQL Milvus',font=font,fill='black');image.save(root/'scan.png')
  docs=load(str(root/'scan.png'),{'file_id':123})
  assert 'FastAPI' in docs[0].page_content and '技术文档' in docs[0].page_content,docs
  assert docs[0].metadata['ocr'] and docs[0].metadata['file_id']==123
  print('CHINESE_IMAGE_OCR_PASS',flush=True)
  canvas=Canvas(str(root/'mixed.pdf'));canvas.drawString(50,750,'Page one Python FastAPI');canvas.showPage();canvas.drawImage(str(root/'scan.png'),20,400,width=560,height=160);canvas.showPage();canvas.save()
  docs=load(str(root/'mixed.pdf'))
  assert len(docs)==2 and docs[0].metadata['page']==1 and not docs[0].metadata['ocr']
  assert docs[1].metadata['page']==2 and docs[1].metadata['ocr'] and 'FastAPI' in docs[1].page_content
  print('MIXED_PDF_TEXT_SCAN_PAGE_SOURCE_PASS',flush=True)
  Image.new('RGB',(300,200),'white').save(root/'blank.png')
  from app.core.exceptions import FileError
  try: load(str(root/'blank.png'))
  except FileError: print('EMPTY_SCAN_REJECTED_PASS',flush=True)
  else: raise AssertionError('blank image accepted')
if __name__=='__main__':main()
