"""Command-line entry point: nimble-triage [FILE ...]."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Iterator
from typing import TextIO

from nimble_triage import __version__
from nimble_triage.cache import ClassificationCache, default_cache_path
from nimble_triage.client import DEFAULT_MODEL, SystemOneClient, TriageError
from nimble_triage.entries import read_log_entries
from nimble_triage.patterns import DEFAULT_MAX_EXAMPLES, aggregate_entries
from nimble_triage.report_format import (
    format_patterns_json,
    format_patterns_pretty,
    format_report_json,
    format_report_pretty,
)
from nimble_triage.reporting import build_pattern_report
from nimble_triage.text import escape_terminal_controls
from nimble_triage.triage import (
    DEFAULT_THRESHOLD,
    Asker,
    AttentionResult,
    TriageResult,
    triage_attention_line,
    triage_full_line,
    triage_scan_line,
)

MAX_LINE_CHARS = 8_000  # keeps every request well under the 64 KiB body limit
DEFAULT_MAX_MODEL_REQUESTS = 50
LOW_CONFIDENCE = 0.6  # below this, pretty output marks the severity with "?"

RESET = "\033[0m"
BOLD_RED = "\033[1;31m"
SEVERITY_COLORS = {
    "debug": "\033[2m",  # dim
    "info": "",
    "warning": "\033[33m",  # yellow
    "error": "\033[31m",  # red
    "critical": BOLD_RED,
}


def probability(text: str) -> float:
    """argparse type for --threshold: a float between 0 and 1."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None

    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"{value} is not between 0 and 1")

    return value


