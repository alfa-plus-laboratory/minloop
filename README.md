# minloop

A minimal, fully autonomous agent loop for research: **goal → plan → act → observe → reflect**, around and around,
until the mission is done, the budget runs out, or the agent stops making progress. Every step is written to an
append-only JSONL trace that can be replayed, inspected and evaluated offline.

> **Status: pre-release research code (0.1.0.dev0). This is not a production framework.**

- **The agent sets its own goals.** A human writes only a *charter*: mission, constraints, tool whitelist, budget.
  The agent generates every concrete goal, each with completion criteria that can be checked from observations.
  The charter is read-only to the agent; it can only propose changes, which are recorded.
- **Self-reports are data.** Before each action the agent states what it expects to observe, how confident it is,
  and what it knows it does not know. After the action it says whether the expectation held and how surprised it is.
  `minloop inspect` turns that into a calibration score.
- **The trace is the source of truth.** Full LLM requests and responses, full tool outputs, every goal change.
  Memory is derived from it; a recorded run can be re-executed deterministically.
- **Small and dependency-free.** Python ≥ 3.10, standard library only. Sizes are measured below.

## Quickstart

No dependencies to install. From a fresh clone:

```bash
cd minloop

# 1. Watch a recorded run (no API key, no GPU):
PYTHONPATH=src python -m minloop replay examples/recorded/hello/trace.jsonl

# 2. Run the loop again with the recorded LLM answers; the tools really execute in a fresh workspace:
PYTHONPATH=src python -m minloop replay examples/recorded/hello/trace.jsonl --rerun --root runs-replay

# 3. Run the example against any OpenAI-compatible endpoint (llama.cpp, vLLM, Ollama, a cloud API):
PYTHONPATH=src python -m minloop run examples/hello/charter.json \
    --base-url http://localhost:8080/v1 --model <model-name>
```

For a hosted API, put the key in `OPENAI_API_KEY` (or name another variable with `--api-key-env`) and pass the
provider's base URL. `pip install -e .` gives you a `minloop` command instead of `PYTHONPATH=src python -m minloop`.

The bundled recording was made with the scripted mock LLM (`examples/hello/mock_script.json`). It shows how the loop
works; it says nothing about how well any model does the task.

The example asks the agent to summarize three short meeting notes and to list contradictions between them. A run
writes `runs/<run_id>/` with `trace.jsonl`, `notes.jsonl`, `state.json`, `artifacts/` and the `workspace/` the agent
worked in. `minloop inspect runs/<run_id>/trace.jsonl` prints a summary and the goal tree.

## The charter

```json
{
  "mission": "Read the meeting notes under notes/ ... and write summary.md ...",
  "constraints": ["Work only inside the workspace.", "Do not modify the files under notes/."],
  "tools": ["fs_list", "fs_read", "fs_write", "finish", "give_up", "ask_human", "propose_charter_change"],
  "budget": {"max_steps": 12, "max_tokens": 120000},
  "stop": {"no_progress_steps": 4},
  "workspace": "workspace"
}
```

| field | meaning |
|---|---|
| `mission` | what the agent works toward (required) |
| `constraints` | rules, in plain language; shown to the model at every call |
| `tools` | whitelist; default: `fs_list fs_read fs_write finish give_up ask_human propose_charter_change` |
| `budget` | `max_steps`, `max_tokens`, `max_seconds`, `max_cost`: **at least one is required**, otherwise the run refuses to start (0 counts as unset). Command-line flags of the same name override them. |
| `stop` | `no_progress_steps` (default 5): stop after that many steps in a row without progress |
| `reflect` | deep-reflection triggers: `surprise` (0.7), `failures` (2), `every` (5 steps) |
| `allow` | `{"shell": true}` / `{"network": true}`: required to enable the `shell` / `http_get` tools |
| `workspace` | directory (relative to the charter) copied into the run as its initial workspace |
| `task` | optional `{"text", "criteria"}`: task mode, the human sets the goal (for baselines) |

