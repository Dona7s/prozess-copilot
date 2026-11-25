import copy
import xml.etree.ElementTree as ET

from copilot.bpmn import NS, to_bpmn_xml
from copilot.demo import EXAMPLE_MODEL, EXAMPLE_TEXT, DemoClient
from copilot.llm import analyze_process, extract_process
from copilot.schema import ProcessModel
from copilot.validate import validate


def _model(d=None):
    return ProcessModel.model_validate(d or EXAMPLE_MODEL)


def test_example_is_valid():
    assert validate(_model()) == []


def test_repair_loop_fixes_broken_first_attempt():
    res = extract_process(DemoClient(), EXAMPLE_TEXT)
    assert res.ok
    assert len(res.attempts) == 2
    assert res.attempts[0]["errors"]  # erster Versuch war fehlerhaft
    assert any("task_notify" in e for e in res.attempts[0]["errors"])


def test_repair_loop_gives_up():
    class AlwaysBroken(DemoClient):
        def generate_json(self, system, prompt, schema):
            return '{"name": "x", "lanes": [], "nodes": [], "flows": []}'

    res = extract_process(AlwaysBroken(), "x", max_repairs=2)
    assert not res.ok and len(res.attempts) == 3


def test_schema_error_is_reported_not_raised():
    class Garbage(DemoClient):
        def generate_json(self, system, prompt, schema):
            return '{"name": 1}'

    res = extract_process(Garbage(), "x", max_repairs=0)
    assert res.model is None and res.errors


def test_validator_catches_common_problems():
    d = copy.deepcopy(EXAMPLE_MODEL)
    d["nodes"].append({"id": "orphan", "type": "task", "name": "Verwaist", "lane": "lane_sales"})
    d["nodes"].append({"id": "bad_lane", "type": "task", "name": "X", "lane": "nope"})
    d["flows"].append({"id": "f8", "source": "gw_in_stock", "target": "end_notified", "label": ""})
    d["flows"].append({"id": "f9", "source": "ghost", "target": "task_ship", "label": ""})
    errs = " | ".join(validate(_model(d)))
    assert "orphan" in errs
    assert "nope" in errs
    assert "f8" in errs  # XOR-Bedingung fehlt
    assert "ghost" in errs


def test_validator_detects_missing_start_end_and_dead_loop():
    d = {
        "name": "loop",
        "lanes": [{"id": "l", "name": "L"}],
        "nodes": [
            {"id": "s", "type": "startEvent", "name": "S", "lane": "l"},
            {"id": "a", "type": "task", "name": "A", "lane": "l"},
            {"id": "b", "type": "task", "name": "B", "lane": "l"},
        ],
        "flows": [
            {"id": "f1", "source": "s", "target": "a"},
            {"id": "f2", "source": "a", "target": "b"},
            {"id": "f3", "source": "b", "target": "a"},
        ],
    }
    errs = validate(_model(d))
    assert any("Kein endEvent" in e for e in errs)


def test_bpmn_xml_structure():
    m = _model()
    xml = to_bpmn_xml(m)
    root = ET.fromstring(xml)
    proc = root.find("bpmn:process", NS)
    assert proc is not None
    assert len(proc.findall("bpmn:sequenceFlow", NS)) == len(m.flows)
    assert len(proc.findall("bpmn:laneSet/bpmn:lane", NS)) == len(m.lanes)
    assert proc.find("bpmn:exclusiveGateway", NS).get("name") == "Ware vorrätig?"
    shapes = root.findall(".//bpmndi:BPMNShape", NS)
    edges = root.findall(".//bpmndi:BPMNEdge", NS)
    # pool + lanes + nodes
    assert len(shapes) == 1 + len(m.lanes) + len(m.nodes)
    assert len(edges) == len(m.flows)
    for e in edges:
        assert len(e.findall("di:waypoint", NS)) >= 2


def test_bpmn_handles_loops_and_odd_ids():
    d = {
        "name": "Freigabe",
        "lanes": [{"id": "lane 1", "name": "Mitarbeitende"}, {"id": "2lane", "name": "Führungskraft"}],
        "nodes": [
            {"id": "s", "type": "startEvent", "name": "S", "lane": "lane 1"},
            {"id": "write", "type": "task", "name": "Antrag schreiben", "lane": "lane 1"},
            {"id": "review", "type": "task", "name": "Antrag prüfen", "lane": "2lane"},
            {"id": "gw", "type": "exclusiveGateway", "name": "Genehmigt?", "lane": "2lane"},
            {"id": "e", "type": "endEvent", "name": "E", "lane": "2lane"},
        ],
        "flows": [
            {"id": "f1", "source": "s", "target": "write"},
            {"id": "f2", "source": "write", "target": "review"},
            {"id": "f3", "source": "review", "target": "gw"},
            {"id": "f4", "source": "gw", "target": "e", "label": "Ja"},
            {"id": "f5", "source": "gw", "target": "write", "label": "Nein"},
        ],
    }
    m = _model(d)
    assert validate(m) == []
    root = ET.fromstring(to_bpmn_xml(m))
    lane_ids = [l.get("id") for l in root.findall(".//bpmn:lane", NS)]
    assert lane_ids == ["lane_1", "_2lane"]


def test_analysis_drops_unknown_ids():
    class Halluc(DemoClient):
        def generate_json(self, system, prompt, schema):
            import json
            from copilot.demo import EXAMPLE_ANALYSIS
            a = copy.deepcopy(EXAMPLE_ANALYSIS)
            a["automations"].append({"node_id": "nope", "technology": "AI", "idea": "x", "benefit": "y", "effort": "niedrig"})
            a["weaknesses"][0]["node_ids"].append("nope")
            return json.dumps(a)

    a = analyze_process(Halluc(), _model())
    assert all(x.node_id != "nope" for x in a.automations)
    assert "nope" not in a.weaknesses[0].node_ids


def test_viewer_escapes_script_end_tag():
    from copilot.viewer import viewer_html

    html = viewer_html("<x>a</script><b/></x>")
    body = html.split("<script>", 2)[-1]
    assert body.count("</script>") == 1  # nur das echte schließende Tag
