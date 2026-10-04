"""The command line, driven through run() with a fake client and in-memory streams."""

import io
import json

import pytest

from nimble_triage.cli import (
    MAX_LINE_CHARS,
    build_parser,
    escape_terminal_controls,
    format_attention_pretty,
    format_pretty,
    main,
    run,
)
from nimble_triage.client import TriageError
from nimble_triage.questions import ATTENTION_QUESTIONS
from nimble_triage.triage import (
    AttentionResult,
    parse_answers,
)


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "app.log"
    path.write_text(
        "ERROR db down\n\n   \nINFO user logged in\nWARN cert expires soon\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def keyword_client(fake_client, make_answers):
    """Flags ERROR and WARN lines, like a well-behaved model would."""

    def respond(line):
        if line.startswith("ERROR"):
            return make_answers(severity="error", attention=0.95)
        if line.startswith("WARN"):
            return make_answers(
                severity="warning", attention=0.8, severity_confidence=0.5
            )
        return make_answers(severity="info", category="other", attention=0.05)

    return fake_client(respond)


@pytest.fixture
def failing_client(fake_client, make_answers):
    """Fails on the INFO entry but succeeds on the others."""

    def respond(line):
        if line.startswith("INFO"):
            raise TriageError("temporary model failure\x1b]52;c;Zm9v\x07")

        if line.startswith("WARN"):
            return make_answers(
                severity="warning",
                attention=0.8,
            )

        return make_answers(
            severity="error",
            attention=0.95,
        )

    return fake_client(respond)


def run_cli(argv, client):
    out, err = io.StringIO(), io.StringIO()
    code = run(build_parser().parse_args(argv), client, out, err)
    return code, out.getvalue(), err.getvalue()


def test_jsonl_one_object_per_non_blank_line(log_file, keyword_client):
    code, out, err = run_cli([str(log_file)], keyword_client)
    rows = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert [r["line"] for r in rows] == [
        "ERROR db down",
        "INFO user logged in",
        "WARN cert expires soon",
    ]
    assert [r["needs_attention"] for r in rows] == [True, False, True]
    assert err == ""  # no summary in jsonl mode


def test_flagged_only_filters_output_but_summary_counts_everything(
    log_file, keyword_client
):
    _code, out, err = run_cli(["-f", "pretty", "-a", str(log_file)], keyword_client)
    assert len(out.splitlines()) == 2
    assert "INFO user logged in" not in out
    assert err == "3 entries, 2 need attention\n"


def test_threshold_flag_is_applied(log_file, keyword_client):
    _, out, _ = run_cli(["-t", "0.9", str(log_file)], keyword_client)
    assert [json.loads(line)["needs_attention"] for line in out.splitlines()] == [
        True,
        False,
        False,
    ]


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


def test_terminal_controls_are_escaped(make_answers):
    result = parse_answers(
        "safe\x1b]52;c;Zm9v\x07",
        make_answers(severity="info", attention=0.1),
    )

    output = format_pretty(result, color=False)

    assert "\x1b" not in output
    assert "\x07" not in output
    assert r"\x1b]52;c;Zm9v\x07" in output


def test_terminal_controls_are_escaped_when_color_is_enabled(make_answers):
    result = parse_answers(
        "safe\x1b]52;c;Zm9v\x07",
        make_answers(severity="error", attention=0.9),
    )

    output = format_pretty(result, color=True)

    assert "\x1b]52" not in output
    assert r"\x1b]52;c;Zm9v\x07" in output
    assert "\x1b[31m" in output


def test_printable_unicode_is_not_changed():
    assert escape_terminal_controls("café 東京") == "café 東京"


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "nan", "inf", "-inf", "abc"],
)
def test_timeout_must_be_positive_and_finite(value, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--timeout", value])

    assert exc.value.code == 2
    assert "--timeout" in capsys.readouterr().err


def test_invalid_host_returns_clean_error(capsys):
    code = main(["--host", "localhost:notaport"])

    captured = capsys.readouterr()

    assert code == 1
    assert "invalid Ollama URL" in captured.err
    assert "Traceback" not in captured.err


def test_processing_stops_on_first_error_by_default(
    log_file,
    failing_client,
):
    with pytest.raises(TriageError, match="temporary model failure"):
        run_cli([str(log_file)], failing_client)

    assert [call[0] for call in failing_client.calls] == [
        "ERROR db down",
        "INFO user logged in",
    ]


def test_continue_on_error_processes_remaining_entries(
    log_file,
    failing_client,
):
    code, out, err = run_cli(
        ["--continue-on-error", str(log_file)],
        failing_client,
    )

    rows = [json.loads(line) for line in out.splitlines()]

    assert code == 1
    assert [row["line"] for row in rows] == [
        "ERROR db down",
        "WARN cert expires soon",
    ]
    assert [call[0] for call in failing_client.calls] == [
        "ERROR db down",
        "INFO user logged in",
        "WARN cert expires soon",
    ]

    assert "nimble-triage: entry 2:" in err
    assert "temporary model failure" in err
    assert "\x1b]52" not in err
    assert r"\x1b]52;c;Zm9v\x07" in err


def test_continue_on_error_reports_failures_in_pretty_summary(
    log_file,
    failing_client,
):
    code, out, err = run_cli(
        [
            "--format",
            "pretty",
            "--continue-on-error",
            str(log_file),
        ],
        failing_client,
    )

    assert code == 1
    assert len(out.splitlines()) == 2
    assert "entry 2" in err
    assert err.endswith("3 entries, 2 need attention, 1 failed\n")


def test_continue_on_error_returns_zero_when_nothing_fails(
    log_file,
    keyword_client,
):
    code, out, err = run_cli(
        ["--continue-on-error", str(log_file)],
        keyword_client,
    )

    assert code == 0
    assert len(out.splitlines()) == 3
    assert err == ""


def test_attention_mode_jsonl_has_reduced_fields(
    log_file,
    keyword_client,
):
    code, out, err = run_cli(
        ["--mode", "attention", str(log_file)],
        keyword_client,
    )

    rows = [json.loads(line) for line in out.splitlines()]

    assert code == 0
    assert rows == [
        {
            "attention_probability": 0.95,
            "needs_attention": True,
            "line": "ERROR db down",
        },
        {
            "attention_probability": 0.05,
            "needs_attention": False,
            "line": "INFO user logged in",
        },
        {
            "attention_probability": 0.8,
            "needs_attention": True,
            "line": "WARN cert expires soon",
        },
    ]
    assert err == ""

    assert all(
        questions == ATTENTION_QUESTIONS for _line, questions in keyword_client.calls
    )


def test_attention_mode_combines_with_output_filter(
    log_file,
    keyword_client,
):
    code, out, err = run_cli(
        [
            "--mode",
            "attention",
            "--flagged-only",
            str(log_file),
        ],
        keyword_client,
    )

    rows = [json.loads(line) for line in out.splitlines()]

    assert code == 0
    assert [row["line"] for row in rows] == [
        "ERROR db down",
        "WARN cert expires soon",
    ]
    assert err == ""


def test_attention_mode_pretty_output(
    log_file,
    keyword_client,
):
    code, out, err = run_cli(
        [
            "--mode",
            "attention",
            "--format",
            "pretty",
            str(log_file),
        ],
        keyword_client,
    )

    assert code == 0
    assert out.splitlines() == [
        "! 0.95  ERROR db down",
        "  0.05  INFO user logged in",
        "! 0.80  WARN cert expires soon",
    ]
    assert err == "3 entries, 2 need attention\n"


def test_attention_mode_pretty_escapes_controls():
    result = AttentionResult(
        attention_probability=0.9,
        needs_attention=True,
        line="unsafe\x1b]52;c;Zm9v\x07",
    )

    output = format_attention_pretty(result, color=False)

    assert "\x1b" not in output
    assert "\x07" not in output
    assert r"\x1b]52;c;Zm9v\x07" in output


def test_attention_mode_sends_only_attention_question(
    log_file,
    keyword_client,
):
    code, _, _ = run_cli(
        ["-m", "attention", str(log_file)],
        keyword_client,
    )

    assert code == 0
    assert all(
        questions == ATTENTION_QUESTIONS for _line, questions in keyword_client.calls
    )


def test_legacy_only_attention_alias_still_filters_output(
    log_file,
    keyword_client,
):
    code, out, err = run_cli(
        ["--only-attention", str(log_file)],
        keyword_client,
    )

    assert code == 0
    assert [json.loads(line)["line"] for line in out.splitlines()] == [
        "ERROR db down",
        "WARN cert expires soon",
    ]
    assert err == ""


def test_help_uses_unambiguous_option_names():
    help_text = build_parser().format_help()

    assert "-m {full,attention}" in help_text
    assert "-a, --flagged-only" in help_text
    assert "--only-attention" not in help_text
    assert "--attention-only" not in help_text
