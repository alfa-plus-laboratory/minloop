# Trace schema (schema_version 1)

A run directory looks like this:

```
runs/<run_id>/
├── trace.jsonl     # append-only event log: the source of truth
├── artifacts/      # long texts (LLM requests, big tool outputs), named by sha256
├── notes.jsonl     # long-term notes (unless --notes points to a shared file)
├── state.json      # checkpoint for resume; derived, not authoritative
└── workspace/      # the sandbox the file tools can see
```

## Envelope

Every line of `trace.jsonl` is one JSON object:

| field | type | meaning |
|---|---|---|
| `v` | int | schema version (`minloop.trace.SCHEMA_VERSION`) |
| `seq` | int | 0, 1, 2, ... across all sessions of the run |
| `ts` | string | UTC wall-clock time, ISO 8601 with milliseconds |
| `type` | string | event type, see below |
| `step` | int or null | the step the event belongs to; `null` for `run_start` and startup warnings. Events between steps (goal generation, `stop`) carry the number of the last completed step (0 before the first step). |

The payload fields of each type sit next to the envelope fields, at the top level. Lines are separated by `\n`
only; read them with `split("\n")`, not `splitlines()` (JSON strings may contain U+2028). Numbers are always finite
(no `NaN` / `Infinity`).

A step that is cut short (budget, Ctrl-C, error) still ends with its `budget_tick`, so every `plan` step number in the
trace is unique and `state.json` never lags behind the trace.

## Text fields and artifacts

Fields marked *text* hold either an inline string or, when the text is longer than 4000 characters, a reference:

```json
{"artifact": "artifacts/<sha256>.txt", "sha256": "<sha256>", "chars": 5293}
```

Nothing is truncated in the trace. Only the view shown to the model (`observe.view`) is shortened.

## Events

| type | payload |
|---|---|
| `run_start` | `run_id`, `session`, `version` (package), `schema_version`, `charter` (full), `llm` (adapter, model, decoding parameters, seed), `budget` (effective for this session), `forever`, `prompts` (sha256 of each prompt file), `workspace_files` (initial workspace content, *text* values; empty on resume) |
| `goal_set` | `goal`: `{id, text, criteria[], source: charter/self/reflect, status, parent, step}` |
| `goal_update` | `goal` (after the change), `action`: activate / revise / block / unblock / done / abandoned |
| `llm_call` | `kind`: goal / plan / reflect / deep, `attempt` (0 = first try, then JSON retries), `request` (*text*: the JSON-encoded messages), `request_sha256`, `response` (*text*), `usage`: `{prompt_tokens, completion_tokens, cost}`, `ms` |
| `plan` | `goal` (id), `plan`: `{intent, tool, args, expected, confidence, unknowns[], recall[]?}` |
| `tool_call` | `tool`, `args` |
| `tool_result` | `tool`, `ok`, `output` (*text*, full), `error` (`"kind: message"` or null), `data`, `cost`, `ms` |
| `observe` | `observation`: `{ok, view, chars, error_type, surprise_rule}` |
| `reflect` | `level`: light / deep, `reflection`; light also has `surprise` (rule value if there is one, else the model's), deep has `trigger` |
| `note_write` | `note`: `{op: add, id, kind: world/skill/self, title, text, confidence, tags, run_id, step, ts}` or `{op: supersede, id, run_id, step, ts}` |
| `budget_tick` | `used`: `{steps, tokens, cost, ms}` for this session, `no_progress` (consecutive steps without progress) |
| `ask_human` | `question` |
| `charter_proposal` | `change`, `reason` |
| `idle` | `reason` |
| `warning` | `message` (e.g. shell/network enabled, no valid JSON after retries) |
| `stop` | `reason`: done / budget / no_progress / interrupt / error, `detail` |

Light reflection fields: `met_expectation` (bool), `surprise` (0–1), `lesson`, `goal_action` (keep / revise / spawn), `new_goal` (for revise and spawn), `calibration`.
Deep reflection fields: `summary`, `notes[]`, `supersede[]`.

Events of unknown types are accepted if their envelope is valid, so tools can add their own.

## Determinism

`ts` and `ms` (at any depth) are the only fields that depend on the clock. `minloop.trace.normalized()` drops them; two
`replay --rerun` runs of the same trace produce byte-identical normalized traces, and the same artifact files. Against
the original recording, only `run_start.llm` differs (it names the replay adapter).

This holds when the recorded session ended for a reason that does not depend on time: done, a step/token/cost budget,
no progress, or an error. A run cut short by Ctrl-C or `max_seconds`, a run that started from a pre-filled shared notes
file, and multi-session (resumed) runs are not reproduced exactly: `--rerun` replays the first session only.

## Versioning

Probes and analysis code should depend on this schema, not on internal classes. Any change that removes or renames a
field, or changes its meaning, bumps `schema_version`. Adding an optional field does not.
