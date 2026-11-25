import json
from pathlib import Path

from copilot.demo import EXAMPLE_MODEL, DemoClient
from copilot.evaluation import (
    categorize,
    evaluate_case,
    markdown_report,
    name_matches,
    score_structure,
    summarize,
)
from copilot.schema import ProcessModel

ROOT = Path(__file__).resolve().parent.parent
CASE = {
    "id": "bestellung",
    "text": "x",
    "expected": {
        "lanes": ["Kunde", "Vertrieb", "Lager"],
        "activities": ["Bestellung aufgeben", "Lagerbestand prüfen", "Ware versenden", "Kunde benachrichtigen"],
        "decisions": 1,
        "parallel": 0,
    },
}


def test_dataset_is_well_formed():
    cases = json.loads((ROOT / "eval" / "dataset.json").read_text(encoding="utf-8"))["cases"]
    assert 10 <= len(cases) <= 15
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        e = c["expected"]
        assert c["text"] and e["lanes"] and e["activities"]
        assert isinstance(e["decisions"], int) and isinstance(e["parallel"], int)


def test_fuzzy_name_matching():
    assert name_matches("Lagerbestand prüfen", "Bestand im Lager prüfen")
    assert name_matches("Personalabteilung|HR", "HR")
    assert name_matches("Kunde benachrichtigen", "Kunden benachrichtigen")
    assert not name_matches("Rechnung freigeben", "Ware versenden")


def test_score_perfect_model():
    s = score_structure(ProcessModel.model_validate(EXAMPLE_MODEL), CASE["expected"])
    assert s.lane_f1 == 1.0 and s.activity_recall == 1.0
    assert s.decisions_ok and s.parallel_ok and s.total == 1.0


def test_score_detects_wrong_structure():
    exp = dict(CASE["expected"], decisions=2, parallel=1, lanes=["Kunde", "Vertrieb", "Lager", "Buchhaltung"])
    s = score_structure(ProcessModel.model_validate(EXAMPLE_MODEL), exp)
    assert s.lane_recall == 0.75 and not s.decisions_ok and not s.parallel_ok
    assert s.total < 0.6


def test_evaluate_case_and_report_with_demo_client():
    r = evaluate_case(DemoClient(), CASE)
    assert not r.first_try_valid and r.final_valid and r.attempts == 2  # Demo repariert einmal
    assert r.score["total"] == 1.0
    summary = summarize([r])
    assert summary["first_try_validity"] == 0 and summary["final_validity"] == 1
    assert summary["repaired_share"] == 1
    md = markdown_report([r], summary, "demo", "heute")
    assert "| bestellung |" in md and "Struktur gesamt" in md


def test_api_errors_do_not_abort_evaluation():
    class Broken:
        def generate_json(self, *a):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    r = evaluate_case(Broken(), CASE)
    assert not r.final_valid and "429" in r.exception
    assert summarize([r])["api_errors"] == 1


def test_error_categories():
    assert categorize("Aufgabe 'x' (X) hat keinen ausgehenden Fluss. Sie muss mit ... einem endEvent verbunden sein.") \
        == "Aufgabe ohne Ein-/Ausgang"
    assert categorize("Kein endEvent vorhanden.") == "Start/Ende"
    assert categorize("JSON entspricht nicht dem Schema: ('nodes',): Field required") == "Schema"
    assert categorize("Ausgehende Flüsse von XOR-Gateway 'g' haben keine Bedingung: f1.") == "XOR ohne Bedingung"


def test_cli_demo_run(tmp_path):
    import sys

    sys.path.insert(0, str(ROOT / "eval"))
    import run_eval

    assert run_eval.main(["--demo", "--cases", "bestellung", "--out", str(tmp_path)]) == 0
    assert list(tmp_path.glob("*.json")) and list(tmp_path.glob("*.md"))
