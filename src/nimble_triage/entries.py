"""Read log entries while preserving their source locations."""

from __future__ import annotations

import fileinput
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class LogEntry:
    """One non-blank input entry and the location it came from."""

    text: str
    source: str
    line_number: int
    truncated: bool
    original_length: int

    def to_source_dict(self, include_text: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "source": self.source,
            "line_number": self.line_number,
            "truncated": self.truncated,
            "original_length": self.original_length,
        }
        if include_text:
            data["text"] = self.text
        return data


def read_log_entries(files: list[str], max_chars: int) -> Iterator[LogEntry]:
    """Yield non-blank entries with source metadata from files or standard input."""

    with fileinput.input(
        files=files or ("-",), encoding="utf-8", errors="replace"
    ) as lines:
        for raw in lines:
            text = raw.strip()
            if not text:
                continue

            original_length = len(text)
            yield LogEntry(
                text=text[:max_chars],
                source="<stdin>" if lines.isstdin() else lines.filename(),
                line_number=lines.filelineno(),
                truncated=original_length > max_chars,
                original_length=original_length,
            )
