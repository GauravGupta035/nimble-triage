"""Command behavior for pattern inspection and aggregate reports."""

import io
import json

from nimble_triage.cache import ClassificationCache
from nimble_triage.cli import (
    build_patterns_parser,
    build_report_parser,
    run_patterns_command,
    run_report_command,
)


def test_patterns_command_does_not_need_a_client(tmp_path):
    path = tmp_path / "app.log"
    path.write_text(
        "ERROR host 10.0.0.1 failed\nERROR host 10.0.0.2 failed\n",
        encoding="utf-8",
    )
    args = build_patterns_parser().parse_args(["--format", "json", str(path)])
    out = io.StringIO()

    assert run_patterns_command(args, out) == 0
    data = json.loads(out.getvalue())
    assert data["summary"] == {"scanned_entries": 2, "distinct_patterns": 1}
    assert data["patterns"][0]["occurrences"] == 2


def test_report_command_outputs_pretty_summary(tmp_path, fake_client):
    path = tmp_path / "app.log"
    path.write_text(
        "2026-10-09 10:00:00 ERROR host 10.0.0.1 failed\n"
        "2026-10-09 10:01:00 ERROR host 10.0.0.2 failed\n",
        encoding="utf-8",
    )
    args = build_report_parser().parse_args(
        ["--no-cache", "--format", "pretty", str(path)]
    )
    out = io.StringIO()

    assert run_report_command(args, fake_client(), out, None) == 0
    rendered = out.getvalue()
    assert "Scanned 2 entries" in rendered
    assert "Found 1 distinct pattern" in rendered
    assert "1 pattern evaluated" in rendered
    assert "ERROR host <IP> failed" in rendered
    assert "First: 2026-10-09 10:00:00" in rendered


def test_report_command_writes_progress_to_stderr(tmp_path, fake_client):
    path = tmp_path / "app.log"
    path.write_text("ERROR failed\n", encoding="utf-8")
    args = build_report_parser().parse_args(["--no-cache", str(path)])
    err = io.StringIO()

    assert run_report_command(args, fake_client(), io.StringIO(), None, err) == 0
    assert "found 1 distinct pattern, 1 uncached pattern to evaluate" in err.getvalue()


def test_report_command_reuses_cache(tmp_path, fake_client):
    path = tmp_path / "app.log"
    path.write_text("ERROR host 10.0.0.1 failed\n", encoding="utf-8")
    args = build_report_parser().parse_args(["--format", "json", str(path)])
    out = io.StringIO()

    with ClassificationCache(":memory:") as cache:
        assert run_report_command(args, fake_client(), out, cache) == 0
        second_out = io.StringIO()
        second_client = fake_client()
        assert run_report_command(args, second_client, second_out, cache) == 0

    summary = json.loads(second_out.getvalue())["summary"]
    assert summary["cache_hits"] == 1
    assert summary["model_requests"] == 0
    assert second_client.calls == []
