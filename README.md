# Prozess Copilot

**Von der Prozessbeschreibung in natürlicher Sprache zum validen BPMN-2.0-Diagramm – inklusive Schwachstellenanalyse, Automatisierungsvorschlägen und verbessertem Soll-Prozess.**

Fachabteilungen beschreiben Abläufe meist in Fließtext. Prozess Copilot übersetzt solche Beschreibungen mithilfe eines Large Language Models (Google Gemini) in ein strukturiertes Prozessmodell, prüft es automatisch, erzeugt daraus standardkonformes BPMN 2.0, analysiert Schwachstellen und entwirft einen verbesserten Soll-Prozess (To-Be) – ebenfalls als BPMN.

![Generiertes BPMN-Diagramm](docs/diagramm.png)

*Eingabe:* „Der Mitarbeiter stellt einen Urlaubsantrag im Portal. Der Teamleiter prüft den Antrag. Wenn er genehmigt wird, trägt die Personalabteilung den Urlaub ins System ein und der Mitarbeiter erhält eine Bestätigung. Wenn er abgelehnt wird, bekommt der Mitarbeiter eine Begründung per E-Mail.“

In der Anwendung werden nach der Analyse zusätzlich Schwachstellen (orange), Automatisierungspotenziale (grün) und beides (violett) farbig markiert.

---

## Funktionsweise

```mermaid
flowchart LR
    A[Prozessbeschreibung<br/>Freitext] --> B[Gemini<br/>Structured Output]
    B --> C{Validierung}
    C -- Fehler --> B
    C -- ok --> D[JSON → BPMN 2.0 XML<br/>Python]
    D --> E[bpmn-js Ansicht<br/>.bpmn-Download]
    D --> F[Gemini<br/>Prozessanalyse]
    F --> E
    F --> G[Gemini<br/>Soll-Prozess]
    G --> C2{Validierung}
    C2 -- Fehler --> G
    C2 -- ok --> H[Soll-BPMN<br/>+ Ist/Soll-Vergleich]
```

| # | Schritt | Umsetzung | Code |
|---|---------|-----------|------|
| 1 | **Eingabe** – Prozess in eigenen Worten beschreiben | Streamlit | `app.py` |
| 2 | **Text → JSON** – Rollen (Lanes), Aufgaben, Gateways und Sequenzflüsse extrahieren | Gemini mit JSON-Schema (Structured Output), Schema als Pydantic-Modell | `copilot/llm.py`, `copilot/schema.py` |
| 3 | **Validierung & Reparaturschleife** – Start/Ende vorhanden? Unverbundene Aufgaben? Erreicht jeder Pfad ein Ende? Bedingungen an XOR-Gateways? Bei Fehlern erhält das Modell die Fehlerliste und korrigiert (Standard: bis zu 3 Versuche) | Python (Graphanalyse) | `copilot/validate.py` |
| 4 | **JSON → BPMN 2.0 XML** inkl. automatischem Layout (BPMNDI) | Deterministischer Python-Code, **kein LLM** | `copilot/bpmn.py` |
| 5 | **Darstellung & Export** – interaktives Diagramm mit Zoom, „Einpassen“ und Vollbild; Download als `.bpmn` (Camunda, Signavio, bpmn.io) und JSON | bpmn-js | `copilot/viewer.py` |
| 6 | **Analyse** – Engpässe, unnötige Übergaben, Wartezeiten sowie Automatisierungspotenziale (RPA / API / KI) | Zweiter Gemini-Aufruf, ebenfalls schema-gebunden | `analyze_process()` |
| 7 | **Soll-Prozess (To-Be)** – verbesserter Prozess auf Basis der Analyse, mit Änderungsübersicht; Ist und Soll in Tabs bzw. nebeneinander, neue Elemente hervorgehoben | Dritter Gemini-Aufruf mit derselben Validierung und Reparaturschleife; Kennzahlenvergleich in Python | `propose_to_be()`, `copilot/compare.py` |

## Designentscheidungen