## How it works

```
  charter (human, read-only)
        |
        v
 +------------------- kernel: budget / stop guards / checkpoint ----------------------+
 |   GOAL --> PLAN --> ACT --> OBSERVE --> REFLECT --+                                |
 |    ^                                             | goal: keep / revise / sub-goal; |
 |    +---------------------------------------------+ finish / give_up close it       |
 +------------------------------------------------------------------------------------+
        | every event                                  | lessons / facts / self-knowledge
        v                                              v
   trace.jsonl (append-only)                      notes.jsonl (long-term memory)
```

| module | does | does not |
|---|---|---|
| `goal.py` | goal stack (sub-goals) and queue; every goal needs criteria | act |
| `plan.py` | one next step, with expectation, confidence, known unknowns | call tools |
| `act.py` | whitelist, argument validation, timeout, call | call the LLM or decide |
| `observe.py` | truncated view for the model, error type, rule-based surprise | touch memory |
| `reflect.py` | light reflection every step; deep reflection (writes notes) on triggers | act |
| `memory.py` | working context from trace events; notes read/write/select | call the LLM |
| `trace.py` | event schema, JSONL writer and reader, artifacts | — |
| `kernel.py` | the loop, budgets, stop guards, checkpoint/resume, Ctrl-C, the JSON protocol | domain logic |
| `llm/` | `openai_compat`, `replay`, `mock` adapters | prompt design |
| `prompts/` | `goal.md`, `plan.md`, `reflect.md`, `deep_reflect.md` | — |

A step costs two LLM calls (plan, light reflection). Goals are generated at the start and whenever no goal is active.
A deep reflection runs when a goal closes, when surprise is high, after repeated tool failures, and every few steps.

**Tool calls are JSON text.** The model answers with one JSON object; minloop parses it, validates it against a
schema, and on failure retries (twice) with the errors quoted back. No native function calling is needed, so any
chat endpoint works and every model is driven through the same protocol.

**Progress is judged by rule, not by self-report:** a step makes progress if it closes a goal or produces a
successful tool result not seen before in the run.

**Memory** has three layers, all plain files: working memory is rebuilt from the trace for every call; the trace
itself is the episodic memory; `notes.jsonl` holds `world`, `skill` and `self` notes written by deep reflection.
Notes are never edited; a wrong note gets a `supersede` record. `--notes FILE` shares notes across runs.

## Tools

| tool | default | |
|---|---|---|
| `fs_list`, `fs_read`, `fs_write` | on | paths are confined to the run's `workspace/` |
| `finish` | on | closes the current goal; must include a check of each criterion |
| `give_up` | on | abandons the current goal; must say why |
| `ask_human` | on | records the question; in an autonomous run nobody answers |
| `propose_charter_change` | on | records a proposal; the charter does not change |
| `idle` | off | records that there is nothing useful to do |
| `http_get` | **off** | needs `"allow": {"network": true}` |
| `shell` | **off** | needs `"allow": {"shell": true}` |

A tool is a function `fn(args, ctx) -> ToolResult` registered with `@tool(name, description, params_schema)`.
`ctx` holds only the workspace path, the run id and a trace handle.

## Budgets, stopping, resuming

- At least one budget is required. All limits are checked before every step and before every LLM call.
- `--forever` ignores `max_steps` and `max_seconds`, and refuses to start without `max_tokens` or `max_cost` and the
  no-progress stop. Cost is only counted if you give prices (`--price-in`, `--price-out`, per million tokens);
  a `max_cost` budget without prices is refused. A goal round that yields no usable goal adds to the no-progress
  counter, and three such rounds in a row stop the run.
- A run ends with a `stop` event: `done`, `budget`, `no_progress`, `interrupt` (Ctrl-C) or `error`. State is saved
  after every step and at the stop.
- A step cut short by a budget, Ctrl-C or an error still ends with its `budget_tick` and checkpoint, so the
  trace and `state.json` always agree.
