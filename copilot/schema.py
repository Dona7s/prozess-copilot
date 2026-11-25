"""JSON-Schema für das Prozessmodell (Pydantic).

Gemini antwortet genau nach diesem Schema (Structured Output); die Antwort
wird anschließend über dieselben Modelle streng geprüft.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    start_event = "startEvent"
    end_event = "endEvent"
    task = "task"
    user_task = "userTask"
    service_task = "serviceTask"
    exclusive_gateway = "exclusiveGateway"  # XOR: „wenn … dann … sonst“
    parallel_gateway = "parallelGateway"  # AND: gleichzeitig


TASK_TYPES = {NodeType.task, NodeType.user_task, NodeType.service_task}
GATEWAY_TYPES = {NodeType.exclusive_gateway, NodeType.parallel_gateway}


class Lane(BaseModel):
    id: str = Field(description="Kurzer Bezeichner (ASCII), z. B. 'lane_vertrieb'")
    name: str = Field(description="Name der Rolle oder Abteilung, z. B. 'Vertrieb'")


class Node(BaseModel):
    id: str = Field(description="Eindeutiger Bezeichner, z. B. 'task_lager_pruefen'")
    type: NodeType
    name: str = Field(description="Kurzer Name. Aufgaben als Objekt + Verb: 'Lagerbestand prüfen'. Gateways als Frage: 'Ware vorrätig?'")
    lane: str = Field(description="ID der Lane, zu der dieses Element gehört")


class Flow(BaseModel):
    id: str = Field(description="Eindeutiger Bezeichner, z. B. 'flow_1'")
    source: str = Field(description="ID des Quellelements")
    target: str = Field(description="ID des Zielelements")
    label: str = Field(default="", description="Bedingung an ausgehenden Gateway-Flüssen, z. B. 'Ja' oder 'Nein'. Sonst leer.")


class ProcessModel(BaseModel):
    name: str = Field(description="Name des Prozesses")
    lanes: list[Lane]
    nodes: list[Node]
    flows: list[Flow]


# ---------------------------------------------------------------- Analyse

class IssueKind(str, Enum):
    bottleneck = "bottleneck"  # Engpass
    handoff = "handoff"  # unnötige Übergabe
    waiting = "waiting"  # Wartezeit
    other = "other"


class AutomationTech(str, Enum):
    rpa = "RPA"
    api = "API"
    ai = "AI"


class Weakness(BaseModel):
    kind: IssueKind
    node_ids: list[str] = Field(description="IDs der betroffenen Elemente")
    title: str
    explanation: str


class Automation(BaseModel):
    node_id: str = Field(description="ID der zu automatisierenden Aufgabe")
    technology: AutomationTech
    idea: str = Field(description="Was und wie automatisiert wird")
    benefit: str = Field(description="Erwarteter Nutzen (Zeit, Fehler, Kosten)")
    effort: str = Field(description="Geschätzter Aufwand: niedrig / mittel / hoch")


class ProcessAnalysis(BaseModel):
    summary: str = Field(description="Kurzes Fazit zum Prozess in 2–3 Sätzen")
    weaknesses: list[Weakness]
    automations: list[Automation]


# ---------------------------------------------------------------- Soll-Prozess (To-Be)

class Change(BaseModel):
    title: str = Field(description="Kurzer Titel der Änderung")
    description: str = Field(description="Was wurde geändert und warum")
    addresses: str = Field(default="", description="Welche Schwachstelle oder welches Automatisierungspotenzial damit adressiert wird")


class ToBeProposal(BaseModel):
    changes: list[Change] = Field(description="Die wichtigsten Änderungen gegenüber dem Ist-Prozess (3–6 Punkte)")
    process: ProcessModel = Field(description="Der vollständige verbesserte Soll-Prozess")


# ---------------------------------------------------------------- Bearbeitung per Chat

class EditProposal(BaseModel):
    summary: str = Field(description="Kurze Beschreibung der vorgenommenen Änderung (1–2 Sätze). "
                                     "Falls die Anweisung unklar oder nicht umsetzbar war: Begründung, Modell unverändert.")
    process: ProcessModel = Field(description="Das vollständige Prozessmodell nach der Änderung")
