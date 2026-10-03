"""Shared test helpers. pytest loads this file automatically."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


def build_answers(
    severity: str = "error",
    category: str = "network",
    attention: float = 0.9,
    severity_confidence: float = 0.9,
    category_confidence: float = 0.8,
) -> dict[str, Any]:
    """A /v1/systemone 'answers' object shaped like the real thing."""
    return {
        "severity": {
            "type": "choice",
            "choice": severity,
            "confidence": severity_confidence,
        },
        "category": {
            "type": "choice",
            "choice": category,
            "confidence": category_confidence,
        },
        "needs_attention": {"type": "noul", "noul": attention},
    }


class FakeClient:
    """Stands in for SystemOneClient: no network, records every call."""

    def __init__(self, respond: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.respond = respond or (lambda state: build_answers())
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((state, questions))
        return self.respond(state)


@pytest.fixture
def make_answers() -> Callable[..., dict[str, Any]]:
    return build_answers


@pytest.fixture
def fake_client() -> type[FakeClient]:
    return FakeClient
