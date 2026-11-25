"""Text aus hochgeladenen Prozessbeschreibungen extrahieren (PDF, Word, Text)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

MAX_CHARS = 30_000  # genug für ausführliche Prozessbeschreibungen, schützt vor riesigen Dokumenten

SUPPORTED = ("pdf", "docx", "txt", "md")


class DocumentError(ValueError):
    pass


@dataclass
class ExtractedText:
    text: str
    pages: int | None = None
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def extract_text(filename: str, data: bytes, max_chars: int = MAX_CHARS) -> ExtractedText:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        result = _from_pdf(data)
    elif ext == "docx":
        result = _from_docx(data)
    elif ext in ("txt", "md"):
        result = ExtractedText(_decode(data))
    elif ext == "doc":
        raise DocumentError("Das alte Word-Format .doc wird nicht unterstützt. Bitte als .docx oder PDF speichern.")
    else:
        raise DocumentError(f"Dateityp „.{ext}“ wird nicht unterstützt. Erlaubt: {', '.join(SUPPORTED)}.")

    result.text = _clean(result.text)
    if not result.text:
        hint = " Vermutlich ein eingescanntes PDF ohne Textebene (OCR nötig)." if ext == "pdf" else ""
        raise DocumentError("Im Dokument wurde kein Text gefunden." + hint)
    if len(result.text) > max_chars:
        result.text = result.text[:max_chars]
        result.truncated = True
        result.warnings.append(f"Das Dokument ist sehr lang und wurde nach {max_chars:,} Zeichen gekürzt.".replace(",", "."))
    return result


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _from_pdf(data: bytes) -> ExtractedText:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise DocumentError("Das PDF ist passwortgeschützt.") from e
        pages = [p.extract_text() or "" for p in reader.pages]
    except PdfReadError as e:
        raise DocumentError(f"Das PDF konnte nicht gelesen werden: {e}") from e
    return ExtractedText("\n\n".join(pages), pages=len(pages))


def _from_docx(data: bytes) -> ExtractedText:
    import docx

    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise DocumentError(f"Die Word-Datei konnte nicht gelesen werden: {e}") from e
    parts = [p.text for p in doc.paragraphs]
    # Tabellen enthalten in Prozessdokumenten oft Schritte und Zuständigkeiten
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(dict.fromkeys(c for c in cells if c)))
    return ExtractedText("\n".join(parts))


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # Silbentrennung am Zeilenende (PDF)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
