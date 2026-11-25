"""Gemini-API-Aufrufe: Text → JSON (mit Reparaturschleife) und Prozessanalyse."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from .schema import Change, EditProposal, ProcessAnalysis, ProcessModel, ToBeProposal
from .validate import validate

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

EXTRACT_SYSTEM = """Du bist Experte für Geschäftsprozessmodellierung (BPMN 2.0).
Der Nutzer beschreibt einen Prozess in natürlicher Sprache. Deine Aufgabe: die Prozessstruktur als JSON extrahieren.

Regeln:
- lanes: jede Rolle oder Abteilung (Kunde, Vertrieb, Lager …) ist eine eigene Lane.
- nodes:
  * genau ein startEvent (Prozessbeginn) und mindestens ein endEvent.
  * Aufgaben (task / userTask / serviceTask) als Objekt + Verb benennen: „Bestellung aufgeben“, „Lagerbestand prüfen“.
    Von Menschen ausgeführt → userTask, automatisch durch ein System → serviceTask, unklar → task.
  * Entscheidungspunkte („wenn … sonst …“) → exclusiveGateway, als Frage benannt: „Ware vorrätig?“.
  * Parallel ausgeführte Tätigkeiten → parallelGateway (je ein Gateway zum Öffnen und zum Schließen).
  * Jedes Element gehört in die Lane der Rolle, die es ausführt.
- flows: Reihenfolge zwischen den Elementen. Aus einer Aufgabe geht genau ein Fluss heraus; Verzweigungen nur über Gateways.
  Jeder ausgehende Fluss eines exclusiveGateway erhält ein label (Bedingung): „Ja“ / „Nein“ usw.
- Jeder Pfad muss letztlich in einem endEvent enden. Keine unverbundenen Elemente.
- IDs bestehen nur aus ASCII-Buchstaben, Ziffern und Unterstrich und sind eindeutig.
- Benenne Elemente in der Sprache des Nutzers. Erfinde nichts, was nicht im Text steht – ergänze nur, was nötig ist, damit der Prozess logisch geschlossen ist.
"""

REPAIR_MARKER = "Gefundene Fehler:"

REPAIR_TEMPLATE = """Das Prozessmodell aus deiner letzten Antwort hat die Prüfung nicht bestanden.

Ursprüngliche Aufgabe:
{task}

Bisheriges JSON:
{prev}

""" + REPAIR_MARKER + """
{errors}

Behebe alle Fehler und gib das vollständige JSON erneut aus. Korrekte Teile bitte unverändert lassen."""

ANALYSIS_SYSTEM = """Du bist Berater für Prozessoptimierung und Automatisierung (Lean, BPM, RPA).
Du erhältst ein BPMN-Prozessmodell als JSON. Analysiere es:
1. weaknesses – Schwachstellen:
   - bottleneck: Engpässe, an denen sich Arbeit bei einer Rolle oder Aufgabe staut; unnötig sequenzielle Schritte.
   - handoff: unnötige Übergaben zwischen Rollen (Lanes); jeder Lane-Wechsel ist eine Quelle für Verzögerung und Fehler.
   - waiting: Stellen, an denen auf Freigabe, Antwort oder Informationen gewartet wird.
   - other: Nacharbeitsschleifen, ungeklärte Fälle usw.
2. automations – Automatisierungspotenziale, jeweils einer konkreten Aufgabe (node_id) zugeordnet:
   - RPA: manuelles Übertragen strukturierter Daten zwischen Systemen, repetitive Bildschirmarbeit.
   - API: direkte Systemintegration (z. B. automatische Bestandsabfrage).
   - AI: Texte/Dokumente verstehen, klassifizieren, Antworten formulieren, Entscheidungen unterstützen.
Verweise nur auf IDs, die im Modell existieren. Sei konkret und praxisnah. Antworte in der Sprache des Nutzers."""


TOBE_SYSTEM = """Du bist Berater für Prozessoptimierung (Lean, BPM, Automatisierung) und modellierst in BPMN 2.0.
Du erhältst einen Ist-Prozess als JSON sowie eine Analyse mit Schwachstellen und Automatisierungspotenzialen.
Entwirf daraus einen realistischen, verbesserten Soll-Prozess (To-Be):
- Behebe die genannten Schwachstellen (z. B. Sackgassen schließen, unnötige Übergaben vermeiden, Wartezeiten verkürzen).
- Setze sinnvolle Automatisierungen um: automatisierte Schritte werden serviceTask, z. B. „Bestand per ERP-API prüfen“.
  Technologie (RPA/API/KI) gern im Namen kenntlich machen.
- Unabhängige Schritte dürfen mit parallelGateway parallelisiert werden.
- Behalte bestehende IDs für unveränderte Elemente bei; neue Elemente erhalten neue IDs.
- Es gelten dieselben Modellierungsregeln wie beim Ist-Prozess: genau ein startEvent, jeder Pfad endet in einem endEvent,
  aus Aufgaben genau ein ausgehender Fluss, Verzweigungen nur über Gateways, XOR-Ausgänge mit Bedingung.
- Erfinde keine Rollen oder Systeme ohne Not; der Soll-Prozess soll umsetzbar bleiben.
Fasse in changes die wichtigsten Änderungen zusammen. Antworte in der Sprache des Nutzers."""


EDIT_SYSTEM = """Du bearbeitest ein bestehendes BPMN-Prozessmodell (JSON) nach einer Anweisung des Nutzers.
- Setze genau die gewünschte Änderung um (z. B. Schritt einfügen, entfernen, umbenennen, Rolle ändern,
  Entscheidung oder Parallelität ergänzen) und passe die Flüsse so an, dass der Prozess schlüssig bleibt.
