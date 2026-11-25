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


# ---------------------------------------------------------------- Ablation

def test_extract_json_text_handles_freeform_answers():
    from copilot.llm import extract_json_text

    assert extract_json_text('{"a": 1}') == '{"a": 1}'
    assert extract_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json_text('Hier ist das Modell:\n{"a": {"b": 2}}\nViel Erfolg!') == '{"a": {"b": 2}}'


def test_invalid_json_is_categorized_and_repaired():
    from copilot.demo import EXAMPLE_MODEL as M
    from copilot.llm import REPAIR_MARKER

    class Chatty(DemoClient):
        """Simuliert reines Prompting: erst kaputtes JSON, nach Reparatur korrekt mit Code-Fence."""

        def generate_json(self, system, prompt, schema):
            if REPAIR_MARKER not in prompt:
                return 'Gern! {"name": "x", "lanes": [ '
            return "```json\n" + json.dumps(M) + "\n```"

    r = evaluate_case(Chatty(), CASE)
    assert not r.first_try_valid and r.final_valid and r.attempts == 2
    assert categorize(r.first_errors[0]) == "JSON-Syntax"


def test_without_repair_uses_first_attempt():
    from copilot.evaluation import without_repair

    r = evaluate_case(DemoClient(), CASE)  # erster Versuch ungültig, zweiter gültig
    nr = without_repair(r, "schema")
    assert nr.mode == "schema" and nr.attempts == 1
    assert not nr.final_valid and nr.score is None
    ok = evaluate_case(DemoClient(simulate_repair=False), CASE)
    nr_ok = without_repair(ok, "schema")
    assert nr_ok.final_valid and nr_ok.score["total"] == 1.0


def test_ablation_report_and_cli(tmp_path):
    import sys

    from copilot.evaluation import ablation_report

    sys.path.insert(0, str(ROOT / "eval"))
    import run_eval

    assert run_eval.main(["--demo", "--ablation", "--cases", "bestellung", "--out", str(tmp_path)]) == 0
    data = json.loads(next(tmp_path.glob("*-ablation.json")).read_text(encoding="utf-8"))
    assert set(data["summaries"]) == {"schema+repair", "schema", "prompt+repair", "prompt"}
    assert data["summaries"]["schema"]["final_validity"] == 0  # Demo: erster Versuch fehlerhaft
    assert data["summaries"]["schema+repair"]["final_validity"] == 1
    md = ablation_report(data["summaries"], "demo", "heute", 1, 1)
    assert "Effekt der Reparaturschleife" in md and "Nur Prompt" in md


def test_gemini_client_freeform_config(monkeypatch):
    """Ohne Structured Output steht das Schema im System-Prompt und es wird kein response_schema gesetzt."""
    from copilot import llm
    from copilot.schema import ProcessModel

    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["config"] = config

            class R:
                text = "{}"
            return R()

    for structured in (True, False):
        c = llm.GeminiClient.__new__(llm.GeminiClient)
        c._client = type("C", (), {"models": FakeModels()})()
        c.model, c.temperature, c.structured = "m", 0.2, structured
        c.generate_json("SYS", "P", ProcessModel)
        cfg = captured["config"]
        if structured:
            assert cfg.response_json_schema and cfg.response_mime_type == "application/json"
        else:
            assert cfg.response_json_schema is None and cfg.response_mime_type is None
            assert '"lanes"' in cfg.system_instruction


def test_readme_from_saved_results(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(ROOT / "eval"))
    import run_eval

    readme = tmp_path / "README.md"
    readme.write_text("x\n<!-- EVAL:START -->\nalt\n<!-- EVAL:END -->\ny\n<!-- ABLATION:START -->\nalt\n<!-- ABLATION:END -->\n",
                      encoding="utf-8")
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)
    out = tmp_path / "res"
    ds = ["--dataset", str(ROOT / "eval" / "dataset.json"), "--cases", "bestellung", "--out", str(out)]
    run_eval.main(["--demo", *ds])
    run_eval.main(["--demo", "--ablation", *ds])
    for f in sorted(out.glob("*.json")):
        assert run_eval.main(["--readme-from", str(f)]) == 0
    text = readme.read_text(encoding="utf-8")
    assert "alt" not in text and "| bestellung |" in text and "Nur Prompt" in text
