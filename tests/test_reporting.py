"""Pattern report orchestration and output contracts."""

import pytest

from nimble_triage.cache import ClassificationCache
from nimble_triage.client import TriageError
from nimble_triage.entries import LogEntry
from nimble_triage.reporting import build_pattern_report


def entry(text: str, line_number: int) -> LogEntry:
    return LogEntry(text, "app.log", line_number, False, len(text))


def test_report_classifies_each_unique_pattern_once(fake_client):
    client = fake_client()
    report = build_pattern_report(
        [
            entry("ERROR host 10.0.0.1 failed", 1),
            entry("ERROR host 10.0.0.2 failed", 2),
            entry("INFO request complete", 3),
        ],
        client,
    )

    assert report.scanned_entries == 3
    assert len(report.items) == 2
    assert report.model_requests == 2
    assert len(client.calls) == 2


def test_uncached_report_does_not_resolve_cache_identity(fake_client):
    client = fake_client()

    def fail_if_called():
        raise AssertionError("cache identity should not be resolved")

    client.resolve_model_identity = fail_if_called

    report = build_pattern_report([entry("ERROR failed", 1)], client)

    assert report.model_requests == 1


def test_warm_cache_avoids_model_requests(fake_client, tmp_path):
    entries = [entry("ERROR host 10.0.0.1 failed", 1)]
    path = tmp_path / "cache.sqlite3"

    with ClassificationCache(path) as cache:
        first_client = fake_client()
        first = build_pattern_report(entries, first_client, cache=cache)
        second_client = fake_client()
        second = build_pattern_report(
            entries,
            second_client,
            cache=cache,
            threshold=0.99,
        )

    assert first.model_requests == 1
    assert second.model_requests == 0
    assert second.cache_hits == 1
    assert second.attention_patterns == 0
    assert second_client.calls == []


def test_continue_on_error_retains_failed_pattern(fake_client):
    def fail(_state):
        from nimble_triage.client import TriageError

        raise TriageError("model unavailable")

    report = build_pattern_report(
        [entry("ERROR failed", 1), entry("ERROR failed", 2)],
        fake_client(fail),
        continue_on_error=True,
    )

    assert report.failed_patterns == 1
    assert report.failed_occurrences == 2
    assert report.items[0].error == "model unavailable"


def test_json_contract_contains_summary_and_sources(fake_client):
    report = build_pattern_report(
        [entry("ERROR database unavailable", 7)],
        fake_client(),
    )

    data = report.to_dict()

    assert data["schema_version"] == 1
    assert data["summary"]["distinct_patterns"] == 1
    assert data["patterns"][0]["first_source"] == {
        "source": "app.log",
        "line_number": 7,
        "truncated": False,
        "original_length": 26,
    }


def test_report_orders_attention_by_severity_then_occurrences(
    fake_client,
    make_answers,
):
    def respond(state):
        if "critical" in state:
            return make_answers(severity="critical", attention=0.9)
        return make_answers(severity="warning", attention=0.9)

    report = build_pattern_report(
        [
            entry("WARNING repeated", 1),
            entry("WARNING repeated", 2),
            entry("CRITICAL critical", 3),
        ],
        fake_client(respond),
    )

    assert [item.classification.severity for item in report.sorted_items()] == [
        "critical",
        "warning",
    ]


def test_report_stops_before_exceeding_model_request_limit(fake_client):
    client = fake_client()

    with pytest.raises(TriageError, match="3 uncached model evaluations"):
        build_pattern_report(
            [entry(f"event {number}", number) for number in range(3)],
            client,
            max_model_requests=2,
        )

    assert client.calls == []


def test_report_emits_progress(fake_client):
    messages = []

    build_pattern_report(
        [entry("ERROR repeated", 1), entry("ERROR repeated", 2)],
        fake_client(),
        progress=messages.append,
    )

    assert messages == [
        "Scanned 2 entries, found 1 distinct pattern, 1 uncached pattern to evaluate",
        "Evaluating pattern 1/1 (2 occurrences)",
    ]
