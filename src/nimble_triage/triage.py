"""Validate System One answers and turn them into typed triage results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

from nimble_triage.client import TriageError
from nimble_triage.questions import (
    ATTENTION_QUESTIONS,
    CATEGORIES,
    DETAIL_QUESTIONS,
    FULL_QUESTIONS,
    SEVERITIES,
)

DEFAULT_THRESHOLD = 0.5


class Asker(Protocol):
    """Anything with an ask() like SystemOneClient. Lets tests pass a fake."""

    def ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TriageResult:
    severity: str
    severity_confidence: float
    category: str
    category_confidence: float
    attention_probability: float
    needs_attention: bool
    line: str

    @property
    def severity_rank(self) -> int:
        """0 for debug up to 4 for critical, for sorting and filtering."""
        return SEVERITIES.index(self.severity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "severity_confidence": round(self.severity_confidence, 4),
            "category": self.category,
            "category_confidence": round(self.category_confidence, 4),
            "attention_probability": round(self.attention_probability, 4),
            "needs_attention": self.needs_attention,
            "line": self.line,
        }


@dataclass(frozen=True)
class AttentionResult:
    attention_probability: float
    needs_attention: bool
    line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_probability": round(self.attention_probability, 4),
            "needs_attention": self.needs_attention,
            "line": self.line,
        }


@dataclass(frozen=True)
class ClassificationDetails:
    severity: str
    severity_confidence: float
    category: str
    category_confidence: float


def parse_probability(value: Any, field: str) -> float:
    """Parse and validate a probability returned by the model."""
    if isinstance(value, bool):
        raise TriageError(
            f"unexpected answer shape from the model: "
            f"{field} must be a number between 0 and 1"
        )

    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise TriageError(
            f"unexpected answer shape from the model: "
            f"{field} must be a number between 0 and 1"
        ) from exc

    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise TriageError(
            f"unexpected answer shape from the model: "
            f"{field} must be between 0 and 1, got {value!r}"
        )

    return probability


def parse_detail_answers(
    answers: dict[str, Any],
) -> ClassificationDetails:
    try:
        severity = answers["severity"]
        category = answers["category"]

        severity_choice = severity["choice"]
        category_choice = category["choice"]

        severity_confidence = parse_probability(
            severity["confidence"],
            "severity confidence",
        )
        category_confidence = parse_probability(
            category["confidence"],
            "category confidence",
        )
    except (KeyError, TypeError) as exc:
        raise TriageError(f"unexpected answer shape from the model: {exc!r}") from exc

    if severity_choice not in SEVERITIES:
        raise TriageError(f"model returned unknown severity {severity_choice!r}")

    if category_choice not in CATEGORIES:
        raise TriageError(f"model returned unknown category {category_choice!r}")

    return ClassificationDetails(
        severity=severity_choice,
        severity_confidence=severity_confidence,
        category=category_choice,
        category_confidence=category_confidence,
    )


def parse_full_answers(
    line: str,
    answers: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> TriageResult:
    details = parse_detail_answers(answers)

    try:
        attention_probability = parse_probability(
            answers["needs_attention"]["noul"],
            "attention probability",
        )
    except (KeyError, TypeError) as exc:
        raise TriageError(f"unexpected answer shape from the model: {exc!r}") from exc

    return TriageResult(
        severity=details.severity,
        severity_confidence=details.severity_confidence,
        category=details.category,
        category_confidence=details.category_confidence,
        attention_probability=attention_probability,
        needs_attention=attention_probability >= threshold,
        line=line,
    )


def parse_attention_answers(
    line: str,
    answers: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> AttentionResult:
    try:
        probability = parse_probability(
            answers["needs_attention"]["noul"],
            "attention probability",
        )
    except (KeyError, TypeError) as exc:
        raise TriageError(f"unexpected answer shape from the model: {exc!r}") from exc

    return AttentionResult(
        attention_probability=probability,
        needs_attention=probability >= threshold,
        line=line,
    )


def triage_full_line(
    client: Asker, line: str, threshold: float = DEFAULT_THRESHOLD
) -> TriageResult:
    return parse_full_answers(line, client.ask(line, FULL_QUESTIONS), threshold)


def triage_attention_line(
    client: Asker,
    line: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> AttentionResult:
    return parse_attention_answers(
        line,
        client.ask(line, ATTENTION_QUESTIONS),
        threshold,
    )


def triage_scan_line(
    client: Asker,
    line: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> TriageResult | None:
    attention = triage_attention_line(client, line, threshold)

    if not attention.needs_attention:
        return None

    details = parse_detail_answers(client.ask(line, DETAIL_QUESTIONS))
    return TriageResult(
        severity=details.severity,
        severity_confidence=details.severity_confidence,
        category=details.category,
        category_confidence=details.category_confidence,
        attention_probability=attention.attention_probability,
        needs_attention=True,
        line=line,
    )


# Compatibility aliases for code written against nimble-triage 0.1 and 0.2.
parse_answers = parse_full_answers
triage_line = triage_full_line
