import io
import zipfile

import fitz
import pytest

from qq_digest.attachment_readers import read_document


def package(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name,value in entries.items():z.writestr(name,value)
    return output.getvalue()


def test_office_documents_extract_text_tables_and_slides():
    doc=package({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>文档正文</w:t></w:r></w:p></w:document>'})
    assert read_document(doc,'合成.docx')==('文档正文',False,[])
    sheet=package({'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>名称</t></si></sst>',
        'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><row><c t="s"><v>0</v></c><c><v>42</v></c></row></worksheet>'})
    assert '名称\t42' in read_document(sheet,'合成.xlsx')[0]
    slide=lambda text:f'<a:slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>{text}</a:t></a:slide>'
    slides=package({'ppt/slides/slide10.xml':slide('十'),'ppt/slides/slide2.xml':slide('二')})
    assert read_document(slides,'合成.pptx')[0].index('二')<read_document(slides,'合成.pptx')[0].index('十')


def test_pdf_text_and_scanned_pages_and_page_limit():
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50,50),'Synthetic document text contains the needed project facts.')
        for _ in range(10):pdf.new_page()
        data=pdf.tobytes()
    text,partial,images=read_document(data,'合成.pdf')
    assert 'project facts' in text and partial and len(images)==9
    assert all(image.startswith(b'\x89PNG') for image in images)


def test_local_document_limits_and_untrusted_xml():
    text,partial,images=read_document(('内容'*7000).encode(),'合成.md')
    assert len(text)==12000 and partial and images==[]
    bad=package({'word/document.xml':'<!DOCTYPE x [<!ENTITY x "bad">]><x/>'})
    with pytest.raises(ValueError):read_document(bad,'合成.docx')
    with pytest.raises(NotImplementedError):read_document(b'not executable','合成.exe')
