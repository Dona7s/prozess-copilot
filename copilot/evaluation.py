"""Evaluation der Extraktion: Zuverlässigkeit des Structured Output und strukturelle Genauigkeit.

Gemessen wird je Testfall:
- ob die erste Antwort bereits schema- und graphvalide ist (First-Try-Validität),
- wie viele Reparaturrunden nötig waren und ob am Ende ein gültiges Modell vorliegt,
- wie gut die Struktur zur Erwartung passt (Rollen, Aktivitäten, Entscheidungen, Parallelität),
- welche Fehlerarten im ersten Versuch auftraten.

Die Zuordnung von Namen ist bewusst unscharf (Wortstämme), weil das Modell Aktivitäten
frei formulieren darf: „Lagerbestand prüfen“ und „Bestand im Lager prüfen“ gelten als Treffer.
"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field

from .llm import extract_process
from .schema import NodeType, ProcessModel

STEM_LEN = 5
STOPWORDS = {"der", "die", "das", "den", "dem", "des", "und", "oder", "eine", "einen", "einer", "beim", "zum",
             "zur", "per", "mit", "von", "vom", "für", "fuer", "an", "im", "in", "ins", "auf", "aus", "bei"}


# ---------------------------------------------------------------- Namensabgleich

def _norm(text: str) -> str:
    text = text.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def stems(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _norm(text))
    return {w[:STEM_LEN] for w in words if len(w) >= 2 and w not in STOPWORDS}


def name_matches(expected: str, actual: str) -> bool:
    """expected darf Alternativen mit | enthalten. Treffer, wenn mind. die Hälfte der Stämme vorkommt."""
    act = stems(actual)
    for alt in expected.split("|"):
        exp = stems(alt)
        if exp and len(exp & act) / len(exp) >= 0.5:
            return True
    return False


def _match_count(expected: list[str], actual: list[str]) -> int:
    """Greedy 1:1-Zuordnung, damit ein Element nicht mehrere Erwartungen erfüllt."""
    free = list(actual)
    hits = 0
    for e in expected:
        for a in free:
            if name_matches(e, a):
                free.remove(a)
                hits += 1
                break
    return hits


# ---------------------------------------------------------------- Bewertung eines Modells

def splits(model: ProcessModel, node_type: NodeType) -> int:
    out = Counter(f.source for f in model.flows)
    return sum(1 for n in model.nodes if n.type == node_type and out[n.id] > 1)


@dataclass
class StructureScore:
    lane_recall: float
    lane_precision: float
    activity_recall: float
    decisions_ok: bool
    parallel_ok: bool
    decisions: int
    parallel: int

    @property
    def lane_f1(self) -> float:
        p, r = self.lane_precision, self.lane_recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    @property
    def total(self) -> float:
        """Gleichgewichteter Gesamtwert 0–1."""
        return (self.lane_f1 + self.activity_recall + float(self.decisions_ok) + float(self.parallel_ok)) / 4


def score_structure(model: ProcessModel, expected: dict) -> StructureScore:
    lanes = [l.name for l in model.lanes]
    tasks = [n.name for n in model.nodes if n.type in (NodeType.task, NodeType.user_task, NodeType.service_task)]
    exp_lanes = expected.get("lanes", [])
    exp_acts = expected.get("activities", [])
    lane_hits = _match_count(exp_lanes, lanes)
    act_hits = _match_count(exp_acts, tasks)
    dec = splits(model, NodeType.exclusive_gateway)
    par = splits(model, NodeType.parallel_gateway)
    return StructureScore(
        lane_recall=lane_hits / len(exp_lanes) if exp_lanes else 1.0,
        lane_precision=lane_hits / len(lanes) if lanes else 0.0,
        activity_recall=act_hits / len(exp_acts) if exp_acts else 1.0,
        decisions_ok=dec == expected.get("decisions", dec),
        parallel_ok=par == expected.get("parallel", par),
        decisions=dec,
        parallel=par,
    )


# ---------------------------------------------------------------- Fehlerkategorien

ERROR_CATEGORIES = [  # Reihenfolge = Priorität (spezifisch vor allgemein)
    ("Schema", r"entspricht nicht dem Schema"),
    ("Doppelte ID", r"mehrfach verwendet|leerer ID"),
    ("Ungültige Referenz", r"existiert nicht|nicht existierende Lane"),
    ("Erreichbarkeit", r"nicht erreichbar|kein endEvent erreichbar"),
    ("Aufgabe ohne Ein-/Ausgang", r"^Aufgabe "),
    ("Gateway-Struktur", r"^Gateway "),
    ("XOR ohne Bedingung", r"keine Bedingung"),
    ("Start/Ende", r"^(startEvent|endEvent|Kein startEvent|Kein endEvent)"),
]


def categorize(error: str) -> str:
    for name, pattern in ERROR_CATEGORIES:
        if re.search(pattern, error):
            return name
    return "Sonstiges"


# ---------------------------------------------------------------- Lauf

@dataclass
class CaseResult:
    case_id: str
    run: int
    first_try_valid: bool
    final_valid: bool
    attempts: int
    seconds: float
    first_errors: list[str] = field(default_factory=list)
    score: dict | None = None  # StructureScore als dict + total
    exception: str = ""


def evaluate_case(client, case: dict, run: int = 1, max_repairs: int = 3) -> CaseResult:
    t0 = time.perf_counter()
    try:
        res = extract_process(client, case["text"], max_repairs=max_repairs)
    except Exception as e:  # API-Fehler zählen als ungültig, Lauf geht weiter
        return CaseResult(case["id"], run, False, False, 0, time.perf_counter() - t0, exception=str(e)[:300])
    dt = time.perf_counter() - t0
    first_errors = res.attempts[0]["errors"] if res.attempts else []
    score = None
    if res.model is not None:
        s = score_structure(res.model, case["expected"])
        score = asdict(s) | {"lane_f1": s.lane_f1, "total": s.total}
    return CaseResult(
        case_id=case["id"],
        run=run,
        first_try_valid=not first_errors,
        final_valid=res.ok,
        attempts=len(res.attempts),
        seconds=dt,
        first_errors=first_errors,
        score=score,
    )


def summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    scored = [r.score for r in results if r.score]

    def mean(values):
        values = list(values)
        return sum(values) / len(values) if values else 0.0

    cats = Counter(categorize(e) for r in results for e in r.first_errors)
    return {
        "runs": n,
        "first_try_validity": mean(r.first_try_valid for r in results),
        "final_validity": mean(r.final_valid for r in results),
        "avg_attempts": mean(r.attempts for r in results if r.attempts),
        "repaired_share": mean((not r.first_try_valid) and r.final_valid for r in results),
        "lane_f1": mean(s["lane_f1"] for s in scored),
        "activity_recall": mean(s["activity_recall"] for s in scored),
        "decisions_accuracy": mean(s["decisions_ok"] for s in scored),
        "parallel_accuracy": mean(s["parallel_ok"] for s in scored),
        "structure_total": mean(s["total"] for s in scored),
        "avg_seconds": mean(r.seconds for r in results),
        "api_errors": sum(1 for r in results if r.exception),
        "error_categories": dict(cats.most_common()),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def markdown_report(results: list[CaseResult], summary: dict, model_name: str, date: str) -> str:
    lines = [
        f"**Modell:** `{model_name}` · **Datum:** {date} · **Läufe:** {summary['runs']}",
        "",
        "| Kennzahl | Wert |",
        "|---|---|",
        f"| Valide im ersten Versuch (First-Try) | {_pct(summary['first_try_validity'])} |",
        f"| Valide nach Reparaturschleife | {_pct(summary['final_validity'])} |",
        f"| Durch Reparatur gerettet | {_pct(summary['repaired_share'])} |",
        f"| Ø Generierungen je Fall | {summary['avg_attempts']:.2f} |",
        f"| Rollen (F1) | {_pct(summary['lane_f1'])} |",
        f"| Aktivitäten (Recall) | {_pct(summary['activity_recall'])} |",
        f"| Entscheidungen korrekt | {_pct(summary['decisions_accuracy'])} |",
        f"| Parallelität korrekt | {_pct(summary['parallel_accuracy'])} |",
        f"| **Struktur gesamt** | **{_pct(summary['structure_total'])}** |",
        f"| Ø Laufzeit je Fall | {summary['avg_seconds']:.1f} s |",
        "",
        "| Fall | First-Try | Versuche | Valide | Rollen F1 | Aktivitäten | Entsch. | Parallel |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r.score or {}
        lines.append(
            "| {id}{run} | {ft} | {att} | {fv} | {lf} | {ar} | {de} | {pa} |".format(
                id=r.case_id,
                run=f" #{r.run}" if summary["runs"] > len({x.case_id for x in results}) else "",
                ft="✅" if r.first_try_valid else "❌",
                att=r.attempts or "–",
                fv="✅" if r.final_valid else "❌",
                lf=_pct(s["lane_f1"]) if s else "–",
                ar=_pct(s["activity_recall"]) if s else "–",
                de=("✅" if s["decisions_ok"] else "❌") if s else "–",
                pa=("✅" if s["parallel_ok"] else "❌") if s else "–",
            )
        )
    if summary["error_categories"]:
        lines += ["", "Fehlerarten im ersten Versuch: " + ", ".join(
            f"{k} ({v})" for k, v in summary["error_categories"].items())]
    return "\n".join(lines)