- **Das LLM erzeugt nur Struktur, nie XML.** BPMN-XML ist umfangreich und fehleranfällig. Das Modell liefert ein schlankes, schema-gebundenes JSON; die Übersetzung in BPMN übernimmt deterministischer Code. Das Ergebnis ist dadurch immer syntaktisch gültig und reproduzierbar.
- **Zuverlässige Übergabe durch Structured Output und Validierung.** Das Pydantic-Schema wird als JSON-Schema an Gemini übergeben und die Antwort mit demselben Schema geprüft. Zusätzlich prüft eine Graphanalyse die *fachliche* Korrektheit des Prozesses. Die Fehlermeldungen sind so formuliert, dass das Modell sie direkt zur Selbstkorrektur nutzen kann.
- **Ein Validierungspfad für Ist und Soll.** Der Soll-Prozess nutzt dasselbe Schema, dieselbe Graphprüfung und dieselbe Reparaturschleife wie die Extraktion. Ein „verbesserter“ Prozess mit Sackgassen kann so nicht entstehen.
- **Fakten statt LLM-Behauptungen beim Vergleich.** Die Änderungsübersicht formuliert das Modell, die Kennzahlen (manuelle vs. automatisierte Aufgaben, manuelle Übergaben zwischen Rollen, neue und entfallene Elemente) berechnet dagegen deterministischer Code aus beiden Modellen.
- **Schutz vor Halluzinationen in der Analyse.** Verweise des Modells auf nicht existierende Elemente werden automatisch entfernt.
- **Automatisches Layout und Routing.** Spalten ergeben sich aus dem längsten Pfad im Prozessgraphen, Schleifen werden per Tiefensuche erkannt und unten herum geführt. Zweige behalten die Zeile ihres Vorgängers. Sequenzflüsse werden orthogonal in den freien Kanälen zwischen den Spalten geführt: Für jeden Fluss werden mehrere Routen erzeugt und so bewertet, dass keine Linie durch ein Element läuft und keine zwei Flüsse deckungsgleich übereinanderliegen. Ein Test prüft das für mehrere Beispielprozesse.
- **Testbar ohne API.** Ein Demo-Client simuliert Gemini – inklusive einer fehlerhaften ersten Antwort, um die Reparaturschleife vorzuführen. Die Tests laufen dadurch offline und deterministisch.

## Schnellstart

```bash
git clone https://github.com/Dona7s/prozess-copilot.git
cd prozess-copilot
pip install -r requirements.txt

export GEMINI_API_KEY="..."              # https://aistudio.google.com/apikey
export GEMINI_MODEL="gemini-3.8-flash"   # optional, jedes aktuelle Gemini-Modell möglich

streamlit run app.py
```

Ohne API-Schlüssel startet die Anwendung im **Demo-Modus** mit dem Beispielprozess (inklusive Analyse und Soll-Prozess).

### Beispiel: Soll-Prozess

Aus der Bestellabwicklung entwirft der Copilot u. a. eine automatische Bestandsprüfung per ERP-API und ein KI-gestütztes Alternativangebot, wenn Ware fehlt:

![Soll-Prozess](docs/soll-prozess.png)

## Tests

```bash
python -m pytest -q
```

Abgedeckt sind u. a. die Reparaturschleife (auch für den Soll-Prozess), der Ist/Soll-Vergleich, Schema- und Graphfehler, die BPMN-XML-Struktur, Schleifen und ungültige IDs sowie das Entfernen halluzinierter Verweise. Die Tests laufen bei jedem Push über GitHub Actions.

## Projektstruktur

```
prozess-copilot/
├── app.py                  # Streamlit-Oberfläche
├── copilot/
│   ├── schema.py           # Pydantic-Modelle (Prozess + Analyse)
│   ├── llm.py              # Gemini-Client, Prompts, Reparaturschleife, Analyse, Soll-Prozess
│   ├── validate.py         # Graphbasierte Prüfung des Prozessmodells
│   ├── bpmn.py             # JSON → BPMN 2.0 XML inkl. Layout und Routing
│   ├── compare.py          # Ist/Soll-Vergleich und Kennzahlen
│   ├── viewer.py           # bpmn-js-Einbettung mit Zoom und Vollbild
│   └── demo.py             # Offline-Demo-Client und Beispieldaten
├── examples/
│   └── bestellabwicklung.bpmn
└── tests/
    └── test_pipeline.py
```

## Technologien

Python · Google Gemini API (`google-genai`) · Pydantic · Streamlit · bpmn-js · BPMN 2.0 · pytest · GitHub Actions

## Ausblick

- Weitere BPMN-Elemente (Zwischenereignisse, Nachrichtenflüsse, Subprozesse)
- Diagramm iterativ per Chat bearbeiten („Füge eine Freigabe durch die Buchhaltung hinzu“)
- Import bestehender `.bpmn`-Dateien zur Analyse
- Evaluation der Extraktionsqualität über einen Testkorpus von Prozessbeschreibungen
