"""Answer parsing and the threshold decision."""

import pytest

from nimble_triage.client import TriageError
from nimble_triage.questions import QUESTIONS
from nimble_triage.triage import parse_answers, triage_line


def test_parses_a_well_formed_answer(make_answers):
    result = parse_answers("disk full", make_answers(severity="critical", attention=0.97))
    assert result.severity == "critical"
    assert result.category == "network"
    assert result.needs_attention is True
    assert result.line == "disk full"


@pytest.mark.parametrize(
    ("attention", "threshold", "expected"),
    [(0.49, 0.5, False), (0.5, 0.5, True), (0.51, 0.5, True), (0.11, 0.05, True), (0.99, 1.0, False)],
)
def test_threshold_decides_needs_attention(make_answers, attention, threshold, expected):
    result = parse_answers("x", make_answers(attention=attention), threshold)
    assert result.needs_attention is expected
    assert result.attention_probability == attention  # the model's number is never changed


def test_severity_rank_follows_order(make_answers):
    ranks = [parse_answers("x", make_answers(severity=s)).severity_rank for s in ("debug", "error")]
    assert ranks == [0, 3]


def test_to_dict_rounds_floats_but_keeps_bools(make_answers):
    data = parse_answers("x", make_answers(attention=0.123456789)).to_dict()
    assert data["attention_probability"] == 0.1235
    assert data["needs_attention"] is False
    assert list(data)[-1] == "line"


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"severity": None},
        {"needs_attention": {"noul": "not a number"}},
    ],
)
def test_malformed_answers_raise_triage_error(make_answers, broken):
    with pytest.raises(TriageError, match="unexpected answer shape"):
        parse_answers("x", {**make_answers(), **broken} if broken else broken)


def test_unknown_severity_rejected(make_answers):
    with pytest.raises(TriageError, match="unknown severity 'panic'"):
        parse_answers("x", make_answers(severity="panic"))


def test_triage_line_sends_the_line_and_our_questions(fake_client):
    client = fake_client()
    triage_line(client, "ERROR boom")
    assert client.calls == [("ERROR boom", QUESTIONS)]
