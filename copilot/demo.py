"""Demo-Client, der ohne API-Schlüssel funktioniert.

Liefert für den Beispieltext vorbereitete Antworten. Die erste Antwort
enthält absichtlich einen Fehler (eine nicht weiterverbundene Aufgabe),
damit die Reparaturschleife sichtbar wird. Auch die Tests nutzen diesen Client.
"""
from __future__ import annotations

import copy
import json

from .llm import REPAIR_MARKER
from .schema import ProcessAnalysis, ProcessModel, ToBeProposal

EXAMPLE_TEXT = (
    "Der Kunde gibt eine Bestellung auf. Der Vertrieb prüft den Lagerbestand. "
    "Ist die Ware vorrätig, versendet das Lager sie, andernfalls wird der Kunde benachrichtigt."
)

EXAMPLE_MODEL = {
    "name": "Bestellabwicklung",
    "lanes": [
        {"id": "lane_customer", "name": "Kunde"},
        {"id": "lane_sales", "name": "Vertrieb"},
        {"id": "lane_warehouse", "name": "Lager"},
    ],
    "nodes": [
        {"id": "start", "type": "startEvent", "name": "Bedarf entstanden", "lane": "lane_customer"},
        {"id": "task_order", "type": "userTask", "name": "Bestellung aufgeben", "lane": "lane_customer"},
        {"id": "task_check_stock", "type": "userTask", "name": "Lagerbestand prüfen", "lane": "lane_sales"},
        {"id": "gw_in_stock", "type": "exclusiveGateway", "name": "Ware vorrätig?", "lane": "lane_sales"},
        {"id": "task_ship", "type": "userTask", "name": "Ware versenden", "lane": "lane_warehouse"},
        {"id": "task_notify", "type": "userTask", "name": "Kunden benachrichtigen", "lane": "lane_sales"},
        {"id": "end_shipped", "type": "endEvent", "name": "Bestellung versendet", "lane": "lane_warehouse"},
        {"id": "end_notified", "type": "endEvent", "name": "Kunde informiert", "lane": "lane_sales"},
    ],
    "flows": [
        {"id": "f1", "source": "start", "target": "task_order", "label": ""},
        {"id": "f2", "source": "task_order", "target": "task_check_stock", "label": ""},
        {"id": "f3", "source": "task_check_stock", "target": "gw_in_stock", "label": ""},
        {"id": "f4", "source": "gw_in_stock", "target": "task_ship", "label": "Ja"},
        {"id": "f5", "source": "gw_in_stock", "target": "task_notify", "label": "Nein"},
        {"id": "f6", "source": "task_ship", "target": "end_shipped", "label": ""},
        {"id": "f7", "source": "task_notify", "target": "end_notified", "label": ""},
    ],
}

EXAMPLE_ANALYSIS = {
    "summary": (
        "Der Prozess ist kurz, enthält aber zwei Übergaben zwischen drei Rollen, und der Lagerbestand "
        "wird manuell geprüft. Ist die Ware nicht vorrätig, wird der Kunde nur informiert – ohne Alternativangebot."
    ),
    "weaknesses": [
        {
            "kind": "waiting",
            "node_ids": ["task_check_stock"],
            "title": "Manuelle Bestandsprüfung",
            "explanation": "Der Vertrieb fragt beim Lager nach und wartet auf Antwort. Das verzögert jede einzelne Bestellung.",
        },
        {
            "kind": "handoff",
            "node_ids": ["task_order", "task_check_stock", "task_ship"],
            "title": "Übergaben Kunde → Vertrieb → Lager",
            "explanation": "Jeder Lane-Wechsel erhöht das Risiko von Informationsverlust und Wartezeiten in Warteschlangen.",
        },
        {
            "kind": "other",
            "node_ids": ["task_notify", "end_notified"],
            "title": "Prozess endet ohne Alternative, wenn Ware fehlt",
            "explanation": "Es werden weder Alternativprodukte, Vorbestellung noch Liefertermin angeboten – der Umsatz geht verloren.",
        },
    ],
    "automations": [
        {
            "node_id": "task_check_stock",
            "technology": "API",
            "idea": "Bei Bestelleingang den Bestand automatisch aus dem ERP/WMS abfragen.",
            "benefit": "Prüfung dauert Sekunden statt Stunden, manuelle Fehler entfallen.",
            "effort": "mittel",
        },
        {
            "node_id": "task_notify",
            "technology": "AI",
            "idea": "Ein LLM formuliert eine persönliche Nachricht und schlägt ähnliche verfügbare Produkte vor.",
            "benefit": "Ein Teil des verlorenen Umsatzes wird zurückgewonnen, Mitarbeitende sparen Zeit.",
            "effort": "niedrig",
        },
        {
            "node_id": "task_order",
            "technology": "RPA",
            "idea": "Ein Bot überträgt Bestellungen aus E-Mail oder Formular automatisch ins System.",
            "benefit": "Keine Abtippfehler mehr, die Bestellung ist sofort in der Bearbeitung.",
            "effort": "niedrig",
        },
    ],
}


