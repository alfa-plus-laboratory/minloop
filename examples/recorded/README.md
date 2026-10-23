# Recorded runs

`hello/` is a complete run directory of `examples/hello`, recorded with the **scripted mock LLM**
(`examples/hello/mock_script.json`). It shows how the loop works without any API key or GPU; it says nothing about
how well a real model does the task.

```bash
# print it step by step
PYTHONPATH=src python -m minloop replay examples/recorded/hello/trace.jsonl
# run the loop again with the recorded LLM responses (tools really execute in a fresh workspace)
PYTHONPATH=src python -m minloop replay examples/recorded/hello/trace.jsonl --rerun --root runs-replay
```

Replay checks every request against the recording, so the recording must be regenerated whenever a prompt file, the
context layout or the mock script changes (the test suite will fail until it is):

```bash
rm -rf examples/recorded/hello
PYTHONPATH=src python -m minloop run examples/hello/charter.json --llm mock \
    --mock-script examples/hello/mock_script.json --root examples/recorded --run-id hello --quiet
```