def positive_seconds(text: str) -> float:
    """argparse type for a positive, finite number of seconds."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None

    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a positive, finite number")

    return value


def nonnegative_integer(text: str) -> int:
    """argparse type for zero or a positive integer."""

    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text!r} must not be negative")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nimble-triage",
        description=(
            "Classify log lines in full, attention, or scan mode using the Nimble "
            "decision model running locally in Ollama."
        ),
        epilog=(
            "example: tail -f app.log | nimble-triage --format pretty --flagged-only; "
            "use 'nimble-triage report --help' for aggregate reports"
        ),
    )

    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="log files to read (default: stdin, '-' also means stdin)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("jsonl", "pretty"),
        default="jsonl",
        help="output format (default: jsonl)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("full", "attention", "scan"),
        default="full",
        help=(
            "classification mode: full classifies every entry; attention asks only "
            "whether an entry needs attention; scan fully classifies flagged entries"
        ),
    )
    parser.add_argument(
        "-a",
        "--flagged-only",
        dest="flagged_only",
        action="store_true",
        help="print only entries at or above the attention threshold",
    )

    # Preserve the 0.1 filtering option without continuing its ambiguous name.
    parser.add_argument(
        "--only-attention",
        dest="flagged_only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=probability,
        default=DEFAULT_THRESHOLD,
        help=f"needs-attention cutoff between 0 and 1 (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--host", help="Ollama URL (default: $OLLAMA_HOST or http://localhost:11434)"
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=120.0,
        help="seconds to wait for each answer (default: 120)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "report failed entries and continue processing "
            "(exit code 1 if any entry fails)"
        ),
    )

    return parser


def _add_report_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-t",
        "--threshold",
        type=probability,
        default=DEFAULT_THRESHOLD,
        help=f"needs-attention cutoff between 0 and 1 (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--host", help="Ollama URL (default: $OLLAMA_HOST or http://localhost:11434)"
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=120.0,
        help="seconds to wait for each answer (default: 120)",
    )


def build_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nimble-triage report",
        description="Group repeated log patterns and classify each uncached pattern once.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="log files to read (default: stdin, '-' also means stdin)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("pretty", "json"),
        default="pretty",
        help="output format (default: pretty)",
    )
    parser.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="include patterns below the attention threshold in pretty output",
    )
    parser.add_argument(
        "--cache",
        default=str(default_cache_path()),
        metavar="PATH",
        help="classification cache path",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not read or write the classification cache",
    )
    parser.add_argument(
        "--max-examples",
        type=nonnegative_integer,
        default=DEFAULT_MAX_EXAMPLES,
        help=f"representative entries kept per pattern (default: {DEFAULT_MAX_EXAMPLES})",
    )
    parser.add_argument(
        "--max-model-requests",
        type=nonnegative_integer,
        default=DEFAULT_MAX_MODEL_REQUESTS,
        metavar="N",
        help=(
            "maximum uncached patterns to evaluate (default: "
            f"{DEFAULT_MAX_MODEL_REQUESTS}; 0 disables the limit)"
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="include failed patterns and continue (exit code 1 if any fail)",
    )
    _add_report_connection_options(parser)
    return parser


def build_patterns_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nimble-triage patterns",
        description="Inspect normalized patterns without connecting to Ollama.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="log files to read (default: stdin, '-' also means stdin)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("pretty", "json"),
        default="pretty",
        help="output format (default: pretty)",
    )
    parser.add_argument(
        "--max-examples",
        type=nonnegative_integer,
        default=DEFAULT_MAX_EXAMPLES,
        help=f"representative entries kept per pattern (default: {DEFAULT_MAX_EXAMPLES})",
    )
    return parser


def read_entries(files: list[str]) -> Iterator[str]:
    """Yield non-blank, stripped lines from the files, or stdin when none are given."""
    for entry in read_log_entries(files, MAX_LINE_CHARS):
        yield entry.text


def format_pretty(result: TriageResult, color: bool) -> str:
    marker = "!" if result.needs_attention else " "
    severity = result.severity + (
        "?" if result.severity_confidence < LOW_CONFIDENCE else ""
    )
    severity = f"{severity:<9}"

    if color:
        severity = f"{SEVERITY_COLORS[result.severity]}{severity}{RESET}"

        if result.needs_attention:
            marker = f"{BOLD_RED}{marker}{RESET}"

    line = escape_terminal_controls(result.line)

    return (
        f"{marker} {severity} {result.category:<11} "
        f"{result.attention_probability:.2f}  {line}"
    )


def format_attention_pretty(result: AttentionResult, color: bool) -> str:
    marker = "!" if result.needs_attention else " "

    if color and result.needs_attention:
        marker = f"{BOLD_RED}{marker}{RESET}"

    line = escape_terminal_controls(result.line)
    return f"{marker} {result.attention_probability:.2f}  {line}"


def run(
    args: argparse.Namespace,
    client: Asker,
    out: TextIO,
    err: TextIO,
) -> int:
    color = args.format == "pretty" and out.isatty() and not os.environ.get("NO_COLOR")
    total = flagged = failed = 0

    for line in read_entries(args.files):
        total += 1

        try:
            if args.mode == "attention":
                result = triage_attention_line(client, line, args.threshold)
            elif args.mode == "scan":
                result = triage_scan_line(client, line, args.threshold)

                if result is None:
                    continue
            else:
                result = triage_full_line(client, line, args.threshold)
        except TriageError as exc:
            if not args.continue_on_error:
                raise

            failed += 1
            message = escape_terminal_controls(str(exc))
            err.write(f"nimble-triage: entry {total}: {message}\n")
            err.flush()
            continue

        if result.needs_attention:
            flagged += 1
        elif args.flagged_only:
            continue

        if args.format == "jsonl":
            out.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        elif isinstance(result, AttentionResult):
            out.write(format_attention_pretty(result, color) + "\n")
        else:
            out.write(format_pretty(result, color) + "\n")

        out.flush()

    if args.format == "pretty":
        summary = f"{total} entries, {flagged} need attention"

        if failed:
            summary += f", {failed} failed"

        err.write(summary + "\n")

    return 1 if failed else 0


def run_patterns_command(
    args: argparse.Namespace,
    out: TextIO,
) -> int:
    entries = read_log_entries(args.files, MAX_LINE_CHARS)
    aggregates = aggregate_entries(entries, max_examples=args.max_examples)
    scanned_entries = sum(item.occurrences for item in aggregates.values())
    if args.format == "json":
        out.write(format_patterns_json(scanned_entries, aggregates))
    else:
        out.write(format_patterns_pretty(scanned_entries, aggregates))
    return 0


def run_report_command(
    args: argparse.Namespace,
    client: Asker,
    out: TextIO,
    cache: ClassificationCache | None,
    err: TextIO | None = None,
) -> int:
    entries = read_log_entries(args.files, MAX_LINE_CHARS)
    report = build_pattern_report(
        entries,
        client,
        cache=cache,
        threshold=args.threshold,
        continue_on_error=args.continue_on_error,
        max_examples=args.max_examples,
        max_model_requests=args.max_model_requests or None,
        progress=(lambda message: print(message, file=err, flush=True)) if err else None,
    )
    if args.format == "json":
        out.write(format_report_json(report))
    else:
        out.write(format_report_pretty(report, include_all=args.include_all))
    return 1 if report.failed_patterns else 0


def _dispatch(argv: list[str]) -> int:
    if argv and argv[0] == "patterns":
        args = build_patterns_parser().parse_args(argv[1:])
        return run_patterns_command(args, sys.stdout)

    if argv and argv[0] == "report":
        args = build_report_parser().parse_args(argv[1:])
        client = SystemOneClient(
            host=args.host,
            model=args.model,
            timeout=args.timeout,
        )
        if args.no_cache:
            return run_report_command(args, client, sys.stdout, None, sys.stderr)
        with ClassificationCache(args.cache) as cache:
            return run_report_command(args, client, sys.stdout, cache, sys.stderr)

    args = build_parser().parse_args(argv)
    client = SystemOneClient(
        host=args.host,
        model=args.model,
        timeout=args.timeout,
    )
    return run(args, client, sys.stdout, sys.stderr)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        return _dispatch(arguments)
    except TriageError as exc:
        message = escape_terminal_controls(str(exc))
        print(f"nimble-triage: error: {message}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # The reader (e.g. `head`) went away. Point stdout at /dev/null so Python's
        # final flush at exit does not raise a second BrokenPipeError.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1
    except OSError as exc:
        message = escape_terminal_controls(f"{exc.filename}: {exc.strerror}")
        print(f"nimble-triage: error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
