import io

import pytest

from copilot.documents import DocumentError, extract_text

TEXT = "Der Mitarbeiter stellt einen Urlaubsantrag. Der Teamleiter prüft den Antrag."


def _docx_bytes() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Urlaubsprozess", 1)
    d.add_paragraph(TEXT)
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Schritt", "Rolle"
    t.cell(1, 0).text, t.cell(1, 1).text = "Urlaub eintragen", "Personalabteilung"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf_bytes(lines) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 16
    c.showPage()
    c.drawString(50, 800, "Seite zwei: Bestätigung per E-Mail.")
    c.save()
    return buf.getvalue()


def test_docx_with_table():
    r = extract_text("prozess.docx", _docx_bytes())
    assert "Urlaubsantrag" in r.text and "prüft" in r.text
    assert "Urlaub eintragen | Personalabteilung" in r.text


def test_pdf_multi_page_and_hyphenation():
    r = extract_text("Prozess.PDF", _pdf_bytes(["Der Mitarbeiter stellt einen Urlaubs-", "antrag im Portal."]))
    assert r.pages == 2
    assert "Urlaubsantrag" in r.text and "Seite zwei" in r.text


def test_txt_encodings():
    assert extract_text("a.txt", TEXT.encode("cp1252")).text == TEXT
    assert extract_text("a.md", TEXT.encode("utf-8-sig")).text == TEXT


def test_truncation_warning():
    r = extract_text("a.txt", ("x" * 50).encode(), max_chars=20)
    assert r.truncated and len(r.text) == 20 and r.warnings


@pytest.mark.parametrize("name,data,msg", [
    ("a.doc", b"x", ".docx"),
    ("a.xlsx", b"x", "nicht unterstützt"),
    ("a.txt", b"   \n ", "kein Text"),
    ("a.pdf", b"kein pdf", "PDF"),
    ("a.docx", b"kein docx", "Word"),
])
def test_errors_are_user_friendly(name, data, msg):
    with pytest.raises(DocumentError, match=msg):
        extract_text(name, data)
