"""Evaluation des Prozess Copilot gegen den Testkorpus in eval/dataset.json.

Beispiele:
    python eval/run_eval.py                         # alle Fälle, 1 Lauf, Modell aus GEMINI_MODEL
    python eval/run_eval.py --runs 3 --update-readme
    python eval/run_eval.py --cases urlaub rechnung --model gemini-3.8-flash
    python eval/run_eval.py --demo                  # Probelauf ohne API (nur Technik, keine echten Werte)

Ergebnisse: eval/results/<zeitstempel>.json und .md; mit --update-readme zusätzlich im README.
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

from copilot.evaluation import evaluate_case, markdown_report, summarize  # noqa: E402
from copilot.llm import DEFAULT_MODEL  # noqa: E402

START, END = "<!-- EVAL:START -->", "<!-- EVAL:END -->"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--runs", type=int, default=1, help="Wiederholungen je Fall (Streuung messen)")
    ap.add_argument("--cases", nargs="*", help="nur diese Fall-IDs")
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.0, help="Pause zwischen Aufrufen (Rate-Limits)")
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.json"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--update-readme", action="store_true")
    ap.add_argument("--demo", action="store_true", help="DemoClient statt Gemini (Technik-Test)")
    args = ap.parse_args(argv)

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["cases"]
    if args.cases:
        cases = [c for c in cases if c["id"] in set(args.cases)]
    if args.demo:
        from copilot.demo import DemoClient
        client, model_name = DemoClient(), "demo"
    else:
        from copilot.llm import GeminiClient
        client, model_name = GeminiClient(model=args.model), args.model

    results = []
    for run in range(1, args.runs + 1):
        for case in cases:
            r = evaluate_case(client, case, run=run, max_repairs=args.max_repairs)
            results.append(r)
            total = f"{r.score['total'] * 100:.0f} %" if r.score else "–"
            status = "ok" if r.final_valid else ("API-Fehler: " + r.exception if r.exception else "ungültig")
            print(f"[{run}] {case['id']:<14} first-try={'ja' if r.first_try_valid else 'nein':<4} "
                  f"versuche={r.attempts} struktur={total:<5} {r.seconds:5.1f}s  {status}", flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    summary = summarize(results)
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = markdown_report(results, summary, model_name, date)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps({
        "model": model_name, "date": date, "summary": summary,
        "results": [asdict(r) for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{stamp}.md").write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    print(f"\nGespeichert: {out / stamp}.json / .md")

    if args.update_readme:
        readme = ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        if START in text and END in text:
            head, rest = text.split(START, 1)
            _, tail = rest.split(END, 1)
            readme.write_text(f"{head}{START}\n{md}\n{END}{tail}", encoding="utf-8")
            print("README aktualisiert.")
        else:
            print("Hinweis: Markierungen für die Ergebnistabelle fehlen im README.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
