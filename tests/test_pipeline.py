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


# Beispielmodelle aus echten Gemini-Läufen bzw. mit Schleife und Parallelität
URLAUB = {
 "name": "Urlaubsantragsprozess",
 "lanes": [{"id":"l_ma","name":"Mitarbeiter"},{"id":"l_tl","name":"Teamleiter"},{"id":"l_hr","name":"Personalabteilung"}],
 "nodes": [
  {"id":"s","type":"startEvent","name":"Urlaub geplant","lane":"l_ma"},
  {"id":"t1","type":"userTask","name":"Urlaubsantrag im Portal stellen","lane":"l_ma"},
  {"id":"t2","type":"userTask","name":"Urlaubsantrag prüfen","lane":"l_tl"},
  {"id":"g","type":"exclusiveGateway","name":"Antrag genehmigt?","lane":"l_tl"},
  {"id":"t3","type":"userTask","name":"Urlaub ins System eintragen","lane":"l_hr"},
  {"id":"t4","type":"userTask","name":"Bestätigung erhalten","lane":"l_ma"},
  {"id":"e1","type":"endEvent","name":"Urlaub genehmigt","lane":"l_ma"},
  {"id":"t5","type":"userTask","name":"Begründung per E-Mail erhalten","lane":"l_ma"},
  {"id":"e2","type":"endEvent","name":"Urlaub abgelehnt","lane":"l_ma"}],
 "flows": [
  {"id":"f1","source":"s","target":"t1"},{"id":"f2","source":"t1","target":"t2"},{"id":"f3","source":"t2","target":"g"},
  {"id":"f4","source":"g","target":"t3","label":"Ja"},{"id":"f5","source":"t3","target":"t4"},{"id":"f6","source":"t4","target":"e1"},
  {"id":"f7","source":"g","target":"t5","label":"Nein"},{"id":"f8","source":"t5","target":"e2"}]}
LOOP = {
 "name":"Rechnungsfreigabe",
 "lanes":[{"id":"a","name":"Buchhaltung"},{"id":"b","name":"Abteilungsleitung"}],
 "nodes":[
  {"id":"s","type":"startEvent","name":"Rechnung eingegangen","lane":"a"},
  {"id":"t1","type":"task","name":"Rechnung erfassen","lane":"a"},
  {"id":"t2","type":"userTask","name":"Rechnung prüfen","lane":"b"},
  {"id":"g","type":"exclusiveGateway","name":"Korrekt?","lane":"b"},
  {"id":"p1","type":"parallelGateway","name":"","lane":"a"},
  {"id":"t3","type":"serviceTask","name":"Zahlung anweisen","lane":"a"},
  {"id":"t4","type":"serviceTask","name":"Beleg archivieren","lane":"a"},
  {"id":"p2","type":"parallelGateway","name":"","lane":"a"},
  {"id":"e","type":"endEvent","name":"Erledigt","lane":"a"}],
 "flows":[
  {"id":"f1","source":"s","target":"t1"},{"id":"f2","source":"t1","target":"t2"},{"id":"f3","source":"t2","target":"g"},
  {"id":"f4","source":"g","target":"t1","label":"Nein"},{"id":"f5","source":"g","target":"p1","label":"Ja"},
  {"id":"f6","source":"p1","target":"t3"},{"id":"f7","source":"p1","target":"t4"},{"id":"f8","source":"t3","target":"p2"},
  {"id":"f9","source":"t4","target":"p2"},{"id":"f10","source":"p2","target":"e"}]}


def _geometry(model_dict):
    m = _model(model_dict)
    root = ET.fromstring(to_bpmn_xml(m))
    proc = root.find("bpmn:process", NS)
    flows = {f.get("id"): (f.get("sourceRef"), f.get("targetRef")) for f in proc.findall("bpmn:sequenceFlow", NS)}
    node_ids = {n.id for n in m.nodes}
    rects, edges = {}, {}
    for sh in root.findall(".//bpmndi:BPMNShape", NS):
        el = sh.get("bpmnElement")
        if el in node_ids:
            b = sh.find("dc:Bounds", NS)
            x, y, w, h = (float(b.get(k)) for k in ("x", "y", "width", "height"))
            rects[el] = (x, y, x + w, y + h)
    for ed in root.findall(".//bpmndi:BPMNEdge", NS):
        pts = [(float(p.get("x")), float(p.get("y"))) for p in ed.findall("di:waypoint", NS)]
        edges[ed.get("bpmnElement")] = pts
    return flows, rects, edges


import pytest


@pytest.mark.parametrize("model_dict", [URLAUB, LOOP, EXAMPLE_MODEL], ids=["urlaub", "schleife", "demo"])
def test_edges_do_not_cross_shapes_or_overlap(model_dict):
    from copilot.bpmn import _collinear_overlap, _seg_hits_rect

    flows, rects, edges = _geometry(model_dict)
    for fid, pts in edges.items():
        src, tgt = flows[fid]
        segs = list(zip(pts, pts[1:]))
        # alle Abschnitte orthogonal
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in segs), fid
        for nid, rect in rects.items():
            if nid in (src, tgt):
                continue
            assert not any(_seg_hits_rect(s, rect, pad=0) for s in segs), f"{fid} kreuzt {nid}"
        for oid, opts in edges.items():
            osrc, otgt = flows[oid]
            if oid == fid or osrc == src or otgt == tgt:
                continue
            assert not any(_collinear_overlap(s, o) for s in segs for o in zip(opts, opts[1:])), f"{fid} überdeckt {oid}"
