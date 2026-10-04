"""Command-line entry point: nimble-triage [FILE ...]."""

from __future__ import annotations

import argparse
import fileinput
import json
import math
import os
import sys
from collections.abc import Iterator
from typing import TextIO

from nimble_triage import __version__
from nimble_triage.client import DEFAULT_MODEL, SystemOneClient, TriageError
from nimble_triage.triage import (
    DEFAULT_THRESHOLD,
    Asker,
    AttentionResult,
    TriageResult,
    triage_attention_line,
    triage_line,
)

MAX_LINE_CHARS = 8_000  # keeps every request well under the 64 KiB body limit
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nimble-triage",
        description=(
            "Label every log line with a severity, a category and a needs-attention flag, "
            "using the Nimble decision model running locally in Ollama."
        ),
        epilog="example: tail -f app.log | nimble-triage --format pretty --flagged-only",
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
        choices=("full", "attention"),
        default="full",
        help=(
            "classification mode: full asks for severity, category, and attention; "
            "attention asks only whether an entry needs attention"
        ),
    )
    parser.add_argument(
        "-a",
        "--flagged-only",
        dest="flagged_only",
        action="store_true",
        help="print only entries at or above the attention threshold",
    )

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


def read_entries(files: list[str]) -> Iterator[str]:
    """Yield non-blank, stripped lines from the files, or stdin when none are given."""
    with fileinput.input(
        files=files or ("-",), encoding="utf-8", errors="replace"
    ) as lines:
        for raw in lines:
            text = raw.strip()

            if text:
                yield text[:MAX_LINE_CHARS]


def escape_terminal_controls(text: str) -> str:
    """Render terminal control characters as visible escape sequences."""
    return "".join(
        character
        if character.isprintable()
        else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


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
            else:
                result = triage_line(client, line, args.threshold)
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
        elif args.mode == "attention":
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        client = SystemOneClient(
            host=args.host,
            model=args.model,
            timeout=args.timeout,
        )
        return run(args, client, sys.stdout, sys.stderr)
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
