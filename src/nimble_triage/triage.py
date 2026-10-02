"""Turn raw /v1/systemone answers into a typed TriageResult"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from nimble_triage.client import TriageError
from nimble_triage.questions import QUESTIONS, SEVERITIES

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
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in asdict(self).items()}


def parse_answers(
    line: str, answers: dict[str, Any], threshold: float = DEFAULT_THRESHOLD
) -> TriageResult:
    try:
        severity = answers["severity"]
        category = answers["category"]
        attention = float(answers["needs_attention"]["noul"])
        result = TriageResult(
            severity=severity["choice"],
            severity_confidence=float(severity["confidence"]),
            category=category["choice"],
            category_confidence=float(category["confidence"]),
            attention_probability=attention,
            needs_attention=attention >= threshold,
            line=line,
        )

    except (KeyError, TypeError, ValueError) as exc:
        raise TriageError(f"unexpected answer shape from the model: {exc!r}") from exc

    if result.severity not in SEVERITIES:
        raise TriageError(f"model returned unknown severity {result.severity!r}")

    return result


def triage_line(client: Asker, line: str, threshold: float = DEFAULT_THRESHOLD) -> TriageResult:
    return parse_answers(line, client.ask(line, QUESTIONS), threshold)
