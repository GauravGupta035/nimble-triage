"""Answer parsing and the threshold decision."""

import pytest

from nimble_triage.client import TriageError
from nimble_triage.questions import ATTENTION_QUESTIONS, QUESTIONS
from nimble_triage.triage import (
    AttentionResult,
    parse_answers,
    parse_attention_answers,
    triage_attention_line,
    triage_line,
)


def test_parses_a_well_formed_answer(make_answers):
    result = parse_answers(
        "disk full", make_answers(severity="critical", attention=0.97)
    )
    assert result.severity == "critical"
    assert result.category == "network"
    assert result.needs_attention is True
    assert result.line == "disk full"


@pytest.mark.parametrize(
    ("attention", "threshold", "expected"),
    [
        (0.49, 0.5, False),
        (0.5, 0.5, True),
        (0.51, 0.5, True),
        (0.11, 0.05, True),
        (0.99, 1.0, False),
    ],
)
def test_threshold_decides_needs_attention(
    make_answers, attention, threshold, expected
):
    result = parse_answers("x", make_answers(attention=attention), threshold)
    assert result.needs_attention is expected
    assert (
        result.attention_probability == attention
    )  # the model's number is never changed


def test_severity_rank_follows_order(make_answers):
    ranks = [
        parse_answers("x", make_answers(severity=s)).severity_rank
        for s in ("debug", "error")
    ]
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


def test_unknown_category_rejected(make_answers):
    with pytest.raises(TriageError, match="unknown category 'made-up'"):
        parse_answers(
            "x",
            make_answers(category="made-up"),
        )


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("severity", "confidence", -0.1, "severity confidence"),
        ("severity", "confidence", 1.1, "severity confidence"),
        ("severity", "confidence", float("nan"), "severity confidence"),
        ("severity", "confidence", float("inf"), "severity confidence"),
        ("severity", "confidence", True, "severity confidence"),
        ("category", "confidence", -0.1, "category confidence"),
        ("category", "confidence", 1.1, "category confidence"),
        ("category", "confidence", float("nan"), "category confidence"),
        ("needs_attention", "noul", -0.1, "attention probability"),
        ("needs_attention", "noul", 1.1, "attention probability"),
        (
            "needs_attention",
            "noul",
            float("nan"),
            "attention probability",
        ),
    ],
)
def test_invalid_model_probabilities_are_rejected(
    make_answers,
    section,
    field,
    value,
    message,
):
    answers = make_answers()
    answers[section][field] = value

    with pytest.raises(TriageError, match=message):
        parse_answers("x", answers)


def test_parse_attention_answers() -> None:
    result = parse_attention_answers(
        "database unavailable",
        {"needs_attention": {"type": "noul", "noul": 0.91}},
        threshold=0.7,
    )

    assert result == AttentionResult(
        attention_probability=0.91,
        needs_attention=True,
        line="database unavailable",
    )


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"needs_attention": None},
        {"needs_attention": {}},
        {"needs_attention": {"noul": "invalid"}},
    ],
)
def test_malformed_attention_answers_raise_triage_error(answers):
    with pytest.raises(TriageError, match="unexpected answer shape"):
        parse_attention_answers("x", answers)


def test_attention_threshold_decides_needs_attention() -> None:
    result = parse_attention_answers(
        "request completed",
        {"needs_attention": {"type": "noul", "noul": 0.69}},
        threshold=0.7,
    )

    assert result.needs_attention is False
    assert result.attention_probability == 0.69


def test_attention_result_to_dict_rounds_probability() -> None:
    result = AttentionResult(
        attention_probability=0.123456789,
        needs_attention=False,
        line="routine",
    )

    assert result.to_dict() == {
        "attention_probability": 0.1235,
        "needs_attention": False,
        "line": "routine",
    }


def test_triage_attention_line_sends_only_attention_question(fake_client):
    client = fake_client(
        lambda _line: {
            "needs_attention": {
                "type": "noul",
                "noul": 0.8,
            }
        }
    )

    result = triage_attention_line(client, "ERROR boom")

    assert result.needs_attention is True
    assert client.calls == [("ERROR boom", ATTENTION_QUESTIONS)]
