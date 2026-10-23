"""Command line: run (and resume), replay (print or re-run a trace), inspect (summary of a trace)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import trace
from .kernel import BUDGET_KEYS, ConfigError, Kernel
from .llm import MockLLM, OpenAICompat, ReplayLLM


def fmt(e: dict) -> str | None:
    """One console line per interesting event (the same printer serves live runs and replays)."""
    t, tag = e["type"], f"[{e['step']}]" if e["step"] else "   "
    if t == "run_start":
        llm = e["llm"]
        return (f"== run {e['run_id']} session {e['session']} | llm {llm['adapter']} {llm.get('model', '')} "
                f"| budget {json.dumps(e['budget'])}{' | forever' if e['forever'] else ''}")
    if t == "goal_set":
        g = e["goal"]
        return f"{tag} + goal {g['id']} ({g['source']}): {g['text']}\n      done when: {'; '.join(g['criteria'])}"
    if t == "goal_update":
        return f"{tag} ~ goal {e['goal']['id']} -> {e['goal']['status']}"
    if t == "plan":
        p = e["plan"]
        return (f"{tag} plan  {p['tool']} {json.dumps(p['args'], ensure_ascii=False)[:100]}\n"
                f"      expect ({p['confidence']:.2f}): {p['expected'][:140]}")
    if t == "tool_result":
        out = e["output"] if isinstance(e["output"], str) else f"<{e['output']['chars']} chars>"
        first = (out if e["ok"] else e["error"] or "").strip().splitlines() or [""]
        return f"{tag} {'ok ' if e['ok'] else 'ERR'}   {first[0][:140]}"
    if t == "reflect" and e["level"] == "light":
        r = e["reflection"]
        return (f"{tag} reflect met={r['met_expectation']} surprise={e.get('surprise')} "
                f"goal={r['goal_action']} | {r['lesson'][:120]}")
    if t == "reflect":
        return f"{tag} deep reflection ({e.get('trigger')}): {e['reflection']['summary'][:140]}"
    if t == "note_write":
        n = e["note"]
        return f"{tag} note {n['id']} {n['op']}" + (f" [{n['kind']}] {n['title']}" if n["op"] == "add" else "")
    if t in ("ask_human", "charter_proposal", "idle", "warning"):
        return f"{tag} {t}: {e.get('question') or e.get('change') or e.get('reason') or e.get('message')}"
    if t == "stop":
        return f"== stop: {e['reason']} {e['detail']}"
    return None


def printer(e: dict):
    line = fmt(e)
    if line:
        print(line, flush=True)


def make_llm(a):
    def endpoint():
        if not a.model:
            raise ConfigError("--model (or MINLOOP_MODEL) is required for an OpenAI-compatible endpoint")
        return OpenAICompat(a.base_url, a.model, os.environ.get(a.api_key_env), a.temperature, a.call_max_tokens,
                            a.seed, price_in=a.price_in, price_out=a.price_out)
    if a.llm in ("mock", "replay") and not (a.mock_script if a.llm == "mock" else a.replay_trace):
        raise ConfigError(f"--llm {a.llm} needs --{'mock-script' if a.llm == 'mock' else 'replay-trace'}")
    if a.llm == "mock":
        return MockLLM.from_file(a.mock_script)
    if a.llm == "replay":
        counterfactual = a.from_step is not None
        return ReplayLLM(a.replay_trace, strict=not counterfactual, from_step=a.from_step,
                         then=endpoint() if counterfactual else None)
    return endpoint()


def read_workspace(charter_path: Path, charter: dict) -> dict:
    """Initial workspace files, copied into the run and recorded in run_start (text files only)."""
    if not charter.get("workspace"):
        return {}
    root, files = charter_path.parent / charter["workspace"], {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                files[p.relative_to(root).as_posix()] = p.read_bytes().decode("utf-8")  # bytes kept as they are
            except UnicodeDecodeError:
                raise ConfigError(f"workspace file '{p.relative_to(root)}' is not UTF-8 text") from None
    return files


def cmd_run(a) -> dict:
    budget = {k: getattr(a, k) for k in BUDGET_KEYS if getattr(a, k) is not None}
    options = dict(budget=budget, forever=a.forever, notes_path=a.notes, prompts_dir=a.prompts,
                   on_event=[] if a.quiet else [printer])
    if a.resume:
        kernel = Kernel.resume(a.resume, make_llm(a), **options)
    else:
        if not a.charter:
            raise ConfigError("give a charter file, or --resume RUN_DIR")
        try:
            charter = json.loads(Path(a.charter).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read charter '{a.charter}': {e}") from None
        run_id = a.run_id or time.strftime("%Y%m%d-%H%M%S-") + os.urandom(2).hex()
        kernel = Kernel(charter, make_llm(a), Path(a.root) / run_id, run_id=run_id,
                        files=read_workspace(Path(a.charter), charter), **options)
    return start(kernel)


def start(kernel: Kernel) -> dict:
    for warning in kernel.warnings:
        print(f"\n{'!' * 78}\n!! {warning}\n{'!' * 78}\n", file=sys.stderr)
    return kernel.run()


def cmd_replay(a) -> dict | None:
    events = trace.read(a.trace)
    if not a.rerun:
        for e in events:
            printer(e)
        return None
    first = next(e for e in events if e["type"] == "run_start")
    base, run_id = Path(a.trace).parent, a.run_id or first["run_id"]
    a.llm, a.replay_trace = "replay", a.trace
    kernel = Kernel(first["charter"], make_llm(a), Path(a.root) / run_id, run_id=run_id,
                    budget=first["budget"], forever=first["forever"], prompts_dir=a.prompts,
                    files={k: trace.text(v, base) for k, v in first["workspace_files"].items()},
                    on_event=[] if a.quiet else [printer])
    return start(kernel)


def cmd_inspect(a) -> dict:
    events = trace.read(a.trace)
    of = lambda t: [e for e in events if e["type"] == t]  # noqa: E731
    goals = {e["goal"]["id"]: e["goal"] for e in events if e["type"] in ("goal_set", "goal_update")}
    plans = {e["step"]: e["plan"] for e in of("plan")}
    pairs = [(plans[e["step"]]["confidence"], e["reflection"]["met_expectation"])
             for e in of("reflect") if e["level"] == "light" and e["step"] in plans]
    results = of("tool_result")
    summary = {
        "sessions": len(of("run_start")), "steps": max([e["step"] or 0 for e in events] or [0]),
        "llm_calls": len(of("llm_call")),
        "tokens": sum(e["usage"]["prompt_tokens"] + e["usage"]["completion_tokens"] for e in of("llm_call")),
        "cost": round(sum(e["usage"].get("cost", 0) for e in of("llm_call")), 6),
        "tool_calls": len(results),
        "tool_error_rate": round(sum(not e["ok"] for e in results) / len(results), 3) if results else None,
        "finish": sum(e["tool"] == "finish" and e["ok"] for e in results),
        "give_up": sum(e["tool"] == "give_up" and e["ok"] for e in results),
        "ask_human": len(of("ask_human")), "charter_proposals": len(of("charter_proposal")),
        "notes_written": sum(e["note"]["op"] == "add" for e in of("note_write")),
        "warnings": len(of("warning")),
        "mean_confidence": round(sum(c for c, _ in pairs) / len(pairs), 3) if pairs else None,
        "brier": round(sum((c - m) ** 2 for c, m in pairs) / len(pairs), 4) if pairs else None,
        "stops": [f"{e['reason']}: {e['detail']}" for e in of("stop")],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("goals:")

    def tree(parent, depth):
        for g in (g for g in goals.values() if g["parent"] == parent):
            print(f"{'  ' * depth}  {g['id']} [{g['status']}] {g['text']}")
            tree(g["id"], depth + 1)
    tree(None, 0)
    return summary


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="minloop", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="start a run from a charter, or resume one")
    run.add_argument("charter", nargs="?", help="charter JSON file")
    run.add_argument("--resume", metavar="RUN_DIR", help="continue a stopped run from its state.json")
    run.add_argument("--llm", choices=["openai", "mock", "replay"], default="openai")
    run.add_argument("--mock-script", help="JSON script for --llm mock")
    run.add_argument("--replay-trace", help="trace whose LLM responses --llm replay feeds back")
    for key in BUDGET_KEYS:
        run.add_argument("--" + key.replace("_", "-"), dest=key, type=float if key == "max_cost" else int)
    run.add_argument("--forever", action="store_true", help="ignore step/time budgets; needs a token or cost "
                     "budget and the no-progress stop")
    run.add_argument("--notes", help="notes.jsonl to use (e.g. one shared across runs); default: per run")
    rep = sub.add_parser("replay", help="print a recorded run step by step, or --rerun it deterministically")
    rep.add_argument("trace")
    rep.add_argument("--rerun", action="store_true", help="re-run the loop with the recorded LLM responses")
    for p in (run, rep):
        p.add_argument("--root", default="runs", help="directory that holds run directories (default: ./runs)")
        p.add_argument("--run-id")
        p.add_argument("--prompts", help="directory with replacement prompt files")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--from-step", type=int, help="replay: recorded responses only before this step, then "
                       "the endpoint answers (counterfactual replay)")
        p.add_argument("--model", default=os.environ.get("MINLOOP_MODEL"))
        p.add_argument("--base-url", default=os.environ.get("MINLOOP_BASE_URL", "http://localhost:8080/v1"))
        p.add_argument("--api-key-env", default="OPENAI_API_KEY", help="environment variable holding the API key")
        p.add_argument("--temperature", type=float, default=0.0)
        p.add_argument("--call-max-tokens", type=int, default=2048, help="max_tokens for each LLM call")
        p.add_argument("--seed", type=int)
        p.add_argument("--price-in", type=float, default=0.0, help="cost per million prompt tokens")
        p.add_argument("--price-out", type=float, default=0.0, help="cost per million completion tokens")
    sub.add_parser("inspect", help="summarize a trace").add_argument("trace")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        result = {"run": cmd_run, "replay": cmd_replay, "inspect": cmd_inspect}[a.cmd](a)
    except ConfigError as e:
        print(f"minloop: {e}", file=sys.stderr)
        return 2
    if a.cmd != "inspect" and result:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["reason"] in ("done", "budget", "no_progress") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
