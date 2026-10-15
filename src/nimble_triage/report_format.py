"""Render pattern summaries and classified reports."""

from __future__ import annotations

import json

from nimble_triage.patterns import PatternAggregate
from nimble_triage.reporting import (
    REPORT_SCHEMA_VERSION,
    PatternReport,
    PatternReportItem,
)
from nimble_triage.text import escape_terminal_controls


def _counted(count: int, singular: str) -> str:
    if count == 1:
        word = singular
    else:
        word = {"entry": "entries"}.get(singular, singular + "s")
    return f"{count:,} {word}"


def source_label(item: PatternReportItem) -> str:
    aggregate = item.aggregate
    first = aggregate.first_source
    last = aggregate.last_source
    if first is None or last is None:
        return ""
    first_label = f"{first.source}:{first.line_number}"
    last_label = f"{last.source}:{last.line_number}"
    if first_label == last_label:
        return f"Source: {first_label}"
    return f"Sources: {first_label} to {last_label}"


def _format_report_item(item: PatternReportItem) -> list[str]:
    aggregate = item.aggregate
    pattern = escape_terminal_controls(aggregate.pattern.text)
    if item.classification is None:
        error = escape_terminal_controls(item.error or "classification failed")
        lines = [f"FAILED    {_counted(aggregate.occurrences, 'occurrence')}", pattern]
        location = source_label(item)
        if location:
            lines.append(escape_terminal_controls(location))
        lines.append(f"Error: {error}")
        return lines

    classification = item.classification
    lines = [
        (
            f"{classification.severity.upper():<9} "
            f"{classification.category:<12} "
            f"{_counted(aggregate.occurrences, 'occurrence')}"
        ),
        pattern,
    ]
    if aggregate.first_seen or aggregate.last_seen:
        first = aggregate.first_seen or "unknown"
        last = aggregate.last_seen or "unknown"
        lines.append(f"First: {first}  Last: {last}")
    location = source_label(item)
    if location:
        lines.append(escape_terminal_controls(location))
    return lines


def format_report_pretty(report: PatternReport, include_all: bool = False) -> str:
    """Return an action-oriented aggregate report."""

    lines = [
        f"Scanned {_counted(report.scanned_entries, 'entry')}",
        f"Found {_counted(len(report.items), 'distinct pattern')}",
    ]
    if report.cache_enabled:
        lines.extend(
            [
                f"{_counted(report.cache_hits, 'pattern')} served from cache",
                f"{_counted(report.cache_misses, 'pattern')} evaluated",
            ]
        )
    else:
        lines.append(f"{_counted(report.model_requests, 'pattern')} evaluated")
    attention_verb = "needs" if report.attention_patterns == 1 else "need"
    lines.append(
        f"{_counted(report.attention_patterns, 'pattern')} {attention_verb} attention, "
        f"representing {_counted(report.attention_occurrences, 'occurrence')}"
    )
    if report.failed_patterns:
        lines.append(
            f"{_counted(report.failed_patterns, 'pattern')} failed, representing "
            f"{_counted(report.failed_occurrences, 'occurrence')}"
        )
    lines.append(f"Completed in {report.elapsed_seconds:.2f}s")

    selected = [
        item
        for item in report.sorted_items()
        if include_all or item.needs_attention(report.threshold) or item.error
    ]
    for item in selected:
        lines.append("")
        lines.extend(_format_report_item(item))
    return "\n".join(lines) + "\n"


def format_report_json(report: PatternReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"


def patterns_to_dict(
    scanned_entries: int,
    aggregates: dict[str, PatternAggregate],
) -> dict[str, object]:
    ordered = sorted(
        aggregates.values(),
        key=lambda aggregate: (
            aggregate.occurrences,
            aggregate.pattern.text,
        ),
        reverse=True,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": {
            "scanned_entries": scanned_entries,
            "distinct_patterns": len(aggregates),
        },
        "patterns": [
            {
                "fingerprint": aggregate.pattern.fingerprint,
                "pattern": aggregate.pattern.text,
                "occurrences": aggregate.occurrences,
                "first_seen": aggregate.first_seen,
                "last_seen": aggregate.last_seen,
                "examples": [entry.to_source_dict() for entry in aggregate.examples],
            }
            for aggregate in ordered
        ],
    }


def format_patterns_pretty(
    scanned_entries: int,
    aggregates: dict[str, PatternAggregate],
) -> str:
    data = patterns_to_dict(scanned_entries, aggregates)
    lines = [
        f"Scanned {_counted(scanned_entries, 'entry')}",
        f"Found {_counted(len(aggregates), 'distinct pattern')}",
    ]
    for pattern in data["patterns"]:
        lines.extend(
            [
                "",
                _counted(int(pattern["occurrences"]), "occurrence"),
                escape_terminal_controls(str(pattern["pattern"])),
            ]
        )
    return "\n".join(lines) + "\n"


def format_patterns_json(
    scanned_entries: int,
    aggregates: dict[str, PatternAggregate],
) -> str:
    return (
        json.dumps(
            patterns_to_dict(scanned_entries, aggregates),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
