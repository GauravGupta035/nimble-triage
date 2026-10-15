"""Build aggregate reports from normalized log patterns."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from nimble_triage.cache import ClassificationCache
from nimble_triage.client import TriageError
from nimble_triage.entries import LogEntry
from nimble_triage.patterns import PatternAggregate, aggregate_entries
from nimble_triage.triage import (
    DEFAULT_THRESHOLD,
    Asker,
    Classification,
    classify_pattern,
)

REPORT_SCHEMA_VERSION = 1
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class PatternReportItem:
    aggregate: PatternAggregate
    classification: Classification | None
    cache_hit: bool
    error: str | None = None

    def needs_attention(self, threshold: float) -> bool:
        return bool(
            self.classification
            and self.classification.needs_attention(threshold)
        )

    def to_dict(self, threshold: float) -> dict[str, object]:
        aggregate = self.aggregate
        data: dict[str, object] = {
            "fingerprint": aggregate.pattern.fingerprint,
            "pattern": aggregate.pattern.text,
            "occurrences": aggregate.occurrences,
            "cache_hit": self.cache_hit,
            "first_seen": aggregate.first_seen,
            "last_seen": aggregate.last_seen,
            "first_source": (
                aggregate.first_source.to_source_dict(include_text=False)
                if aggregate.first_source
                else None
            ),
            "last_source": (
                aggregate.last_source.to_source_dict(include_text=False)
                if aggregate.last_source
                else None
            ),
            "examples": [entry.to_source_dict() for entry in aggregate.examples],
        }
        if self.classification:
            data.update(self.classification.to_dict(threshold))
        else:
            data.update(
                {
                    "severity": None,
                    "severity_confidence": None,
                    "category": None,
                    "category_confidence": None,
                    "attention_probability": None,
                    "needs_attention": None,
                    "error": self.error,
                }
            )
        return data


@dataclass(frozen=True)
class PatternReport:
    scanned_entries: int
    items: tuple[PatternReportItem, ...]
    cache_hits: int
    cache_misses: int
    model_requests: int
    elapsed_seconds: float
    threshold: float
    cache_enabled: bool

    @property
    def failed_patterns(self) -> int:
        return sum(item.classification is None for item in self.items)

    @property
    def failed_occurrences(self) -> int:
        return sum(
            item.aggregate.occurrences
            for item in self.items
            if item.classification is None
        )

    @property
    def attention_patterns(self) -> int:
        return sum(item.needs_attention(self.threshold) for item in self.items)

    @property
    def attention_occurrences(self) -> int:
        return sum(
            item.aggregate.occurrences
            for item in self.items
            if item.needs_attention(self.threshold)
        )

    def sorted_items(self) -> tuple[PatternReportItem, ...]:
        def key(item: PatternReportItem) -> tuple[int, int, int, str]:
            classification = item.classification
            return (
                1 if item.needs_attention(self.threshold) else 0,
                classification.severity_rank if classification else -1,
                item.aggregate.occurrences,
                item.aggregate.pattern.text,
            )

        return tuple(sorted(self.items, key=key, reverse=True))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "summary": {
                "scanned_entries": self.scanned_entries,
                "distinct_patterns": len(self.items),
                "cache_enabled": self.cache_enabled,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "model_requests": self.model_requests,
                "failed_patterns": self.failed_patterns,
                "failed_occurrences": self.failed_occurrences,
                "attention_patterns": self.attention_patterns,
                "attention_occurrences": self.attention_occurrences,
                "threshold": self.threshold,
                "elapsed_seconds": round(self.elapsed_seconds, 4),
            },
            "patterns": [
                item.to_dict(self.threshold) for item in self.sorted_items()
            ],
        }


def _model_identity(client: Asker) -> str:
    resolver = getattr(client, "resolve_model_identity", None)
    if callable(resolver):
        resolved = resolver()
        if isinstance(resolved, str):
            return resolved
    identity = getattr(client, "model_identity", None)
    if isinstance(identity, str):
        return identity
    model = getattr(client, "model", client.__class__.__name__)
    return str(model)


def build_pattern_report(
    entries: Iterable[LogEntry],
    client: Asker,
    cache: ClassificationCache | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    continue_on_error: bool = False,
    max_examples: int = 3,
    max_model_requests: int | None = None,
    progress: ProgressCallback | None = None,
) -> PatternReport:
    """Aggregate entries, classify cache misses, and return a complete report."""

    started = time.monotonic()
    aggregates = aggregate_entries(entries, max_examples=max_examples)
    scanned_entries = sum(item.occurrences for item in aggregates.values())
    identity = _model_identity(client) if cache else ""
    items: list[PatternReportItem] = []
    pending: list[PatternAggregate] = []
    hits = requests = 0

    for aggregate in aggregates.values():
        classification = cache.get(aggregate.pattern, identity) if cache else None
        if classification is not None:
            hits += 1
            items.append(PatternReportItem(aggregate, classification, True))
        else:
            pending.append(aggregate)

    misses = len(pending)
    if progress:
        pattern_word = "pattern" if len(aggregates) == 1 else "patterns"
        pending_word = "pattern" if misses == 1 else "patterns"
        progress(
            f"Scanned {scanned_entries:,} entries, found {len(aggregates):,} "
            f"distinct {pattern_word}, {misses:,} uncached {pending_word} to evaluate"
        )
    if max_model_requests is not None and misses > max_model_requests:
        raise TriageError(
            f"report needs {misses:,} uncached model evaluations, exceeding the "
            f"safety limit of {max_model_requests:,}; inspect grouping with "
            "'nimble-triage patterns', or set --max-model-requests to a higher "
            "value (0 disables the limit)"
        )

    for index, aggregate in enumerate(pending, start=1):
        requests += 1
        if progress:
            progress(
                f"Evaluating pattern {index:,}/{misses:,} "
                f"({aggregate.occurrences:,} occurrences)"
            )
        try:
            classification = classify_pattern(client, aggregate.pattern.text)
        except TriageError as exc:
            if not continue_on_error:
                raise
            items.append(
                PatternReportItem(aggregate, None, False, error=str(exc))
            )
            continue

        if cache:
            cache.store(aggregate.pattern, identity, classification)
        items.append(PatternReportItem(aggregate, classification, False))

    return PatternReport(
        scanned_entries=scanned_entries,
        items=tuple(items),
        cache_hits=hits,
        cache_misses=misses,
        model_requests=requests,
        elapsed_seconds=time.monotonic() - started,
        threshold=threshold,
        cache_enabled=cache is not None,
    )
