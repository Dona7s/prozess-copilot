"""Evaluation des Prozess Copilot gegen den Testkorpus in eval/dataset.json.

Beispiele:
    python eval/run_eval.py                         # alle Fälle, 1 Lauf, Modell aus GEMINI_MODEL
    python eval/run_eval.py --runs 3 --update-readme
    python eval/run_eval.py --cases urlaub rechnung --model gemini-3.8-flash
    python eval/run_eval.py --ablation --runs 3 --update-readme   # Structured Output / Reparatur vergleichen
    python eval/run_eval.py --demo                  # Probelauf ohne API (nur Technik, keine echten Werte)

Ablation: Es werden zwei Läufe gemacht – mit Structured Output (response_json_schema) und mit
reinem Prompting (Schema nur als Text im Prompt), jeweils mit Reparaturschleife. Die Varianten
ohne Reparatur sind der erste Versuch derselben Läufe (identischer Aufruf); das halbiert die
API-Kosten, ohne das Ergebnis zu verändern.

Ergebnisse: eval/results/<zeitstempel>[-ablation].json und .md; mit --update-readme zusätzlich im README.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from copilot.evaluation import (  # noqa: E402
    ABLATION_MODES,
    ablation_report,
    evaluate_case,
    markdown_report,
    summarize,
    without_repair,
)
from copilot.llm import DEFAULT_MODEL  # noqa: E402

MARKERS = {
    "eval": ("<!-- EVAL:START -->", "<!-- EVAL:END -->"),
    "ablation": ("<!-- ABLATION:START -->", "<!-- ABLATION:END -->"),
}


def _make_client(args, structured: bool):
    if args.demo:
        from copilot.demo import DemoClient

        return DemoClient()
    from copilot.llm import GeminiClient

    return GeminiClient(model=args.model, structured=structured)


def _run(client, cases, args, mode: str) -> list:
    results = []
    for run in range(1, args.runs + 1):
        for case in cases:
            r = evaluate_case(client, case, run=run, max_repairs=args.max_repairs, mode=mode)
            results.append(r)
            total = f"{r.score['total'] * 100:.0f} %" if r.score else "–"
            status = "ok" if r.final_valid else ("API-Fehler: " + r.exception if r.exception else "ungültig")
            print(f"[{mode} {run}] {case['id']:<14} first-try={'ja' if r.first_try_valid else 'nein':<4} "
                  f"versuche={r.attempts} struktur={total:<5} {r.seconds:5.1f}s  {status}", flush=True)
            if args.sleep:
                time.sleep(args.sleep)
    return results


def _update_readme(section: str, md: str) -> None:
    start, end = MARKERS[section]
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if start in text and end in text:
        head, rest = text.split(start, 1)
        _, tail = rest.split(end, 1)
        readme.write_text(f"{head}{start}\n{md}\n{end}{tail}", encoding="utf-8")
        print("README aktualisiert.")
    else:
        print(f"Hinweis: Markierungen {start} … {end} fehlen im README.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--runs", type=int, default=1, help="Wiederholungen je Fall (Streuung messen)")
    ap.add_argument("--cases", nargs="*", help="nur diese Fall-IDs")
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.0, help="Pause zwischen Fällen (Rate-Limits)")
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.json"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--ablation", action="store_true",
                    help="Structured Output vs. reines Prompting, jeweils mit und ohne Reparatur")
    ap.add_argument("--update-readme", action="store_true")
    ap.add_argument("--demo", action="store_true", help="DemoClient statt Gemini (Technik-Test)")
    args = ap.parse_args(argv)

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["cases"]
    if args.cases:
        cases = [c for c in cases if c["id"] in set(args.cases)]
    model_name = "demo" if args.demo else args.model
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if not args.ablation:
        results = _run(_make_client(args, structured=True), cases, args, "schema+repair")
        summary = summarize(results)
        md = markdown_report(results, summary, model_name, date)
        payload = {"model": model_name, "date": date, "summary": summary, "results": [asdict(r) for r in results]}
        name = stamp
    else:
        by_mode = {}
        for base, structured in (("schema", True), ("prompt", False)):
            res = _run(_make_client(args, structured=structured), cases, args, f"{base}+repair")
            by_mode[f"{base}+repair"] = res
            by_mode[base] = [without_repair(r, base) for r in res]
        summaries = {k: summarize(v) for k, v in by_mode.items()}
        md = ablation_report(summaries, model_name, date, args.runs, len(cases))
        payload = {"model": model_name, "date": date, "ablation": True,
                   "modes": {k: ABLATION_MODES[k][0] for k in by_mode},
                   "summaries": summaries,
                   "results": {k: [asdict(r) for r in v] for k, v in by_mode.items()}}
        name = f"{stamp}-ablation"

    (out / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{name}.md").write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    print(f"\nGespeichert: {out / name}.json / .md")
    if args.update_readme:
        _update_readme("ablation" if args.ablation else "eval", md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
