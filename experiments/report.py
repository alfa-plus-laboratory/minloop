"""Results: the JSON record of a suite run, and the Markdown report rendered from it (numbers are never typed in)."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from probes.cell import POLARITIES
from probes.organisms import MODULES
from probes.report import wilson

from .subjects import SUBJECTS

MOCK_BANNER = ("**MOCK DATA. NOT A MODEL EXPERIMENT.** Every LLM reply below came from a deterministic mock script "
               "written to pass or to fail its cell. These numbers show that the harness, the three subjects and the "
               "probe judges fit together. They say nothing about any model, and nothing about whether one scaffold "
               "is better than another.")


def dumps(results: dict) -> str:
    return json.dumps(results, ensure_ascii=False, indent=1) + "\n"


def comparable(results: dict) -> dict:
    """What a re-run must reproduce exactly: every run record and every not-applicable entry."""
    return {"runs": results["runs"], "not_applicable": results["not_applicable"]}


def ci(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} [{lo:.2f}, {hi:.2f}]" if n else "-"


def render(results: dict, json_name: str = "results-mock.json", json_text: str | None = None) -> str:
    runs, na, date, s = results["runs"], results["not_applicable"], results["date"], results["settings"]
    subjects = s["subjects"]
    digest = hashlib.sha256((json_text or dumps(results)).encode("utf-8")).hexdigest()
    out = [f"# Probe suite results: {results['label']}", "", MOCK_BANNER, "",
           "| | |", "|---|---|", f"| date | {date} |", f"| source | commit `{results['source']}` |",
           f"| code sha256 | `{results['code_sha256']}` (`experiments.harness.code_digest`: src/minloop, probes, "
           "experiments) |",
           f"| command | `{results['command']}` |", f"| seeds | {', '.join(map(str, s['seeds']))} |",
           f"| personas | {', '.join(s['personas'])} (see `experiments/personas.py`) |",
           f"| judge | {s['judge']} |", f"| replay check | {'yes' if s['replay'] else 'no'} |",
           f"| raw data | `{json_name}`, sha256 `{digest}` |",
           f"| runs | {len(runs)} scored, {len(na)} subject x cell pairs not applicable |", "",
           "To check every number: `PYTHONPATH=src python -m experiments check experiments/" + json_name + "` runs "
           "the whole suite again with the recorded settings and compares every run record (not the date or the "
           "commit). `tests/test_experiments.py` checks that this file is exactly what `experiments/report.py` "
           "renders from the JSON, that the JSON's code sha256 is that of the current code, and re-runs one cell "
           "per subject against the recorded records.", ""]

    out += ["## 1. Harness check", "",
            "Each persona is written to get one verdict in every cell (`honest`: pass, `flawed`: fail). A row is "
            "healthy when every run got its intended verdict, ended normally and replayed identically.", "",
            "| date | subject | persona | runs | intended verdict | got it | stop = done | replay identical | "
            "decided by rule / judge | undecided by rules alone |", "|---|---|---|---|---|---|---|---|---|---|"]
    for subject in subjects:
        for persona in s["personas"]:
            rs = [r for r in runs if r["subject"] == subject and r["persona"] == persona]
            by = Counter(r["by"] for r in rs)
            got, done = sum(r["verdict"] == r.get("intended") for r in rs), sum(r["stop"] == "done" for r in rs)
            out.append(f"| {date} | {subject} | {persona} | {len(rs)} | {rs[0]['intended'] if rs else '-'} | "
                       f"{got} | {done} | "
                       f"{sum(r.get('replay') == 'identical' for r in rs) if s['replay'] else '-'} | "
                       f"{by['rule']} / {by['judge']} | {sum(r['rules'] == 'undecided' for r in rs)} |")

    out += ["", "## 2. Pass rates by organism and polarity", "",
            "Passes / scored runs with a Wilson 95% interval; `n/a` means the subject cannot be scored on that cell "
            "by design (section 4). This is the table a real experiment will fill. The `honest` scripts are written to "
            "pass and the `flawed` ones to fail, so anything other than 100% and 0% here would be a harness bug.", ""]
    cells = defaultdict(list)
    for r in runs:
        cells[(r["organism"], r["polarity"], r["subject"], r["persona"])].append(r["verdict"] == "pass")
    order = [m.NAME for m in MODULES]
    keys = sorted({(r["organism"], r["polarity"]) for r in runs} | {(x["organism"], x["polarity"]) for x in na},
                  key=lambda k: (order.index(k[0]), POLARITIES.index(k[1])))
    for persona in s["personas"]:
        out += [f"**persona `{persona}`**", "", "| date | organism | polarity | " + " | ".join(subjects) + " |",
                "|---|---|---|" + "---|" * len(subjects)]
        for organism, polarity in keys:
            row = []
            for subject in subjects:
                hits = cells.get((organism, polarity, subject, persona))
                row.append(ci(sum(hits), len(hits)) if hits else "n/a")
            out.append(f"| {date} | {organism} | {polarity} | " + " | ".join(row) + " |")
        out.append("")

    out += ["## 3. Cost structure per run", "",
            "Means over all scored runs. Tokens are the mock adapter's estimate (characters / 4), so only the ratios "
            "between subjects mean anything, and only for these scripts. For `bare`, the steps are the harness "
            "reading the files into the prompt and carrying out the answer; the model is called once.", "",
            "| date | subject | runs | LLM calls | steps | tokens (estimate) |", "|---|---|---|---|---|---|"]
    for subject in subjects:
        rs = [r for r in runs if r["subject"] == subject]
        if rs:
            mean = {k: sum(r[k] for r in rs) / len(rs) for k in ("llm_calls", "steps", "tokens")}
            out.append(f"| {date} | {subject} | {len(rs)} | {mean['llm_calls']:.1f} | {mean['steps']:.1f} | "
                       f"{mean['tokens']:.0f} |")

    out += ["", "## 4. Not applicable", "",
            "A cell is not run for a subject when its pass condition needs something the subject does not produce by "
            "design, so a missing mechanism is never counted as a failure. Signals produced: " + "; ".join(
                f"{name}: {', '.join(sorted(SUBJECTS[name].signals))}" for name in subjects) + ".", "",
            "| date | subject | cell | polarity | reason |", "|---|---|---|---|---|"]
    out += [f"| {date} | {x['subject']} | {x['cell']} | {x['polarity']} | {x['reason']} |" for x in na]

    out += ["", "## 5. Every scored cell", "",
            "Verdicts over the seeds, with the checks that failed (or stayed open by rules alone).", "",
            "| date | subject | cell | persona | verdicts | by | failed checks |", "|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in runs:
        groups[(r["subject"], r["cell"], r["persona"])].append(r)
    for (subject, cell, persona), rs in groups.items():
        counts = Counter(r["verdict"] for r in rs)
        failed = sorted({c for r in rs for c in r["failed"]}) or sorted({c for r in rs for c in r["open"]})
        out.append(f"| {date} | {subject} | {cell} | {persona} | "
                   f"{', '.join(f'{v} x{n}' for v, n in sorted(counts.items()))} | "
                   f"{'/'.join(sorted({r['by'] for r in rs}))} | {'; '.join(failed) or '-'} |")
    return "\n".join(out) + "\n"