- Alles andere bleibt unverändert: gleiche IDs, Namen, Lanes und Reihenfolge für nicht betroffene Elemente.
- Neue Elemente erhalten neue, eindeutige IDs. Neue Rollen werden als neue Lane angelegt.
- Es gelten die bekannten Modellierungsregeln: genau ein startEvent, jeder Pfad endet in einem endEvent,
  aus Aufgaben genau ein ausgehender Fluss, Verzweigungen nur über Gateways, XOR-Ausgänge mit Bedingung.
- Ist die Anweisung unklar oder fachlich nicht sinnvoll, gib das Modell unverändert zurück und erkläre in summary, warum.
Antworte in der Sprache des Nutzers."""


class LLMError(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    model: ProcessModel | None
    errors: list[str]
    attempts: list[dict] = field(default_factory=list)  # je Versuch: {"json": ..., "errors": [...]}

    @property
    def ok(self) -> bool:
        return self.model is not None and not self.errors


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, temperature: float = 0.2):
        from google import genai  # später Import: Bibliothek nur bei Bedarf laden

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY nicht gefunden.")
        self._client = genai.Client(api_key=key)
        self.model = model
        self.temperature = temperature

    def generate_json(self, system: str, prompt: str, schema: type[BaseModel]) -> str:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.temperature,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
            ),
        )
        if not resp.text:
            raise LLMError("Das Modell hat eine leere Antwort geliefert.")
        return resp.text


def _generate_validated(client, system: str, prompt: str, task: str, schema: type[BaseModel],
                        get_process, max_repairs: int):
    """Generiert schema-gebundenes JSON und repariert es, bis das Prozessmodell gültig ist.

    get_process: holt das ProcessModel aus dem geparsten Schema-Objekt.
    Rückgabe: (letztes geparstes Objekt oder None, ExtractionResult)
    """
    attempts: list[dict] = []
    parsed = None
    model: ProcessModel | None = None
    errors: list[str] = []
    for _ in range(max_repairs + 1):
        raw = client.generate_json(system, prompt, schema)
        try:
            parsed = schema.model_validate_json(raw)
            model = get_process(parsed)
            errors = validate(model)
        except ValidationError as e:
            parsed, model = None, None
            errors = [f"JSON entspricht nicht dem Schema: {err['loc']}: {err['msg']}" for err in e.errors()]
        attempts.append({"json": raw, "errors": errors})
        if model is not None and not errors:
            break
        prompt = REPAIR_TEMPLATE.format(task=task, prev=raw, errors="\n".join(f"- {e}" for e in errors))
    return parsed, ExtractionResult(model=model, errors=errors, attempts=attempts)


def extract_process(client, text: str, max_repairs: int = 3) -> ExtractionResult:
    """Text → ProcessModel. Bei Validierungsfehlern wird das Modell zur Korrektur aufgefordert."""
    prompt = f"Prozessbeschreibung:\n\"\"\"{text}\"\"\""
    _, result = _generate_validated(client, EXTRACT_SYSTEM, prompt, prompt, ProcessModel, lambda m: m, max_repairs)
    return result


def _model_json(model: ProcessModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)


def analyze_process(client, model: ProcessModel, text: str = "") -> ProcessAnalysis:
    prompt = (
        (f"Ursprüngliche Beschreibung:\n\"\"\"{text}\"\"\"\n\n" if text else "")
        + "Prozessmodell (JSON):\n"
        + _model_json(model)
    )
    raw = client.generate_json(ANALYSIS_SYSTEM, prompt, ProcessAnalysis)
    analysis = ProcessAnalysis.model_validate_json(raw)
    # Verweise auf nicht existierende IDs (Halluzinationen) entfernen
    ids = {n.id for n in model.nodes}
    for w in analysis.weaknesses:
        w.node_ids = [i for i in w.node_ids if i in ids]
    analysis.automations = [a for a in analysis.automations if a.node_id in ids]
    return analysis


@dataclass
class ToBeResult(ExtractionResult):
    changes: list[Change] = field(default_factory=list)


def propose_to_be(client, model: ProcessModel, analysis: ProcessAnalysis, text: str = "",
                  max_repairs: int = 3) -> ToBeResult:
    """Ist-Prozess + Analyse → verbesserter Soll-Prozess (gleiches Schema, gleiche Validierung)."""
    prompt = (
        (f"Ursprüngliche Beschreibung:\n\"\"\"{text}\"\"\"\n\n" if text else "")
        + "Ist-Prozess (JSON):\n" + _model_json(model)
        + "\n\nAnalyse (JSON):\n" + json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )
    task = "Entwirf einen verbesserten Soll-Prozess auf Basis von Ist-Prozess und Analyse.\n\n" + prompt
    parsed, res = _generate_validated(client, TOBE_SYSTEM, prompt, task, ToBeProposal, lambda p: p.process, max_repairs)
    return ToBeResult(model=res.model, errors=res.errors, attempts=res.attempts,
                      changes=parsed.changes if parsed else [])


@dataclass
class EditResult(ExtractionResult):
    summary: str = ""


def edit_process(client, model: ProcessModel, instruction: str, max_repairs: int = 3) -> EditResult:
    """Bestehendes Modell per natürlichsprachlicher Anweisung ändern (validiert, mit Reparaturschleife)."""
    prompt = f"Aktuelles Prozessmodell (JSON):\n{_model_json(model)}\n\nAnweisung:\n\"\"\"{instruction}\"\"\""
    parsed, res = _generate_validated(client, EDIT_SYSTEM, prompt, prompt, EditProposal, lambda p: p.process, max_repairs)
    return EditResult(model=res.model, errors=res.errors, attempts=res.attempts,
                      summary=parsed.summary if parsed else "")
