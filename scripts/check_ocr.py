"""Render an independent Chinese text page into a scan, then verify real OCR."""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fitz
from app.extract import parse_pdf
with tempfile.TemporaryDirectory() as td:
    source=fitz.open();p=source.new_page()
    for i,t in enumerate(['演示市发展和改革局','项目总建筑面积4702平方米','项目建设工期为24个月','项目总投资2998万元']):
        p.insert_text((50,80+i*50),t,fontname='china-s',fontsize=20)
    pix=p.get_pixmap(matrix=fitz.Matrix(3,3));scan=fitz.open();sp=scan.new_page();sp.insert_image(sp.rect,stream=pix.tobytes('png'));path=Path(td)/'scan.pdf';scan.save(path)
    pages,lines,text,*_=parse_pdf(path)
    ok=pages[0]['text_method']=='ocr' and '4702' in text and '24' in text and '2998' in text
    print(json.dumps({'passed':ok,'method':pages[0]['text_method'],'text':text,'lines':len(lines)},ensure_ascii=False))
    sys.exit(0 if ok else 1)
