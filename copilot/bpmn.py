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
COL_W = 200  # Abstand zwischen Spalten
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

    # Zeilen je Lane + Spalte verteilen: ein Element übernimmt nach Möglichkeit
    # die Zeile seines Vorgängers in derselben Lane, damit Zweige gerade bleiben.
    lane_of = {n.id: n.lane for n in model.nodes}
    preds = defaultdict(list)
    for f in model.flows:
        preds[f.target].append(f.source)
    taken: dict[tuple[str, int], set[int]] = defaultdict(set)
    pos: dict[str, tuple[int, int]] = {}
    for i in sorted(node_ids, key=lambda x: (col[x], order.index(x))):
        key = (lane_of[i], col[i])
        pref = next((pos[p][1] for p in preds[i] if p in pos and lane_of[p] == lane_of[i]), 0)
        row = pref
        while row in taken[key]:
            row += 1
        taken[key].add(row)
        pos[i] = (col[i], row)
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

    def col_x(c: int) -> float:
        return lane_x + LANE_LABEL_W + LEFT_PAD + c * COL_W

    box = {}
    obstacles: list[tuple[str, tuple[float, float, float, float]]] = []
    row_bottom = {}
    for n in model.nodes:
        c, r = pos[n.id]
        w, h = _size(n.type)
        cx = col_x(c)
        cy = lane_y[n.lane] + r * ROW_H + ROW_H / 2
        box[n.id] = (cx, cy, w, h)
        row_bottom[n.id] = cy + ROW_H / 2
        obstacles.append((n.id, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
        extra = {"isMarkerVisible": "true"} if n.type == NodeType.exclusive_gateway else None
        shape(sid(n.id), cx - w / 2, cy - h / 2, w, h, extra)
        if n.type in GATEWAY_TYPES and n.name:
            # Gateway-Beschriftung oberhalb; ausgehende Flüsse verlassen das Gateway seitlich
            lx, ly = cx - 50, cy - h / 2 - 32
            lab = ET.SubElement(plane[-1], _q("bpmndi", "BPMNLabel"))
            ET.SubElement(lab, _q("dc", "Bounds"), {"x": _n(lx), "y": _n(ly), "width": "100", "height": "27"})
            obstacles.append((n.id + "#label", (lx, ly, lx + 100, ly + 27)))

    router = _Router(obstacles)
    for f in model.flows:
        if f.source not in box or f.target not in box:
            continue
        pts = router.route(f, box, pos, col_x, row_bottom)
        e = ET.SubElement(plane, _q("bpmndi", "BPMNEdge"), {"id": f"{sid(f.id)}_di", "bpmnElement": sid(f.id)})
        for px, py in pts:
            ET.SubElement(e, _q("di", "waypoint"), {"x": _n(px), "y": _n(py)})
        if f.label:
            lx, ly = _label_pos(pts)
            lab = ET.SubElement(e, _q("bpmndi", "BPMNLabel"))
            ET.SubElement(lab, _q("dc", "Bounds"), {"x": _n(lx), "y": _n(ly), "width": "30", "height": "14"})


def _label_pos(pts) -> tuple[float, float]:
    """Position der Bedingung (z. B. „Ja“/„Nein“) so, dass sie eindeutig an ihrem Zweig steht."""
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    if abs(y1 - y2) < 0.5:  # endet horizontal
        if len(pts) >= 3:  # links neben dem letzten vertikalen Abschnitt, oberhalb der Einmündung
            return x1 - 34, y2 - 18
        return pts[0][0] + 6, pts[0][1] - 18  # gerade Linie: direkt hinter der Quelle
    return pts[0][0] + 4, pts[0][1] + 4  # Rückwärtsfluss: neben dem Start


Seg = tuple[tuple[float, float], tuple[float, float]]


class _Router:
    """Orthogonales Routing der Sequenzflüsse.

    Vertikale Abschnitte verlaufen nur in den freien Kanälen zwischen den Spalten.
    Für jeden Fluss werden mehrere Kandidaten erzeugt und bewertet: Kreuzen von
    Elementen ist am teuersten, deckungsgleiche Abschnitte mit anderen Flüssen
    (die man nicht mehr auseinanderhalten kann) ebenfalls, danach zählen Knicke.
    """

    CHANNEL_OFFSETS = (18, 30, 42, 54)

    def __init__(self, obstacles):
        self.obstacles = obstacles
        self.done: list[tuple[str, str, list[Seg]]] = []

    def route(self, f, box, pos, col_x, row_bottom) -> list[tuple[float, float]]:
        sx, sy, sw, sh = box[f.source]
        tx, ty, tw, th = box[f.target]
        sc, tc = pos[f.source][0], pos[f.target][0]
        a, b = (sx + sw / 2, sy), (tx - tw / 2, ty)
        cands: list[list[tuple[float, float]]] = []
        if tc > sc:  # vorwärts
            if abs(sy - ty) < 1:
                cands.append([a, b])
            for gap in dict.fromkeys((tc, sc + 1)):  # Kanal vor dem Ziel / nach der Quelle
                for off in self.CHANNEL_OFFSETS:
                    x = col_x(gap) - SIZE["task"][0] / 2 - off
                    cands.append([a, (x, sy), (x, ty), b])
            # Umweg entlang des unteren Zeilenrands (wenn die direkte Linie blockiert ist)
            for yd in (row_bottom[f.source] - 10, row_bottom[f.target] - 10):
                for off in self.CHANNEL_OFFSETS[:2]:
                    x1 = col_x(sc + 1) - SIZE["task"][0] / 2 - off
                    x2 = col_x(tc) - SIZE["task"][0] / 2 - off
                    cands.append([a, (x1, sy), (x1, yd), (x2, yd), (x2, ty), b])
        else:  # rückwärts (Schleife): unten herum
            yb = max(row_bottom[f.source], row_bottom[f.target]) - 10
            for d in (0, -8, 8):
                cands.append([(sx + d, sy + sh / 2), (sx + d, yb), (tx + d, yb), (tx + d, ty + th / 2)])
                yb2 = yb - 6
                cands.append([(sx + d, sy + sh / 2), (sx + d, yb2), (tx + d, yb2), (tx + d, ty + th / 2)])

        best = min(cands, key=lambda pts: self._cost(pts, f))
        best = _simplify(best)
        self.done.append((f.source, f.target, _segments(best)))
        return best

    def _cost(self, pts, f) -> float:
        segs = _segments(pts)
        hits = 0
        for oid, rect in self.obstacles:
            if oid in (f.source, f.target):
                continue
            hits += sum(_seg_hits_rect(s, rect) for s in segs)
        overlaps = 0
        for src, tgt, other in self.done:
            if src == f.source or tgt == f.target:
                continue  # gemeinsamer Start/Ziel: Zusammenlaufen ist normal
            overlaps += sum(_collinear_overlap(s, o) for s in segs for o in other)
        return hits * 1000 + overlaps * 100 + len(segs)


def _segments(pts) -> list[Seg]:
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def _simplify(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) < 0.5 and abs(p[1] - out[-1][1]) < 0.5:
            continue
        out.append(p)
    # kollineare Zwischenpunkte entfernen
    res = [out[0]]
    for i in range(1, len(out) - 1):
        (x0, y0), (x1, y1), (x2, y2) = res[-1], out[i], out[i + 1]
        if (abs(x0 - x1) < 0.5 and abs(x1 - x2) < 0.5) or (abs(y0 - y1) < 0.5 and abs(y1 - y2) < 0.5):
            continue
        res.append(out[i])
    res.append(out[-1])
    return res


def _seg_hits_rect(seg: Seg, rect, pad: float = 3) -> bool:
    (x1, y1), (x2, y2) = seg
    rx1, ry1, rx2, ry2 = rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    return lo_x < rx2 and hi_x > rx1 and lo_y < ry2 and hi_y > ry1


def _collinear_overlap(s: Seg, o: Seg, tol: float = 4) -> bool:
    (ax1, ay1), (ax2, ay2) = s
    (bx1, by1), (bx2, by2) = o
    if abs(ax1 - ax2) < 0.5 and abs(bx1 - bx2) < 0.5 and abs(ax1 - bx1) < tol:  # beide vertikal
        return min(max(ay1, ay2), max(by1, by2)) - max(min(ay1, ay2), min(by1, by2)) > tol
    if abs(ay1 - ay2) < 0.5 and abs(by1 - by2) < 0.5 and abs(ay1 - by1) < tol:  # beide horizontal
        return min(max(ax1, ax2), max(bx1, bx2)) - max(min(ax1, ax2), min(bx1, bx2)) > tol
    return False


def _n(v: float) -> str:
    return str(int(round(v)))
