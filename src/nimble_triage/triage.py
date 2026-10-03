"""Turn raw /v1/systemone answers into a typed TriageResult"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from nimble_triage.client import TriageError
from nimble_triage.questions import CATEGORIES, QUESTIONS, SEVERITIES

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
            k: round(v, 4) if isinstance(v, float) else v
            for k, v in asdict(self).items()
        }

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

def parse_answers(
    line: str,
    answers: dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> TriageResult:
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
        attention_probability = parse_probability(
            answers["needs_attention"]["noul"],
            "attention probability",
        )
    except (KeyError, TypeError) as exc:
        raise TriageError(
            f"unexpected answer shape from the model: {exc!r}"
        ) from exc

    if severity_choice not in SEVERITIES:
        raise TriageError(
            f"model returned unknown severity {severity_choice!r}"
        )

    if category_choice not in CATEGORIES:
        raise TriageError(
            f"model returned unknown category {category_choice!r}"
        )

    return TriageResult(
        severity=severity_choice,
        severity_confidence=severity_confidence,
        category=category_choice,
        category_confidence=category_confidence,
        attention_probability=attention_probability,
        needs_attention=attention_probability >= threshold,
        line=line,
    )

def triage_line(
    client: Asker, line: str, threshold: float = DEFAULT_THRESHOLD
) -> TriageResult:
    return parse_answers(line, client.ask(line, QUESTIONS), threshold)
