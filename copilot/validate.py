"""Graphbasierte Prüfung des Prozessmodells.

Rückgabe: Liste von Fehlern (leere Liste = Modell ist korrekt).
Die Fehlertexte werden an das Modell zurückgegeben und sind daher kurz und präzise.
"""
from __future__ import annotations

from collections import defaultdict, deque

from .schema import GATEWAY_TYPES, TASK_TYPES, NodeType, ProcessModel


def validate(model: ProcessModel) -> list[str]:
    errors: list[str] = []

    # 1. Eindeutige IDs
    seen: set[str] = set()
    for kind, items in (("Lane", model.lanes), ("Element", model.nodes), ("Fluss", model.flows)):
        for item in items:
            if not item.id.strip():
                errors.append(f"Es gibt ein(e) {kind} mit leerer ID.")
            elif item.id in seen:
                errors.append(f"Die ID '{item.id}' wird mehrfach verwendet. Jede ID muss eindeutig sein.")
            seen.add(item.id)

    if not model.lanes:
        errors.append("Es muss mindestens eine Lane (Rolle) geben.")

    lane_ids = {l.id for l in model.lanes}
    nodes = {n.id: n for n in model.nodes}

    # 2. Referenzen
    for n in model.nodes:
        if n.lane not in lane_ids:
            errors.append(f"Element '{n.id}' verweist auf die nicht existierende Lane '{n.lane}'.")
    valid_flows = []
    for f in model.flows:
        ok = True
        if f.source not in nodes:
            errors.append(f"Quelle '{f.source}' von Fluss '{f.id}' existiert nicht.")
            ok = False
        if f.target not in nodes:
            errors.append(f"Ziel '{f.target}' von Fluss '{f.id}' existiert nicht.")
            ok = False
        if ok and f.source == f.target:
            errors.append(f"Fluss '{f.id}' verbindet '{f.source}' mit sich selbst.")
        if ok:
            valid_flows.append(f)

    incoming: dict[str, list] = defaultdict(list)
    outgoing: dict[str, list] = defaultdict(list)
    for f in valid_flows:
        outgoing[f.source].append(f)
        incoming[f.target].append(f)

    starts = [n for n in model.nodes if n.type == NodeType.start_event]
    ends = [n for n in model.nodes if n.type == NodeType.end_event]
    if not starts:
        errors.append("Kein startEvent vorhanden. Es wird mindestens ein Startereignis benötigt.")
    if not ends:
        errors.append("Kein endEvent vorhanden. Es wird mindestens ein Endereignis benötigt.")

    # 3. Verbindungsregeln je Elementtyp
    for n in model.nodes:
        i, o = len(incoming[n.id]), len(outgoing[n.id])
        if n.type == NodeType.start_event:
            if i:
                errors.append(f"startEvent '{n.id}' darf keine eingehenden Flüsse haben.")
            if o != 1:
                errors.append(f"startEvent '{n.id}' muss genau 1 ausgehenden Fluss haben (aktuell {o}).")
        elif n.type == NodeType.end_event:
            if o:
                errors.append(f"endEvent '{n.id}' darf keine ausgehenden Flüsse haben.")
            if i < 1:
                errors.append(f"endEvent '{n.id}' hat keinen eingehenden Fluss.")
        elif n.type in TASK_TYPES:
            if i < 1:
                errors.append(f"Aufgabe '{n.id}' ({n.name}) hat keinen eingehenden Fluss.")
            if o < 1:
                errors.append(f"Aufgabe '{n.id}' ({n.name}) hat keinen ausgehenden Fluss. Sie muss mit dem nächsten Schritt oder einem endEvent verbunden sein.")
            if o > 1:
                errors.append(f"Aufgabe '{n.id}' hat {o} ausgehende Flüsse. Für Verzweigungen ein Gateway verwenden.")
        elif n.type in GATEWAY_TYPES:
            if i < 1 or o < 1:
                errors.append(f"Gateway '{n.id}' braucht mindestens 1 eingehenden und 1 ausgehenden Fluss (aktuell {i}/{o}).")
            elif i == 1 and o == 1:
                errors.append(f"Gateway '{n.id}' verzweigt und vereint nichts (1 Eingang, 1 Ausgang). Entfernen oder Zweig ergänzen.")
            if n.type == NodeType.exclusive_gateway and o > 1:
                unlabeled = [f.id for f in outgoing[n.id] if not f.label.strip()]
                if unlabeled:
                    errors.append(f"Ausgehende Flüsse von XOR-Gateway '{n.id}' haben keine Bedingung: {', '.join(unlabeled)}.")

    # 4. Erreichbarkeit: jedes Element vom Start erreichbar und führt zu einem Ende
    if starts and ends:
        fwd = _reach([s.id for s in starts], outgoing, "target")
        bwd = _reach([e.id for e in ends], incoming, "source")
        for n in model.nodes:
            if n.id not in fwd:
                errors.append(f"'{n.id}' ({n.name}) ist vom startEvent aus nicht erreichbar.")
            elif n.id not in bwd:
                errors.append(f"Von '{n.id}' ({n.name}) aus ist kein endEvent erreichbar (Fluss bricht ab oder Endlosschleife).")

    # doppelte Meldungen entfernen
    return list(dict.fromkeys(errors))


def _reach(roots: list[str], edges: dict, attr: str) -> set[str]:
    seen = set(roots)
    q = deque(roots)
    while q:
        cur = q.popleft()
        for f in edges[cur]:
            nxt = getattr(f, attr)
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen
