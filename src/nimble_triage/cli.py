"""Command-line entry point: nimble-triage [FILE ...]."""

from __future__ import annotations

import argparse
import fileinput
import json
import os
import sys
from collections.abc import Iterator
from typing import TextIO

from nimble_triage import __version__
from nimble_triage.client import DEFAULT_MODEL, SystemOneClient, TriageError
from nimble_triage.triage import DEFAULT_THRESHOLD, Asker, TriageResult, triage_line

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nimble-triage",
        description=(
            "Label every log line with a severity, a category and a needs-attention flag, "
            "using the Nimble decision model running locally in Ollama."
        ),
        epilog="example: tail -f app.log | nimble-triage --format pretty --only-attention",
    )

    parser.add_argument(
        "files", nargs="*", metavar="FILE", help="log files to read (default: stdin, '-' also means stdin)"
    )
    parser.add_argument(
        "-f", "--format", choices=("jsonl", "pretty"), default="jsonl", help="output format (default: jsonl)"
    )
    parser.add_argument(
        "-a", "--only-attention", action="store_true", help="print only entries that need attention"
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=probability,
        default=DEFAULT_THRESHOLD,
        help=f"needs-attention cutoff between 0 and 1 (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default: {DEFAULT_MODEL})")
    parser.add_argument("--host", help="Ollama URL (default: $OLLAMA_HOST or http://localhost:11434)")
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="seconds to wait for each answer (default: 120)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    return parser


def read_entries(files: list[str]) -> Iterator[str]:
    """Yield non-blank, stripped lines from the files, or stdin when none are given."""
    with fileinput.input(files=files or ("-",), encoding="utf-8", errors="replace") as lines:
        for raw in lines:
            text = raw.strip()

            if text:
                yield text[:MAX_LINE_CHARS]


def format_pretty(result: TriageResult, color: bool) -> str:
    marker = "!" if result.needs_attention else " "
    severity = result.severity + ("?" if result.severity_confidence < LOW_CONFIDENCE else "")
    severity = f"{severity:<9}"

    if color:
        severity = f"{SEVERITY_COLORS[result.severity]}{severity}{RESET}"

        if result.needs_attention:
            marker = f"{BOLD_RED}{marker}{RESET}"

    return f"{marker} {severity} {result.category:<11} {result.attention_probability:.2f}  {result.line}"


def run(args: argparse.Namespace, client: Asker, out: TextIO, err: TextIO) -> int:
    color = args.format == "pretty" and out.isatty() and not os.environ.get("NO_COLOR")
    total = flagged = 0

    for line in read_entries(args.files):
        result = triage_line(client, line, args.threshold)
        total += 1

        if result.needs_attention:
            flagged += 1
        elif args.only_attention:
            continue

        if args.format == "jsonl":
            out.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        else:
            out.write(format_pretty(result, color) + "\n")

        out.flush()

    if args.format == "pretty":
        err.write(f"{total} entries, {flagged} need attention\n")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = SystemOneClient(host=args.host, model=args.model, timeout=args.timeout)

    try:
        return run(args, client, sys.stdout, sys.stderr)
    except TriageError as exc:
        print(f"nimble-triage: error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # The reader (e.g. `head`) went away. Point stdout at /dev/null so Python's
        # final flush at exit does not raise a second BrokenPipeError.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1
    except OSError as exc:
        print(f"nimble-triage: error: {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
