"""Guard the question definitions against limits the Ollama API enforces."""

from nimble_triage.questions import (
    ATTENTION_QUESTIONS,
    CATEGORIES,
    DETAIL_QUESTIONS,
    FULL_QUESTIONS,
    QUESTIONS,
    SEVERITIES,
)


def test_severities_are_ordered_lowest_to_highest():
    assert SEVERITIES == ("debug", "info", "warning", "error", "critical")


def test_question_count_within_api_limit():
    assert 1 <= len(FULL_QUESTIONS) <= 64


def test_choice_questions_have_2_to_26_string_criteria():
    for name, question in FULL_QUESTIONS.items():
        if question["type"] != "choice":
            continue
        criteria = question["criteria"]
        assert 2 <= len(criteria) <= 26, name
        # Ollama rejects object-valued descriptions (ollama/ollama#18718).
        assert all(isinstance(d, str) for d in criteria.values()), name


def test_other_is_a_fallback_category():
    assert "other" in CATEGORIES


def test_attention_questions_contains_only_attention_question() -> None:
    assert set(ATTENTION_QUESTIONS) == {"needs_attention"}

    assert ATTENTION_QUESTIONS["needs_attention"] is FULL_QUESTIONS["needs_attention"]


def test_detail_questions_exclude_attention_question() -> None:
    assert set(DETAIL_QUESTIONS) == {"severity", "category"}
    assert DETAIL_QUESTIONS["severity"] is FULL_QUESTIONS["severity"]
    assert DETAIL_QUESTIONS["category"] is FULL_QUESTIONS["category"]


def test_legacy_questions_name_is_a_compatibility_alias() -> None:
    assert QUESTIONS is FULL_QUESTIONS
