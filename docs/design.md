# Design

This is the short public version of the design behind minloop v0.1. Everything here is a default that can change.

## Position

minloop is a research kernel, not a product. One fixed loop, small enough to read in one sitting, where every step is
recorded so that a run can be replayed, inspected and evaluated offline. Its first use is measuring whether an
autonomous agent knows what it does not know, so the agent's self-reports (confidence, expectations, known unknowns)
are first-class data.

It is not a general orchestration framework, not a multi-agent system, not a production coding agent, and not
"unconstrained": autonomy means the agent sets and pursues its own goals, not that it runs without budget, sandbox or
stop conditions.

## The loop

```
  human writes only the charter (mission + constraints + tool whitelist + budget)
        |
        v
 +------------------- kernel: budget / stop guards / checkpoint ----------------------+
 |                                                                                    |
 |   GOAL --> PLAN --> ACT --> OBSERVE --> REFLECT --+                                |
 |    ^                                             | goal: keep / revise / sub-goal; |
 |    +---------------------------------------------+ finish / give_up close it       |
 +------------------------------------------------------------------------------------+
        | every event                                  | lessons / facts / self-knowledge
        v                                              v
   trace.jsonl (append-only, source of truth)     notes.jsonl (long-term memory)
```

One step is one plan, one act, one observe and one light reflection: two LLM calls. Goals are generated at the start
and whenever no goal is active; deep reflection runs on triggers.

## Data objects

- **Charter** (human-written, read-only during the run): mission, constraints, tools, budget, stop conditions,
  optional `allow` flags, optional initial workspace, optional `task` (task mode: the human gives the first goal).
- **Goal**: id, text, checkable completion criteria (required), source (charter / self / reflect), status
  (pending / active / blocked / done / abandoned), parent.
- **PlanStep**: intent, tool and arguments, expected observation, confidence, known unknowns.
- **Observation**: full raw result (in the trace), truncated view for the model, normalized error type, rule-based
  surprise.
- **Reflection** (light): expectation met, surprise, lesson, goal action, calibration remark.
  (deep): summary, notes to write, notes to supersede.

The expectation and confidence in each PlanStep, together with the reflection's verdict, are what makes offline
calibration measurement (e.g. Brier score, see `minloop inspect`) possible.

## Defaults and their alternatives

| Question | Default | Alternative kept in mind |
|---|---|---|
| Who sets goals? | A human writes only the charter; the agent generates every concrete goal, each with checkable completion criteria. The agent cannot edit the charter; it can only propose a change, which is recorded. | Task mode (a human gives the goal) exists for baseline comparisons. A charter-less agent is not supported: it could not be evaluated. |
| Does it run forever? | No. At least one budget (steps / tokens / wall-clock / cost) is required. "Continuous" operation is checkpoint + resume. `--forever` drops the step and time limits but requires a token or cost cap and the no-progress stop. | A resident daemon, later. |
| How often does it reflect? | Two levels: a light structured reflection every step; a deep one (the only writer of notes) when surprise is high, tools fail repeatedly, a goal closes, or every K steps. | Episode-end reflection only (cheaper, but no per-step calibration data); folding the light reflection into the next plan call (not implemented). |
| Which LLM / tool protocol? | Any OpenAI-compatible chat endpoint. Tool calls are JSON text validated locally, with retries that quote the errors. | Native function calling as an optional adapter (not implemented). |
| Dependencies? | Python, standard library only, including HTTP and a small JSON Schema subset. | pydantic + httpx: faster to write, two more things to break. |
| One agent or many? | One. Sub-goals are a stack inside the same loop. | Sub-agents per sub-goal, later. |
| Where do prompts live? | One text file per call kind in `src/minloop/prompts/`, English, replaceable with `--prompts DIR` (for ablations). | Prompts in code. |
| Safety boundary? | No shell, no network by default. Enabling either needs an explicit `allow` flag in the charter and prints a loud warning. No built-in container sandbox. | A built-in container sandbox (would add a Docker dependency). |

## Memory

| layer | content | where | lifetime |
|---|---|---|---|
| working | charter, goals, last steps, selected notes, budget left | rebuilt for every call | never stored; rebuilt from the trace on resume |
| episodic | every event of every step | `trace.jsonl` | one file per run, append-only |
| notes | world / skill / self notes written by deep reflection | `notes.jsonl` | survives runs (`--notes`); superseded, never edited |

Note retrieval uses no vector store: the most recent notes, keyword matches with the current goal, and the titles of
the rest. The plan can ask for any listed note in full through its `recall` field.

## Observability hooks for evaluation

1. Structured self-reports in every plan and reflection.
2. One observation point: subscribe to events (`Kernel(on_event=[...])`).
3. Perturbations (rewriting a tool result, removing a tool, planting contradictions or canary strings) are done by
   wrapping tools or preparing the workspace, never by changing the kernel.
4. The trace schema is versioned; probes depend on it, not on internal classes.

## Not done yet

Native function calling, the one-call-per-step mode, an Anthropic adapter, an MCP adapter, a resident daemon, sub-agents.
The probe framework and the experiment harness exist but have only been run offline, with mock scripts; see
`probes/README.md` and `experiments/PROTOCOL.md`.
