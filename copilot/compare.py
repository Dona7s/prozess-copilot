"""Deterministischer Vergleich von Ist- und Soll-Prozess.

Ergänzt die vom LLM formulierte Änderungsübersicht um nachprüfbare Fakten:
welche Elemente neu sind oder entfallen und wie sich einfache Kennzahlen ändern.
"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import TASK_TYPES, NodeType, ProcessModel


@dataclass
class Metric:
    name: str
    ist: int
    soll: int
    lower_is_better: bool | None = True  # None = neutral

    @property
    def delta(self) -> int:
        return self.soll - self.ist


@dataclass
class Comparison:
    added: list[str]  # Namen neuer Elemente im Soll-Prozess
    removed: list[str]  # Namen entfallener Elemente des Ist-Prozesses
    new_ids: set[str]  # IDs neuer Elemente (für die Hervorhebung)
    metrics: list[Metric]


def _key(n) -> str:
    return n.name.strip().lower()


def _metrics(m: ProcessModel) -> dict[str, int]:
    lane = {n.id: n.lane for n in m.nodes}
    auto = {n.id for n in m.nodes if n.type == NodeType.service_task}
    tasks = [n for n in m.nodes if n.type in TASK_TYPES]
    return {
        "Aufgaben gesamt": len(tasks),
        "Manuelle Aufgaben": sum(n.type != NodeType.service_task for n in tasks),
        "Automatisierte Aufgaben (serviceTask)": sum(n.type == NodeType.service_task for n in tasks),
        # Lane-Wechsel zwischen menschlich bearbeiteten Schritten; Übergaben an/von
        # automatisierten Schritten verursachen keine Liegezeit und zählen nicht.
        "Manuelle Übergaben zwischen Rollen": sum(
            1 for f in m.flows
            if f.source in lane and f.target in lane and lane[f.source] != lane[f.target]
            and f.source not in auto and f.target not in auto
        ),
        "Beteiligte Rollen": len({n.lane for n in m.nodes}),
    }


def compare(ist: ProcessModel, soll: ProcessModel) -> Comparison:
    ist_ids = {n.id for n in ist.nodes}
    ist_names = {_key(n) for n in ist.nodes}
    soll_ids = {n.id for n in soll.nodes}
    soll_names = {_key(n) for n in soll.nodes}

    relevant = TASK_TYPES | {NodeType.exclusive_gateway, NodeType.parallel_gateway, NodeType.end_event}
    added = [n for n in soll.nodes if n.type in relevant and n.id not in ist_ids and _key(n) not in ist_names]
    removed = [n for n in ist.nodes if n.type in relevant and n.id not in soll_ids and _key(n) not in soll_names]

    mi, ms = _metrics(ist), _metrics(soll)
    direction = {
        "Aufgaben gesamt": None,
        "Manuelle Aufgaben": True,
        "Automatisierte Aufgaben (serviceTask)": False,
        "Manuelle Übergaben zwischen Rollen": True,
        "Beteiligte Rollen": None,
    }
    metrics = [Metric(k, mi[k], ms[k], lower_is_better=direction[k]) for k in mi]
    return Comparison(
        added=[n.name for n in added],
        removed=[n.name for n in removed],
        new_ids={n.id for n in added},
        metrics=metrics,
    )
