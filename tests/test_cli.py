"""The command line, driven through run() with a fake client and in-memory streams."""

import io
import json

import pytest

from nimble_triage.cli import MAX_LINE_CHARS, build_parser, format_pretty, run
from nimble_triage.triage import parse_answers


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("ERROR db down\n\n   \nINFO user logged in\nWARN cert expires soon\n", encoding="utf-8")
    return path


@pytest.fixture
def keyword_client(fake_client, make_answers):
    """Flags ERROR and WARN lines, like a well-behaved model would."""

    def respond(line):
        if line.startswith("ERROR"):
            return make_answers(severity="error", attention=0.95)
        if line.startswith("WARN"):
            return make_answers(severity="warning", attention=0.8, severity_confidence=0.5)
        return make_answers(severity="info", category="other", attention=0.05)

    return fake_client(respond)


def run_cli(argv, client):
    out, err = io.StringIO(), io.StringIO()
    code = run(build_parser().parse_args(argv), client, out, err)
    return code, out.getvalue(), err.getvalue()


def test_jsonl_one_object_per_non_blank_line(log_file, keyword_client):
    code, out, err = run_cli([str(log_file)], keyword_client)
    rows = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert [r["line"] for r in rows] == ["ERROR db down", "INFO user logged in", "WARN cert expires soon"]
    assert [r["needs_attention"] for r in rows] == [True, False, True]
    assert err == ""  # no summary in jsonl mode


def test_only_attention_filters_output_but_summary_counts_everything(log_file, keyword_client):
    code, out, err = run_cli(["-f", "pretty", "-a", str(log_file)], keyword_client)
    assert len(out.splitlines()) == 2
    assert "INFO user logged in" not in out
    assert err == "3 entries, 2 need attention\n"


def test_threshold_flag_is_applied(log_file, keyword_client):
    _, out, _ = run_cli(["-t", "0.9", str(log_file)], keyword_client)
    assert [json.loads(line)["needs_attention"] for line in out.splitlines()] == [True, False, False]


def test_pretty_has_no_color_when_not_a_terminal(log_file, keyword_client):
    _, out, _ = run_cli(["-f", "pretty", str(log_file)], keyword_client)
    assert "\033[" not in out
    assert out.splitlines()[2].startswith("! warning?")  # low confidence marked


def test_pretty_color_mode(make_answers):
    result = parse_answers("ERROR x", make_answers(severity="error", attention=0.9))
    line = format_pretty(result, color=True)
    assert "\033[31merror" in line
    assert line.endswith("ERROR x")


def test_long_lines_are_truncated_before_sending(tmp_path, fake_client):
    path = tmp_path / "huge.log"
    path.write_text("E" * (MAX_LINE_CHARS + 500) + "\n", encoding="utf-8")
    client = fake_client()
    run_cli([str(path)], client)
    assert len(client.calls[0][0]) == MAX_LINE_CHARS


def test_invalid_bytes_do_not_crash(tmp_path, fake_client):
    path = tmp_path / "bin.log"
    path.write_bytes(b"ERROR bad byte \xff here\n")
    client = fake_client()
    code, _, _ = run_cli([str(path)], client)
    assert code == 0
    assert "\ufffd" in client.calls[0][0]  # the replacement character


@pytest.mark.parametrize("value", ["2", "-0.1", "abc"])
def test_threshold_must_be_a_probability(value, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["-t", value])
    assert exc.value.code == 2
    assert "--threshold" in capsys.readouterr().err