EXAMPLE_TOBE = {
    "changes": [
        {
            "title": "Bestandsprüfung per ERP-API",
            "description": "Der Lagerbestand wird beim Bestelleingang automatisch abgefragt, statt manuell im Vertrieb geprüft zu werden.",
            "addresses": "Wartezeit: manuelle Bestandsprüfung",
        },
        {
            "title": "Bestellungen automatisch erfassen",
            "description": "Bestellungen aus E-Mail und Webformular werden per RPA direkt ins System übernommen.",
            "addresses": "Übergabe Kunde → Vertrieb",
        },
        {
            "title": "Alternativangebot statt Absage",
            "description": "Ist die Ware nicht vorrätig, erstellt eine KI ein Alternativangebot; der Kunde entscheidet, ob er es annimmt.",
            "addresses": "Prozess endet ohne Alternative",
        },
    ],
    "process": {
        "name": "Bestellabwicklung (Soll)",
        "lanes": [
            {"id": "lane_customer", "name": "Kunde"},
            {"id": "lane_system", "name": "ERP / Automatisierung"},
            {"id": "lane_warehouse", "name": "Lager"},
        ],
        "nodes": [
            {"id": "start", "type": "startEvent", "name": "Bedarf entstanden", "lane": "lane_customer"},
            {"id": "task_order", "type": "userTask", "name": "Bestellung aufgeben", "lane": "lane_customer"},
            {"id": "task_capture", "type": "serviceTask", "name": "Bestellung automatisch erfassen (RPA)", "lane": "lane_system"},
            {"id": "task_check_api", "type": "serviceTask", "name": "Bestand per ERP-API prüfen", "lane": "lane_system"},
            {"id": "gw_in_stock", "type": "exclusiveGateway", "name": "Ware vorrätig?", "lane": "lane_system"},
            {"id": "task_ship", "type": "userTask", "name": "Ware versenden", "lane": "lane_warehouse"},
            {"id": "end_shipped", "type": "endEvent", "name": "Bestellung versendet", "lane": "lane_warehouse"},
            {"id": "task_offer", "type": "serviceTask", "name": "Alternativangebot erstellen (KI)", "lane": "lane_system"},
            {"id": "task_decide", "type": "userTask", "name": "Alternativangebot prüfen", "lane": "lane_customer"},
            {"id": "gw_accept", "type": "exclusiveGateway", "name": "Angebot angenommen?", "lane": "lane_customer"},
            {"id": "end_declined", "type": "endEvent", "name": "Bestellung storniert", "lane": "lane_customer"},
        ],
        "flows": [
            {"id": "f1", "source": "start", "target": "task_order", "label": ""},
            {"id": "f2", "source": "task_order", "target": "task_capture", "label": ""},
            {"id": "f3", "source": "task_capture", "target": "task_check_api", "label": ""},
            {"id": "f4", "source": "task_check_api", "target": "gw_in_stock", "label": ""},
            {"id": "f5", "source": "gw_in_stock", "target": "task_ship", "label": "Ja"},
            {"id": "f6", "source": "task_ship", "target": "end_shipped", "label": ""},
            {"id": "f7", "source": "gw_in_stock", "target": "task_offer", "label": "Nein"},
            {"id": "f8", "source": "task_offer", "target": "task_decide", "label": ""},
            {"id": "f9", "source": "task_decide", "target": "gw_accept", "label": ""},
            {"id": "f10", "source": "gw_accept", "target": "task_ship", "label": "Ja"},
            {"id": "f11", "source": "gw_accept", "target": "end_declined", "label": "Nein"},
        ],
    },
}


def _broken_first_attempt() -> dict:
    m = copy.deepcopy(EXAMPLE_MODEL)
    # ausgehender Fluss von „Kunden benachrichtigen“ fehlt
    m["flows"] = [f for f in m["flows"] if f["id"] != "f7"]
    return m


class DemoClient:
    """Gleiche Schnittstelle wie GeminiClient, aber ohne Netzwerkzugriff."""

    model = "demo"

    def __init__(self, simulate_repair: bool = True):
        self._simulate_repair = simulate_repair

    def generate_json(self, system: str, prompt: str, schema) -> str:
        if schema is ProcessAnalysis:
            return json.dumps(EXAMPLE_ANALYSIS, ensure_ascii=False)
        if schema is ToBeProposal:
            return json.dumps(EXAMPLE_TOBE, ensure_ascii=False)
        if schema is ProcessModel:
            if self._simulate_repair and REPAIR_MARKER not in prompt:
                return json.dumps(_broken_first_attempt(), ensure_ascii=False)
            return json.dumps(EXAMPLE_MODEL, ensure_ascii=False)
        raise ValueError(schema)
