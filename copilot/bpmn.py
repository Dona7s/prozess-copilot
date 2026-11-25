"""JSON (ProcessModel) → BPMN 2.0 XML.

Diesen Schritt übernimmt kein LLM, sondern deterministischer Python-Code:
Das Ergebnis ist dadurch immer standardkonformes BPMN 2.0. Die Diagramm-
koordinaten (BPMNDI) werden ebenfalls hier automatisch berechnet, sodass
die Datei sofort sauber in bpmn-js, Camunda Modeler und Signavio öffnet.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from .schema import GATEWAY_TYPES, NodeType, ProcessModel

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


# ---- Abmessungen (Pixel)
SIZE = {
    "event": (36, 36),
    "gateway": (50, 50),
    "task": (110, 80),
}
COL_W = 170  # Abstand zwischen Spalten
ROW_H = 130  # Zeilenhöhe innerhalb einer Lane
POOL_X, POOL_Y = 40, 40
POOL_LABEL_W = 30
LANE_LABEL_W = 30
LEFT_PAD = 70


def _size(t: NodeType) -> tuple[int, int]:
    if t in (NodeType.start_event, NodeType.end_event):
        return SIZE["event"]
    if t in GATEWAY_TYPES:
        return SIZE["gateway"]
    return SIZE["task"]


_NCNAME = re.compile(r"[^A-Za-z0-9_.-]")


def safe_id(raw: str) -> str:
    s = _NCNAME.sub("_", raw.strip()) or "id"
    if not (s[0].isalpha() or s[0] == "_"):
        s = "_" + s
    return s


# ------------------------------------------------------------------ layout

def compute_layout(model: ProcessModel) -> dict[str, tuple[int, int]]:
    """Liefert für jedes Element (Spalte, Zeile innerhalb der Lane)."""
    node_ids = [n.id for n in model.nodes]
    out = defaultdict(list)
    for f in model.flows:
        out[f.source].append(f.target)

    # Rückwärtskanten (Schleifen) per DFS erkennen
    starts = [n.id for n in model.nodes if n.type == NodeType.start_event]
    order = starts + [i for i in node_ids if i not in starts]
    state: dict[str, int] = {}
    back: set[tuple[str, str]] = set()

    def dfs(u: str) -> None:
        state[u] = 1
        for v in out[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2

    for r in order:
        if r not in state:
            dfs(r)

    # Spalte = längster Pfad (topologische Ordnung auf dem DAG)
    indeg = {i: 0 for i in node_ids}
    for u in node_ids:
        for v in out[u]:
            if (u, v) not in back and v in indeg:
                indeg[v] += 1
    col = {i: 0 for i in node_ids}
    queue = [i for i in order if indeg[i] == 0]
    while queue:
        u = queue.pop(0)
        for v in out[u]:
            if (u, v) in back or v not in indeg:
                continue
            col[v] = max(col[v], col[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)

    # Zeilen je Lane + Spalte verteilen
    lane_of = {n.id: n.lane for n in model.nodes}
    used: dict[tuple[str, int], int] = defaultdict(int)
    pos = {}
    for i in sorted(node_ids, key=lambda x: (col[x], order.index(x))):
        key = (lane_of[i], col[i])
        pos[i] = (col[i], used[key])
        used[key] += 1
    return pos


# ------------------------------------------------------------------ XML

def to_bpmn_xml(model: ProcessModel) -> str:
    ids = {}

    def sid(raw: str) -> str:
        if raw not in ids:
            cand = safe_id(raw)
            while cand in ids.values():
                cand += "_x"
            ids[raw] = cand
        return ids[raw]

    proc_id = "Process_1"
    defs = ET.Element(
        _q("bpmn", "definitions"),
        {
            "id": "Definitions_1",
            "targetNamespace": "http://bpmn.io/schema/bpmn",
            "exporter": "Prozess Copilot",
            "exporterVersion": "0.1",
        },
    )
    collab = ET.SubElement(defs, _q("bpmn", "collaboration"), {"id": "Collaboration_1"})
    ET.SubElement(
        collab,
        _q("bpmn", "participant"),
        {"id": "Participant_1", "name": model.name, "processRef": proc_id},
    )
    proc = ET.SubElement(defs, _q("bpmn", "process"), {"id": proc_id, "name": model.name, "isExecutable": "false"})

    # lanes
    lane_set = ET.SubElement(proc, _q("bpmn", "laneSet"), {"id": "LaneSet_1"})
    for lane in model.lanes:
        el = ET.SubElement(lane_set, _q("bpmn", "lane"), {"id": sid(lane.id), "name": lane.name})
        for n in model.nodes:
            if n.lane == lane.id:
                ET.SubElement(el, _q("bpmn", "flowNodeRef")).text = sid(n.id)

    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for f in model.flows:
        outgoing[f.source].append(sid(f.id))
        incoming[f.target].append(sid(f.id))

    for n in model.nodes:
        el = ET.SubElement(proc, _q("bpmn", n.type.value), {"id": sid(n.id), "name": n.name})
        for fid in incoming[n.id]:
            ET.SubElement(el, _q("bpmn", "incoming")).text = fid
        for fid in outgoing[n.id]:
            ET.SubElement(el, _q("bpmn", "outgoing")).text = fid

    for f in model.flows:
        attrs = {"id": sid(f.id), "sourceRef": sid(f.source), "targetRef": sid(f.target)}
        if f.label:
            attrs["name"] = f.label
        ET.SubElement(proc, _q("bpmn", "sequenceFlow"), attrs)

    _add_diagram(defs, model, sid)
    ET.indent(defs, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(defs, encoding="unicode")


def _add_diagram(defs: ET.Element, model: ProcessModel, sid) -> None:
    pos = compute_layout(model)
    max_col = max((c for c, _ in pos.values()), default=0)

    # Lane-Höhen
    rows_per_lane = defaultdict(lambda: 1)
    for n in model.nodes:
        rows_per_lane[n.lane] = max(rows_per_lane[n.lane], pos[n.id][1] + 1)

    lane_x = POOL_X + POOL_LABEL_W
    lane_w = LANE_LABEL_W + LEFT_PAD + (max_col + 1) * COL_W
    lane_y = {}
    y = POOL_Y
    for lane in model.lanes:
        lane_y[lane.id] = y
        y += rows_per_lane[lane.id] * ROW_H
    pool_h = y - POOL_Y

    diagram = ET.SubElement(defs, _q("bpmndi", "BPMNDiagram"), {"id": "BPMNDiagram_1"})
    plane = ET.SubElement(diagram, _q("bpmndi", "BPMNPlane"), {"id": "BPMNPlane_1", "bpmnElement": "Collaboration_1"})

    def shape(elem_id: str, x: float, y: float, w: float, h: float, extra: dict | None = None) -> None:
        attrs = {"id": f"{elem_id}_di", "bpmnElement": elem_id}
        attrs.update(extra or {})
        s = ET.SubElement(plane, _q("bpmndi", "BPMNShape"), attrs)
        ET.SubElement(s, _q("dc", "Bounds"), {"x": _n(x), "y": _n(y), "width": _n(w), "height": _n(h)})

    shape("Participant_1", POOL_X, POOL_Y, POOL_LABEL_W + lane_w, pool_h, {"isHorizontal": "true"})
    for lane in model.lanes:
        shape(sid(lane.id), lane_x, lane_y[lane.id], lane_w, rows_per_lane[lane.id] * ROW_H, {"isHorizontal": "true"})

    box = {}
    for n in model.nodes:
        c, r = pos[n.id]
        w, h = _size(n.type)
        cx = lane_x + LANE_LABEL_W + LEFT_PAD + c * COL_W
        cy = lane_y[n.lane] + r * ROW_H + ROW_H / 2
        box[n.id] = (cx, cy, w, h)
        extra = {"isMarkerVisible": "true"} if n.type == NodeType.exclusive_gateway else None
        shape(sid(n.id), cx - w / 2, cy - h / 2, w, h, extra)
        if n.type in GATEWAY_TYPES and n.name:
            # Gateway-Beschriftung oberhalb, damit sie nicht mit dem nach unten gehenden Fluss kollidiert
            lab = ET.SubElement(plane[-1], _q("bpmndi", "BPMNLabel"))
            ET.SubElement(lab, _q("dc", "Bounds"), {"x": _n(cx - 50), "y": _n(cy - h / 2 - 30), "width": "100", "height": "27"})

    for f in model.flows:
        if f.source not in box or f.target not in box:
            continue
        pts = _route(box[f.source], box[f.target])
        e = ET.SubElement(plane, _q("bpmndi", "BPMNEdge"), {"id": f"{sid(f.id)}_di", "bpmnElement": sid(f.id)})
        for px, py in pts:
            ET.SubElement(e, _q("di", "waypoint"), {"x": _n(px), "y": _n(py)})
        if f.label:
            if len(pts) == 3:  # vertikal-horizontal: nach dem Knick
                lx, ly = pts[1][0] + 10, pts[1][1] - 20
            else:
                lx, ly = pts[0][0] + 8, pts[0][1] - 20
            lab = ET.SubElement(e, _q("bpmndi", "BPMNLabel"))
            ET.SubElement(lab, _q("dc", "Bounds"), {"x": _n(lx), "y": _n(ly), "width": "60", "height": "14"})


def _route(src, tgt) -> list[tuple[float, float]]:
    sx, sy, sw, sh = src
    tx, ty, tw, th = tgt
    if tx > sx:  # vorwärts
        a = (sx + sw / 2, sy)
        b = (tx - tw / 2, ty)
        if abs(sy - ty) < 1:
            return [a, b]
        # erst vertikal, dann horizontal (übersichtlicher bei Gateway-Zweigen)
        a = (sx, sy + (sh / 2 if ty > sy else -sh / 2))
        return [a, (sx, ty), b]
    # rückwärts (Schleife): unten herum führen
    bottom = max(sy + sh / 2, ty + th / 2) + 25
    return [(sx, sy + sh / 2), (sx, bottom), (tx, bottom), (tx, ty + th / 2)]


def _n(v: float) -> str:
    return str(int(round(v)))