- `minloop run --resume runs/<run_id>` continues with a fresh session budget and a reset no-progress counter, always
  with the charter stored in `state.json`. Working memory is rebuilt from the trace. A non-empty run directory is
  never reused by a plain `run` or `replay --rerun`.

## Replay and inspection

- `minloop replay TRACE` prints a run step by step.
- `minloop replay TRACE --rerun --root DIR` re-executes the loop: the replay adapter feeds back the recorded LLM
  responses in order and checks that every request is identical to the recorded one. Two reruns give byte-identical
  traces once timestamps and durations (`ts`, `ms`) are removed.
- `--from-step K --model ...` replays the recording before step K and lets a live model take over from there
  (counterfactual replay; combine with `--prompts DIR` for prompt ablations).
- `minloop inspect TRACE` prints steps, tokens, tool error rate, `finish` / `give_up` / `ask_human` counts, mean
  confidence, a Brier score (confidence vs. "expectation met") and the goal tree.

Event schema: [docs/trace-schema.md](docs/trace-schema.md). Design notes: [docs/design.md](docs/design.md).

## Probes

`probes/` is the evaluation side: five families of situations in which an agent should give up, ask, say "unknown"
or notice that something changed, each judged from the trace by rules first and by an LLM judge only where rules
cannot decide. It perturbs the environment (wrapped tools, canary strings), never the kernel. For now it runs offline
only, with scripted mock agents and deterministic replay: `PYTHONPATH=src python -m probes demo`. No model has been
probed yet. See [probes/README.md](probes/README.md).

## Experiments

`experiments/` runs the probe suite against minloop and two baselines on the same cells, tools and judge: a minimal
greedy tool loop (no goals, no self-report, no reflection) and a bare model answering in one reply without tools.
So far it has only driven mock scripts: [experiments/RESULTS-mock.md](experiments/RESULTS-mock.md) is a check of the
harness, not a result. [experiments/PROTOCOL.md](experiments/PROTOCOL.md) describes the real experiment, how its
numbers stay checkable, and what it is waiting for.

## Safety

- The workspace is a **path check, not a sandbox**: it prevents honest mistakes, not a determined process.
- Shell and network access are off by default. Turning either on takes an explicit `allow` flag in the charter, and
  the run prints a warning and records it in the trace. **If you turn them on, run minloop inside a container or VM.**
- There is no telemetry. Nothing leaves your machine except the requests to the LLM endpoint you configure.

## What it is not

- Not a general orchestration framework: no graph DSL, no multi-agent orchestration, no UI, no plugins, no hosted
  service, no RAG stack.
- Not a production coding agent, and not tuned for coding benchmarks.
- Not unconstrained: autonomy here means self-generated goals inside a human-written charter, with budgets and stop
  conditions.

## Size

Measured with `wc -l` (blank lines and docstrings included) on 0.1.0.dev0:

| part | files | lines |
|---|---|---|
| loop core | `kernel.py goal.py plan.py act.py observe.py reflect.py memory.py trace.py schema.py __init__.py` | 760 |
| LLM adapters | `llm/` (`base`, `openai_compat`, `replay`, `mock`) | 178 |
| command line | `__main__.py` (`run`, `replay`, `inspect`) | 215 |
| **package without tools** | `src/minloop/` minus `tools/` | **1153** |
| built-in tools | `tools/` | 214 |
| prompts | `prompts/*.md` (text) | 67 |

The original goal was at most 800 lines for the package without tools. The loop core alone meets it; the package as a
whole does not, because the command line and the four LLM adapters live inside it.

## Development

```bash
pip install pytest
pytest
```

The tests cover a full mock run, deterministic replay (including the bundled recording), budgets and stop
conditions, Ctrl-C and resume, the safety gates, tools, the schema subset and the HTTP adapter (against a local stub
server), the probes (`tests/test_probe_*.py`) and the experiment harness (`tests/test_experiments.py`).

## License

Apache-2.0. See [LICENSE](LICENSE).
