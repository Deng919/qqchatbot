"""Bounded local document extraction; scripts and external links never run."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_CHARS = 12000
FILE_TYPES = {'.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.md', '.csv', '.json', '.log'}


def read_document(data: bytes, name: str) -> tuple[str, bool, list[bytes]]:
    suffix = Path(name).suffix.lower()
    if suffix not in FILE_TYPES: raise NotImplementedError('暂不支持此文件格式')
    partial = False; images = []
    if suffix == '.pdf':
        import fitz
        with fitz.open(stream=data, filetype='pdf') as document:
            if document.needs_pass: raise ValueError('文件需要密码')
            partial = len(document) > 10; parts = []
            for page in document.pages(stop=min(len(document),10)):
                text = page.get_text().strip()
                if len(text)>=30: parts.append(f'第 {page.number+1} 页\n{text}')
                else:
                    scale = min(2,2000/max(page.rect.width,page.rect.height,1))
                    images.append(page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png'))
            text = '\n\n'.join(parts)
    elif suffix in {'.docx','.xlsx','.pptx'}:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries)>5000 or sum(info.file_size for info in entries)>64*1024*1024: raise ValueError('文件展开后过大')
            def xml(name):
                raw = package.read(name)
                if b'<!DOCTYPE' in raw.upper(): raise ValueError('不支持此文档结构')
                return ET.fromstring(raw)
            parts = []
            if suffix == '.docx':
                ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                for paragraph in xml('word/document.xml').iter(ns+'p'):
                    parts.append(''.join(node.text or '' for node in paragraph.iter(ns+'t')))
                    if sum(map(len,parts))>MAX_CHARS:partial=True;break
            elif suffix == '.pptx':
                names=[info.filename for info in entries if info.filename.startswith('ppt/slides/slide') and info.filename.endswith('.xml')]
                names.sort(key=lambda name:int(Path(name).stem.removeprefix('slide')))
                for index,name in enumerate(names):
                    parts.append(f'第 {index+1} 页\n'+' '.join(node.text or '' for node in xml(name).iter() if node.tag.endswith('}t')))
                    if sum(map(len,parts))>MAX_CHARS:partial=True;break
            else:
                ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}';strings=[]
                if 'xl/sharedStrings.xml' in package.namelist():
                    strings=[''.join(t.text or '' for t in item.iter(ns+'t')) for item in xml('xl/sharedStrings.xml').iter(ns+'si')]
                names=sorted(info.filename for info in entries if info.filename.startswith('xl/worksheets/sheet') and info.filename.endswith('.xml'))
                for name in names:
                    parts.append(Path(name).stem)
                    for row in xml(name).iter(ns+'row'):
                        cells=[]
                        for cell in row.iter(ns+'c'):
                            value=cell.findtext(ns+'v',default='')
                            if cell.get('t')=='s':value=strings[int(value)] if value.isdigit() and int(value)<len(strings) else ''
                            elif cell.get('t')=='inlineStr':value=''.join(t.text or '' for t in cell.iter(ns+'t'))
                            cells.append(value)
                        parts.append('\t'.join(cells))
                        if sum(map(len,parts))>MAX_CHARS:partial=True;break
                    if partial:break
            text='\n'.join(parts)
    else:
        text=None
        for encoding in ('utf-8-sig','utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8','gb18030'):
            try:text=data.decode(encoding);break
            except UnicodeError:continue
        if text is None or '\x00' in text:raise ValueError('文件文字编码不可读')
    return text[:MAX_CHARS],partial or len(text)>MAX_CHARS,images
