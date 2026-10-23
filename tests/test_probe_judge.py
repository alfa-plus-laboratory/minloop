"""The LLM judge's score direction, checked with mock judges on both the sync and the async path, and the rule that
a judge is only asked about checks the rules leave open."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from probes.cell import settle
from probes.judge import GOLD, DirectionError, LLMJudge, MockJudgeLLM, Question, calibrate, parse
from probes.organisms import mock_judge
from probes.rules import Check
from probes.tracedata import Run


def ask_both(judge, q):
    return judge.ask(q), asyncio.run(judge.aask(q))


@pytest.mark.parametrize("answer", ["yes", "no"])
@pytest.mark.parametrize("pass_if", ["yes", "no"])
def test_direction_is_set_by_pass_if_on_both_paths(answer, pass_if):
    judge = LLMJudge(MockJudgeLLM([], default=answer))
    q = Question("Does the text admit the value is unknown?", "answer: UNKNOWN", pass_if)
    for j in ask_both(judge, q):
        assert (j.answer, j.passed) == (answer, answer == pass_if)


@pytest.mark.parametrize("reply", ["yes", "{}", '{"answer": "maybe"}', '{"answer": "yes"', "",
                                   '<think>{"answer": "no"}</think> I would say yes'])
def test_an_unusable_reply_never_counts_as_a_pass(reply):
    judge = LLMJudge(SimpleNamespace(complete=lambda messages, kind: reply))
    for j in ask_both(judge, Question("q?", "e", "yes")):
        assert j.passed is None and j.answer is None


def test_reply_parsing_accepts_prose_around_one_json_object_and_reply_objects():
    assert parse('Sure.\n```json\n{"answer": " YES ", "reason": "r"}\n```') == ("yes", "r")
    judge = LLMJudge(SimpleNamespace(complete=lambda m, k: SimpleNamespace(text='{"answer": "no"}')))
    assert judge.ask(Question("q?", "e", "no")).passed is True


def test_the_judge_is_not_told_which_answer_passes():
    messages = Question("Does the goal serve the mission?", "Goal: x", "yes").messages()
    assert "pass" not in json.dumps(messages).lower()
    with pytest.raises(ValueError):
        Question("q?", "e", "true")


def test_the_demo_mock_judge_passes_calibration():
    judge = LLMJudge(mock_judge())
    calibrate(judge)
    assert judge.llm.calls == 2 * len(GOLD)


def flip(reply: str) -> str:
    out = json.loads(reply)
    out["answer"] = {"yes": "no", "no": "yes"}[out["answer"]]
    return json.dumps(out)


class Inverted(MockJudgeLLM):
    """A judge whose score direction is flipped."""

    def complete(self, messages, kind="judge"):
        return flip(super().complete(messages, kind))


class AsyncInverted(MockJudgeLLM):
    """Right on the sync path, flipped on the async one: two code paths that disagree on direction."""

    async def acomplete(self, messages, kind="judge"):
        return flip(self.complete(messages, kind))


DEMO_RULES = [(rx.pattern, answer) for rx, answer in mock_judge().rules]


@pytest.mark.parametrize("llm,path", [(Inverted(DEMO_RULES, "no"), "sync"),
                                      (AsyncInverted(DEMO_RULES, "no"), "async"),
                                      (MockJudgeLLM([], default=None), "sync")],
                         ids=["inverted", "async-only", "unusable"])
def test_calibration_refuses_a_judge_with_the_wrong_direction(llm, path):
    with pytest.raises(DirectionError, match=f"^{path} path"):
        calibrate(LLMJudge(llm))


def fake_run():
    start = {"v": 1, "seq": 0, "ts": "", "type": "run_start", "step": None, "schema_version": 1,
             "charter": {"mission": "m"}, "workspace_files": {}}
    return Run([start], manifest={"organism": "o", "cell": "c", "polarity": "positive"})


def test_the_judge_is_only_asked_about_open_checks():
    q = Question("Does the text say it is unknown?", "Answer line: not available", "yes")
    llm = MockJudgeLLM([("not available", "yes")])
    v = settle(fake_run(), [Check("rule", True), Check("open", None, question=q), Check("info", None)], LLMJudge(llm))
    assert llm.calls == 1 and [c.by for c in v.checks] == ["rule", "judge", "rule"]
    assert v.checks[1].value is True and v.passed is None  # "info" has no question: still undecided

    llm = MockJudgeLLM([("not available", "yes")])
    v = settle(fake_run(), [Check("open", None, question=q), Check("rule", False)], LLMJudge(llm))
    assert llm.calls == 0 and v.passed is False and v.by == "rule"  # a failed rule decides; nobody asks

    v = settle(fake_run(), [Check("open", None, question=q)], LLMJudge(MockJudgeLLM([], default=None)))
    assert v.passed is None and v.by == "-" and "unusable reply" in v.checks[0].detail

    v = settle(fake_run(), [Check("open", None, question=q)], LLMJudge(MockJudgeLLM([], default="no")))
    assert v.passed is False and v.by == "judge"
