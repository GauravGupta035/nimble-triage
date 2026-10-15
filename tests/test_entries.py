"""Input provenance and truncation metadata."""

from nimble_triage.entries import read_log_entries


def test_reads_source_line_numbers_and_skips_blanks(tmp_path):
    path = tmp_path / "application.log"
    path.write_text("first\n\n  second  \n", encoding="utf-8")

    entries = list(read_log_entries([str(path)], max_chars=100))

    assert [(entry.text, entry.line_number) for entry in entries] == [
        ("first", 1),
        ("second", 3),
    ]
    assert all(entry.source == str(path) for entry in entries)


def test_reports_truncation_metadata(tmp_path):
    path = tmp_path / "application.log"
    path.write_text("abcdefgh\n", encoding="utf-8")

    [entry] = list(read_log_entries([str(path)], max_chars=5))

    assert entry.text == "abcde"
    assert entry.truncated is True
    assert entry.original_length == 8
