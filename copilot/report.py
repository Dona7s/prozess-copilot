"""PDF-Bericht: Diagramm(e), Analyse, Soll-Prozess und Kennzahlen.

Die Diagramme werden direkt aus dem berechneten Layout als Vektorgrafik gezeichnet
(kein Browser, kein Screenshot) und sehen daher in jeder Zoomstufe scharf aus.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Circle, Drawing, Group, Line, Polygon, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .bpmn import POOL_X, layout_geometry
from .compare import Comparison
from .schema import GATEWAY_TYPES, TASK_TYPES, NodeType, ProcessAnalysis, ProcessModel

# ---------------------------------------------------------------- Schrift

FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
try:  # Vera liegt reportlab bei und deckt Umlaute und typografische Zeichen ab
    import reportlab

    _dir = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    pdfmetrics.registerFont(TTFont("Vera", os.path.join(_dir, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("Vera-Bold", os.path.join(_dir, "VeraBd.ttf")))
    FONT, FONT_BOLD = "Vera", "Vera-Bold"
except Exception:  # pragma: no cover - Fallback auf Standardschrift
    pass

_REPLACE = {"→": "->", "←": "<-", "≙": "=", "✅": "ja", "❌": "nein", "⚠": "!", "•": "-", "ʻ": "'", "ʼ": "'", "‘": "'",
            "’": "'"}


def _font_has(ch: str) -> bool:
    if FONT.startswith("Helvetica"):
        try:
            ch.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False
    face = pdfmetrics.getFont(FONT).face
    return ord(ch) in face.charToGlyph


def clean(text: str) -> str:
    """Zeichen, die die Schrift nicht kennt, ersetzen (statt schwarzer Kästchen im PDF)."""
    out = []
    for ch in str(text):
        if ch in "\n\t" or _font_has(ch):
            out.append(ch)
        else:
            out.append(_REPLACE.get(ch, "?"))
    return "".join(out)


def _p(text: str) -> str:
    return escape(clean(text))


# ---------------------------------------------------------------- Farben

INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")
LINE = colors.HexColor("#999999")
MARK = {
    "weak": (colors.HexColor("#d9480f"), colors.HexColor("#fff4e6")),
    "auto": (colors.HexColor("#2b8a3e"), colors.HexColor("#ebfbee")),
    "both": (colors.HexColor("#7048e8"), colors.HexColor("#f3f0ff")),
    "new": (colors.HexColor("#1971c2"), colors.HexColor("#e7f5ff")),
}


# ---------------------------------------------------------------- Diagramm

def _wrap(text: str, width: float, size: float) -> list[str]:
    words, lines, cur = clean(text).split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if stringWidth(cand, FONT, size) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _text_block(g: Group, text: str, cx: float, top: float, width: float, size: float, color=INK) -> None:
    """Zentrierter, umbrochener Text; top = y-Koordinate (reportlab, nach oben wachsend) der ersten Zeile."""
    for i, line in enumerate(_wrap(text, width, size)[:4]):
        g.add(String(cx, top - i * (size + 2), line, fontName=FONT, fontSize=size, fillColor=color, textAnchor="middle"))


def process_drawing(model: ProcessModel, marks: dict[str, str] | None = None) -> Drawing:
    """Prozessmodell als reportlab-Drawing (Koordinaten wie im BPMN-Layout)."""
    marks = marks or {}
    geo = layout_geometry(model)
    W, H = geo.width, geo.height
    d = Drawing(W, H)
    Y = lambda y: H - y  # noqa: E731 – BPMN-Koordinaten wachsen nach unten

    px, py, pw, ph = geo.pool
    d.add(Rect(px, Y(py + ph), pw, ph, strokeColor=INK, fillColor=None, strokeWidth=1.2))
    d.add(Line(px + 30, Y(py), px + 30, Y(py + ph), strokeColor=INK))
    g = Group(String(0, 0, clean(model.name), fontName=FONT_BOLD, fontSize=11, fillColor=INK, textAnchor="middle"))
    g.transform = (0, 1, -1, 0, px + 19, Y(py + ph / 2))
    d.add(g)
    for _, name, (x, y, w, h) in geo.lanes:
        d.add(Rect(x, Y(y + h), w, h, strokeColor=INK, fillColor=None, strokeWidth=0.8))
        d.add(Line(x + 30, Y(y), x + 30, Y(y + h), strokeColor=INK, strokeWidth=0.8))
        g = Group(String(0, 0, clean(name), fontName=FONT, fontSize=10, fillColor=INK, textAnchor="middle"))
        g.transform = (0, 1, -1, 0, x + 19, Y(y + h / 2))
        d.add(g)

    for f, pts, label in geo.edges:
        d.add(PolyLine([c for x, y in pts for c in (x, Y(y))], strokeColor=INK, strokeWidth=1))
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        dx, dy = (x2 - x1), (y2 - y1)
        n = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        ux, uy = dx / n, dy / n
        tip = (x2, Y(y2))
        base = (x2 - ux * 9, Y(y2 - uy * 9))
        perp = (-uy * 4, -ux * 4)
        d.add(Polygon([tip[0], tip[1], base[0] + perp[0], base[1] - perp[1], base[0] - perp[0], base[1] + perp[1]],
                      fillColor=INK, strokeColor=INK, strokeWidth=0.5))
        if label:
            lx, ly, lw, lh = label
            d.add(String(lx + lw / 2, Y(ly + lh - 3), clean(f.label), fontName=FONT, fontSize=9, fillColor=INK,
                         textAnchor="middle"))

    for n, (x, y, w, h), label in geo.nodes:
        m = marks.get(n.id, "")
        key = "both" if ("weak" in m and "auto" in m) else (m.split()[0] if m else "")
        stroke, fill = MARK.get(key, (INK, colors.white))
        sw = 2.2 if key else 1.2
        cx, cy = x + w / 2, Y(y + h / 2)
        if n.type in TASK_TYPES:
            d.add(Rect(x, Y(y + h), w, h, rx=8, ry=8, strokeColor=stroke, fillColor=fill, strokeWidth=sw))
            if n.type == NodeType.service_task:  # Zahnrad angedeutet
                d.add(Circle(x + 11, Y(y + 11), 5, strokeColor=INK, fillColor=None, strokeWidth=0.8))
                d.add(Circle(x + 11, Y(y + 11), 2, strokeColor=INK, fillColor=None, strokeWidth=0.8))
            elif n.type == NodeType.user_task:  # Person angedeutet
                d.add(Circle(x + 11, Y(y + 8), 3, strokeColor=INK, fillColor=None, strokeWidth=0.8))
                d.add(Rect(x + 6, Y(y + 18), 10, 6, rx=3, ry=3, strokeColor=INK, fillColor=None, strokeWidth=0.8))
            lines = _wrap(n.name, w - 12, 9)[:4]
            top = cy + (len(lines) - 1) * 5.5 - 3
            _text_block(d, n.name, cx, top, w - 12, 9)
        elif n.type in GATEWAY_TYPES:
            r = w / 2
            d.add(Polygon([cx, cy + r, cx + r, cy, cx, cy - r, cx - r, cy], strokeColor=stroke, fillColor=fill,
                          strokeWidth=sw))
            k = r * 0.38
            if n.type == NodeType.exclusive_gateway:
                d.add(Line(cx - k, cy - k, cx + k, cy + k, strokeColor=INK, strokeWidth=2.5))
                d.add(Line(cx - k, cy + k, cx + k, cy - k, strokeColor=INK, strokeWidth=2.5))
            else:
                d.add(Line(cx - k * 1.2, cy, cx + k * 1.2, cy, strokeColor=INK, strokeWidth=2.5))
                d.add(Line(cx, cy - k * 1.2, cx, cy + k * 1.2, strokeColor=INK, strokeWidth=2.5))
            if label:
                lx, ly, lw, lh = label
                _text_block(d, n.name, lx + lw / 2, Y(ly) - 9, lw + 20, 8.5)
        else:  # Ereignisse
            r = w / 2
            end = n.type == NodeType.end_event
            d.add(Circle(cx, cy, r, strokeColor=stroke, fillColor=fill, strokeWidth=3 if end else sw))
            _text_block(d, n.name, cx, cy - r - 10, 110, 8.5)
    return d


class ScaledDrawing(Flowable):
    """Drawing proportional in den verfügbaren Platz einpassen."""

    def __init__(self, drawing: Drawing, max_w: float, max_h: float):
        super().__init__()
        self.d = drawing
        self.scale = min(max_w / drawing.width, max_h / drawing.height, 1.0)
        self.width, self.height = drawing.width * self.scale, drawing.height * self.scale

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        self.canv.saveState()
        self.canv.scale(self.scale, self.scale)
        self.d.drawOn(self.canv, 0, 0)
        self.canv.restoreState()


LABEL_STRIP = POOL_X + 60  # Pool- und Lane-Beschriftung am linken Rand


class DrawingTile(Flowable):
    """Horizontaler Ausschnitt [x0, x1] eines Drawings, skaliert und beschnitten.

    Ab dem zweiten Abschnitt wird links die Pool-/Lane-Beschriftung wiederholt,
    damit jede Seite für sich lesbar bleibt.
    """

    def __init__(self, drawing: Drawing, x0: float, x1: float, scale: float):
        super().__init__()
        self.d, self.x0, self.x1, self.scale = drawing, x0, x1, scale
        self.strip = LABEL_STRIP if x0 > 0 else 0
        self.width = (x1 - x0 + self.strip) * scale
        self.height = drawing.height * scale

    def wrap(self, aw, ah):
        return self.width, self.height

    def _part(self, src_x: float, width: float, dst_x: float) -> None:
        c = self.canv
        c.saveState()
        p = c.beginPath()
        p.rect(dst_x * self.scale, 0, width * self.scale, self.height)
        c.clipPath(p, stroke=0, fill=0)
        c.scale(self.scale, self.scale)
        self.d.drawOn(c, dst_x - src_x, 0)
        c.restoreState()

    def draw(self):
        if self.strip:
            self._part(0, self.strip, 0)
        self._part(self.x0, self.x1 - self.x0, self.strip)


MIN_DIAGRAM_SCALE = 0.45  # darunter wird die Schrift zu klein: Diagramm wird auf Abschnitte verteilt


def diagram_flowables(drawing: Drawing, max_w: float, max_h: float, caption_style) -> list:
    """Diagramm einpassen; sehr breite Prozesse werden in lesbare Abschnitte (Teil 1/n …) geteilt."""
    W, H = drawing.width, drawing.height
    if min(max_w / W, max_h / H) >= MIN_DIAGRAM_SCALE:
        return [ScaledDrawing(drawing, max_w, max_h)]
    overlap = 80
    parts = 2
    while (W / parts + overlap + LABEL_STRIP) * MIN_DIAGRAM_SCALE * 1.15 > max_w and parts < 6:
        parts += 1
    seg = W / parts + overlap
    scale = min(max_w / (seg + LABEL_STRIP), max_h / H, 1.0)
    out = []
    for i in range(parts):
        x0 = 0.0 if i == 0 else max(LABEL_STRIP, i * W / parts - overlap / 2)
        x1 = min(W, x0 + seg)
        out.append(KeepTogether([Paragraph(f"Teil {i + 1}/{parts}", caption_style),
                                 DrawingTile(drawing, x0, x1, scale), Spacer(1, 8)]))
    return out


# ---------------------------------------------------------------- Bericht

KIND_DE = {"bottleneck": "Engpass", "handoff": "Übergabe", "waiting": "Wartezeit", "other": "Sonstiges"}


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["BodyText"], fontName=FONT, fontSize=9.5, leading=13, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("t", parent=base, fontName=FONT_BOLD, fontSize=20, leading=24, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=base, textColor=MUTED, fontSize=9),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=14, leading=18, spaceBefore=6, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=11, leading=14, spaceBefore=8, spaceAfter=3),
        "body": base,
        "small": ParagraphStyle("sm", parent=base, fontSize=8, leading=10.5, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base, fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cb", parent=base, fontName=FONT_BOLD, fontSize=8.5, leading=11),
    }


def _table(rows, col_widths, header=True):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f5")))
    t.setStyle(TableStyle(style))
    return t


def _footer(canv, doc):
    canv.saveState()
    canv.setFont(FONT, 7.5)
    canv.setFillColor(MUTED)
    w, _ = doc.pagesize
    canv.drawString(doc.leftMargin, 8 * mm, clean(f"Prozess Copilot · {doc.title}"))
    canv.drawRightString(w - doc.rightMargin, 8 * mm, f"Seite {doc.page}")
    canv.restoreState()


def analysis_marks(analysis: ProcessAnalysis | None) -> dict[str, str]:
    marks: dict[str, str] = {}
    if analysis:
        for w in analysis.weaknesses:
            for nid in w.node_ids:
                marks[nid] = "weak"
        for a in analysis.automations:
            marks[a.node_id] = (marks.get(a.node_id, "") + " auto").strip()
    return marks


def build_report(
    model: ProcessModel,
    *,
    description: str = "",
    analysis: ProcessAnalysis | None = None,
    tobe: ProcessModel | None = None,
    changes: list | None = None,
    comparison: Comparison | None = None,
    llm_model: str = "",
    attempts: int | None = None,
) -> bytes:
    """Erzeugt den PDF-Bericht. Abschnitte ohne Daten (z. B. ohne Analyse) werden weggelassen."""
    buf = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=14 * mm, title=clean(f"Prozessbericht: {model.name}"),
                            author="Prozess Copilot")
    st = _styles()
    aw = page[0] - doc.leftMargin - doc.rightMargin
    ah = page[1] - doc.topMargin - doc.bottomMargin
    names = {n.id: n.name for n in model.nodes}
    story: list = []

    # Titel und Ist-Prozess
    meta = [datetime.now().strftime("%d.%m.%Y %H:%M")]
    if llm_model:
        meta.append(f"Modell: {llm_model}")
    if attempts:
        meta.append(f"Valide nach {attempts} Versuch{'en' if attempts > 1 else ''}")
    story += [Paragraph(_p(f"Prozessbericht: {model.name}"), st["title"]), Paragraph(_p(" · ".join(meta)), st["sub"]),
              Spacer(1, 6)]
    if description:
        short = description if len(description) <= 900 else description[:900].rsplit(" ", 1)[0] + " …"
        story.append(Paragraph("<b>Beschreibung:</b> " + _p(short).replace("\n", "<br/>"), st["body"]))
        story.append(Spacer(1, 6))
    tasks = [n for n in model.nodes if n.type in TASK_TYPES]
    story.append(Paragraph(_p(
        f"{len(model.lanes)} Rollen · {len(tasks)} Aufgaben · "
        f"{sum(n.type in GATEWAY_TYPES for n in model.nodes)} Gateways · {len(model.flows)} Sequenzflüsse"), st["small"]))
    story.append(Spacer(1, 6))
    story += diagram_flowables(process_drawing(model, analysis_marks(analysis)), aw, ah * 0.62, st["small"])
    if analysis:
        story.append(Paragraph("Markierung: orange = Schwachstelle, grün = Automatisierungspotenzial, violett = beides.",
                               st["small"]))

    # Analyse
    if analysis:
        story += [PageBreak(), Paragraph("Analyse", st["h1"]), Paragraph(_p(analysis.summary), st["body"]),
                  Paragraph("Schwachstellen", st["h2"])]
        rows = [[Paragraph(h, st["cellb"]) for h in ("Schwachstelle", "Art", "Erläuterung", "Betroffene Elemente")]]
        for w in analysis.weaknesses:
            rows.append([Paragraph(_p(w.title), st["cellb"]), Paragraph(_p(KIND_DE.get(w.kind.value, w.kind.value)), st["cell"]),
                         Paragraph(_p(w.explanation), st["cell"]),
                         Paragraph(_p(", ".join(names.get(i, i) for i in w.node_ids)), st["cell"])])
        story.append(_table(rows, [aw * 0.2, aw * 0.1, aw * 0.45, aw * 0.25]))
        story.append(Paragraph("Automatisierungspotenziale", st["h2"]))
        rows = [[Paragraph(h, st["cellb"]) for h in ("Aufgabe", "Technologie", "Idee", "Nutzen", "Aufwand")]]
        for a in analysis.automations:
            rows.append([Paragraph(_p(names.get(a.node_id, a.node_id)), st["cellb"]),
                         Paragraph(_p(a.technology.value), st["cell"]), Paragraph(_p(a.idea), st["cell"]),
                         Paragraph(_p(a.benefit), st["cell"]), Paragraph(_p(a.effort), st["cell"])])
        story.append(_table(rows, [aw * 0.18, aw * 0.09, aw * 0.36, aw * 0.27, aw * 0.1]))

    # Soll-Prozess
    if tobe is not None:
        new_marks = {i: "new" for i in (comparison.new_ids if comparison else set())}
        story += [PageBreak(), Paragraph(_p(f"Soll-Prozess (To-Be): {tobe.name}"), st["h1"])]
        story += diagram_flowables(process_drawing(tobe, new_marks), aw, ah * 0.7, st["small"])
        story.append(Paragraph("Blau markiert: neu im Soll-Prozess.", st["small"]))
        block = []
        if changes:
            block.append(Paragraph("Wichtigste Änderungen", st["h2"]))
            rows = [[Paragraph(h, st["cellb"]) for h in ("Änderung", "Beschreibung", "Adressiert")]]
            for c in changes:
                rows.append([Paragraph(_p(c.title), st["cellb"]), Paragraph(_p(c.description), st["cell"]),
                             Paragraph(_p(c.addresses), st["cell"])])
            block.append(_table(rows, [aw * 0.22, aw * 0.5, aw * 0.28]))
        story += block
        if comparison:
            story.append(Paragraph("Kennzahlen Ist / Soll", st["h2"]))
            rows = [[Paragraph(h, st["cellb"]) for h in ("Kennzahl", "Ist", "Soll", "Veränderung")]]
            for m in comparison.metrics:
                trend = ""
                if m.delta and m.lower_is_better is not None:
                    trend = " (besser)" if (m.delta < 0) == m.lower_is_better else " (schlechter)"
                rows.append([Paragraph(_p(m.name), st["cell"]), str(m.ist), str(m.soll),
                             Paragraph(_p(f"{m.delta:+d}{trend}" if m.delta else "±0"), st["cell"])])
            story.append(KeepTogether(_table(rows, [aw * 0.45, aw * 0.15, aw * 0.15, aw * 0.25])))
            story.append(Paragraph("Kennzahlen werden deterministisch aus beiden Modellen berechnet.", st["small"]))
            rows = [[Paragraph("Neu im Soll-Prozess", st["cellb"]), Paragraph("Entfallen", st["cellb"])],
                    [Paragraph(_p("\n".join(comparison.added) or "–").replace("\n", "<br/>"), st["cell"]),
                     Paragraph(_p("\n".join(comparison.removed) or "–").replace("\n", "<br/>"), st["cell"])]]
            story += [Spacer(1, 8), _table(rows, [aw / 2, aw / 2])]

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
