"""Safe normalization and pattern aggregation."""

from nimble_triage.entries import LogEntry
from nimble_triage.patterns import aggregate_entries, normalize_pattern


def entry(text: str, line_number: int = 1) -> LogEntry:
    return LogEntry(text, "app.log", line_number, False, len(text))


def test_high_cardinality_values_share_a_pattern():
    first, first_time = normalize_pattern(
        "2026-10-09 10:14:02 ERROR request_id=abc12345 host 10.1.4.82 failed"
    )
    second, second_time = normalize_pattern(
        "2026-10-09T10:14:03Z ERROR request_id=xyz98765 host 10.1.4.99 failed"
    )

    assert first.text == "ERROR request_id=<ID> host <IP> failed"
    assert first.fingerprint == second.fingerprint
    assert first_time == "2026-10-09 10:14:02"
    assert second_time == "2026-10-09T10:14:03Z"


def test_uuid_long_hash_and_ipv6_are_normalized():
    pattern, _ = normalize_pattern(
        "job 123e4567-e89b-12d3-a456-426614174000 "
        "digest deadbeefdeadbeef from 2001:db8::1"
    )

    assert pattern.text == "job <ID> digest <ID> from <IP>"


def test_email_and_sensitive_values_are_redacted():
    pattern, _ = normalize_pattern(
        "login user@example.com token=super-secret-value failed"
    )

    assert pattern.text == "login <EMAIL> token=<REDACTED> failed"


def test_semantically_relevant_numbers_do_not_merge():
    soon, _ = normalize_pattern("certificate expires in 3 days")
    later, _ = normalize_pattern("certificate expires in 300 days")

    assert soon.fingerprint != later.fingerprint


def test_long_decimal_number_is_not_treated_as_a_hex_hash():
    pattern, _ = normalize_pattern("account 1234567890123456 failed")

    assert pattern.text == "account 1234567890123456 failed"


def test_aggregation_counts_and_bounds_examples():
    entries = [
        entry("ERROR host 10.0.0.1 failed", 1),
        entry("ERROR host 10.0.0.2 failed", 2),
        entry("ERROR host 10.0.0.3 failed", 3),
    ]

    aggregates = aggregate_entries(entries, max_examples=2)
    [aggregate] = aggregates.values()

    assert aggregate.occurrences == 3
    assert [item.line_number for item in aggregate.examples] == [1, 2]
    assert aggregate.first_source == entries[0]
    assert aggregate.last_source == entries[-1]


def test_aggregation_records_first_and_last_timestamps():
    aggregates = aggregate_entries(
        [
            entry("2026-10-09 10:00:00 ERROR host 10.0.0.1 failed", 1),
            entry("2026-10-09 10:05:00 ERROR host 10.0.0.2 failed", 2),
        ]
    )
    [aggregate] = aggregates.values()

    assert aggregate.first_seen == "2026-10-09 10:00:00"
    assert aggregate.last_seen == "2026-10-09 10:05:00"


def test_apache_timestamps_and_process_ids_share_a_pattern():
    first, first_time = normalize_pattern(
        "[Mon Dec 05 03:36:51 2005] [notice] "
        "jk2_init() Found child 2813 in scoreboard slot 7"
    )
    second, second_time = normalize_pattern(
        "[Mon Dec 05 03:36:52 2005] [notice] "
        "jk2_init() Found child 2812 in scoreboard slot 6"
    )

    assert first.text == (
        "[notice] jk2_init() Found child <ID> in scoreboard slot <ID>"
    )
    assert first.fingerprint == second.fingerprint
    assert first_time == "Mon Dec 05 03:36:51 2005"
    assert second_time == "Mon Dec 05 03:36:52 2005"
