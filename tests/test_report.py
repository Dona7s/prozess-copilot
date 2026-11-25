import io

from pypdf import PdfReader
from reportlab.lib.styles import getSampleStyleSheet

from copilot.compare import compare
from copilot.demo import EXAMPLE_TEXT, DemoClient
from copilot.llm import analyze_process, extract_process, propose_to_be
from copilot.report import MIN_DIAGRAM_SCALE, build_report, clean, diagram_flowables, process_drawing
from copilot.schema import ProcessModel


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_full_report_contains_all_sections():
    c = DemoClient()
    ist = extract_process(c, EXAMPLE_TEXT).model
    a = analyze_process(c, ist)
    t = propose_to_be(c, ist, a)
    pdf = build_report(ist, description=EXAMPLE_TEXT, analysis=a, tobe=t.model, changes=t.changes,
                       comparison=compare(ist, t.model), llm_model="demo", attempts=2)
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    for expected in ("Prozessbericht: Bestellabwicklung", "Lagerbestand prüfen", "Schwachstellen",
                     "Automatisierungspotenziale", "Soll-Prozess", "Bestand per ERP-API prüfen",
                     "Wichtigste Änderungen", "Kennzahlen Ist / Soll", "Seite 1"):
        assert expected in text, expected


def test_minimal_report_without_analysis():
    ist = extract_process(DemoClient(), EXAMPLE_TEXT).model
    text = _text(build_report(ist))
    assert "Bestellabwicklung" in text and "Schwachstellen" not in text and "Soll-Prozess" not in text


def test_wide_process_is_split_into_readable_parts():
    d = {"name": "Lang", "lanes": [{"id": "a", "name": "A"}],
         "nodes": [{"id": "s", "type": "startEvent", "name": "S", "lane": "a"}], "flows": []}
    prev = "s"
    for i in range(20):
        d["nodes"].append({"id": f"t{i}", "type": "task", "name": f"Schritt {i}", "lane": "a"})
        d["flows"].append({"id": f"f{i}", "source": prev, "target": f"t{i}"})
        prev = f"t{i}"
    d["nodes"].append({"id": "e", "type": "endEvent", "name": "E", "lane": "a"})
    d["flows"].append({"id": "fe", "source": prev, "target": "e"})
    drawing = process_drawing(ProcessModel.model_validate(d))
    parts = diagram_flowables(drawing, 800, 500, getSampleStyleSheet()["Normal"])
    assert len(parts) >= 3
    for p in parts:
        tile = p._content[1]
        assert tile.scale >= MIN_DIAGRAM_SCALE
    assert "Teil 1/" in _text(build_report(ProcessModel.model_validate(d)))


def test_unsupported_characters_are_replaced():
    assert clean("Kunde → Vertrieb ✅") == "Kunde -> Vertrieb ja"
    assert "ä" in clean("Prüfung äöüß")
