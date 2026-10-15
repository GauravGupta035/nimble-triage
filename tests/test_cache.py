"""Persistent pattern classification cache."""

import sqlite3

import nimble_triage.cache as cache_module
from nimble_triage.cache import ClassificationCache
from nimble_triage.patterns import normalize_pattern
from nimble_triage.triage import Classification


def classification() -> Classification:
    return Classification("error", 0.9, "network", 0.8, 0.95)


def test_cache_round_trip_and_model_isolation(tmp_path):
    path = tmp_path / "cache.sqlite3"
    pattern, _ = normalize_pattern("ERROR host 10.0.0.1 failed")

    with ClassificationCache(path) as cache:
        assert cache.get(pattern, "host|model-a") is None
        cache.store(pattern, "host|model-a", classification())
        assert cache.get(pattern, "host|model-a") == classification()
        assert cache.get(pattern, "host|model-b") is None


def test_cache_does_not_store_raw_examples(tmp_path):
    path = tmp_path / "cache.sqlite3"
    raw_entry = "ERROR user secret@example.com request failed"
    pattern, _ = normalize_pattern(raw_entry)

    with ClassificationCache(path) as cache:
        cache.store(pattern, "host|model", classification())

    connection = sqlite3.connect(path)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(classifications)")
    }
    values = connection.execute("SELECT * FROM classifications").fetchone()
    connection.close()

    assert "examples" not in columns
    assert values is not None
    assert "secret@example.com" not in pattern.text
    assert all(
        not isinstance(value, str) or raw_entry not in value
        for value in values
    )


def test_question_change_invalidates_cached_result(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite3"
    pattern, _ = normalize_pattern("ERROR connection failed")

    with ClassificationCache(path) as cache:
        cache.store(pattern, "host|model", classification())
        assert cache.get(pattern, "host|model") == classification()

        monkeypatch.setattr(cache_module, "QUESTIONS_HASH", "changed")
        assert cache.get(pattern, "host|model") is None
