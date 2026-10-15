"""Normalize high-cardinality log values and aggregate repeated patterns."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from nimble_triage.entries import LogEntry

NORMALIZER_VERSION = 2
DEFAULT_MAX_EXAMPLES = 3

_ISO_TIMESTAMP_PREFIX = re.compile(
    r"^\s*[\[(]?"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"[\])]?(?:\s+|\s*[-|]\s*)"
)
_SYSLOG_TIMESTAMP_PREFIX = re.compile(
    r"^\s*(?P<timestamp>"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
)
_APACHE_TIMESTAMP_PREFIX = re.compile(
    r"^\s*\[(?P<timestamp>"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\]\s*"
)
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?P<key>password|passwd|token|api[_-]?key|authorization|secret)"
    r"(?P<separator>\s*[=:]\s*)(?P<quote>[\"']?)"
    r"(?P<value>[^\s,;]+)(?P=quote)"
)
_EXPLICIT_ID = re.compile(
    r"(?i)\b(?P<key>request[_-]?id|trace[_-]?id|span[_-]?id|"
    r"correlation[_-]?id|event[_-]?id|job[_-]?id|task[_-]?id|"
    r"session[_-]?id)(?P<separator>\s*[=:]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[A-Za-z0-9._:/+\-]{6,})(?P=quote)"
)
_LONG_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{16,}\b")
_IPV4 = re.compile(
    r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])"
)
_IPV6_CANDIDATE = re.compile(
    r"(?<![\w:])\[?(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]+\]?(?![\w:])"
)
_EMBEDDED_ISO_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_CONTEXTUAL_NUMERIC_ID = re.compile(
    r"(?i)(?P<prefix>\b(?:child|pid|process(?:\s+id)?|scoreboard\s+slot)\s+)"
    r"\d+\b"
)


@dataclass(frozen=True)
class NormalizedPattern:
    text: str
    fingerprint: str
    normalizer_version: int = NORMALIZER_VERSION


@dataclass
class PatternAggregate:
    pattern: NormalizedPattern
    occurrences: int = 0
    examples: list[LogEntry] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    first_source: LogEntry | None = None
    last_source: LogEntry | None = None

    def add(
        self,
        entry: LogEntry,
        timestamp: str | None,
        max_examples: int,
    ) -> None:
        self.occurrences += 1
        if self.first_source is None:
            self.first_source = entry
            self.first_seen = timestamp
        self.last_source = entry
        if timestamp is not None:
            if self.first_seen is None:
                self.first_seen = timestamp
            self.last_seen = timestamp
        if len(self.examples) < max_examples:
            self.examples.append(entry)


def _replace_explicit_id(match: re.Match[str]) -> str:
    return f"{match.group('key')}{match.group('separator')}<ID>"


def _replace_sensitive_value(match: re.Match[str]) -> str:
    return f"{match.group('key')}{match.group('separator')}<REDACTED>"


def _replace_long_hex(match: re.Match[str]) -> str:
    value = match.group(0)
    unprefixed = value[2:] if value.casefold().startswith("0x") else value
    if value.casefold().startswith("0x") or any(
        character in "abcdefABCDEF" for character in unprefixed
    ):
        return "<ID>"
    return value


def _replace_ipv6(match: re.Match[str]) -> str:
    candidate = match.group(0)
    address = candidate.strip("[]")
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return candidate
    return "<IP>" if parsed.version == 6 else candidate


def _extract_leading_timestamp(text: str) -> tuple[str, str | None]:
    for expression in (
        _ISO_TIMESTAMP_PREFIX,
        _SYSLOG_TIMESTAMP_PREFIX,
        _APACHE_TIMESTAMP_PREFIX,
    ):
        match = expression.match(text)
        if match:
            return text[match.end() :].strip(), match.group("timestamp")
    return text, None


def normalize_pattern(text: str) -> tuple[NormalizedPattern, str | None]:
    """Return a stable pattern and an optional leading timestamp."""

    normalized, timestamp = _extract_leading_timestamp(text)
    normalized = _UUID.sub("<ID>", normalized)
    normalized = _EMAIL.sub("<EMAIL>", normalized)
    normalized = _SENSITIVE_VALUE.sub(_replace_sensitive_value, normalized)
    normalized = _EXPLICIT_ID.sub(_replace_explicit_id, normalized)
    normalized = _LONG_HEX.sub(_replace_long_hex, normalized)
    normalized = _IPV4.sub("<IP>", normalized)
    normalized = _IPV6_CANDIDATE.sub(_replace_ipv6, normalized)
    normalized = _EMBEDDED_ISO_TIMESTAMP.sub("<TIMESTAMP>", normalized)
    normalized = _CONTEXTUAL_NUMERIC_ID.sub(r"\g<prefix><ID>", normalized)
    normalized = " ".join(normalized.split())
    if not normalized:
        normalized = "<TIMESTAMP>" if timestamp else "<EMPTY>"

    digest_input = f"{NORMALIZER_VERSION}\0{normalized}".encode()
    fingerprint = hashlib.sha256(digest_input).hexdigest()
    return NormalizedPattern(normalized, fingerprint), timestamp


def aggregate_entries(
    entries: Iterable[LogEntry],
    max_examples: int = DEFAULT_MAX_EXAMPLES,
) -> dict[str, PatternAggregate]:
    """Group entries by normalized fingerprint, preserving insertion order."""

    if max_examples < 0:
        raise ValueError("max_examples must not be negative")

    aggregates: dict[str, PatternAggregate] = {}
    for entry in entries:
        pattern, timestamp = normalize_pattern(entry.text)
        aggregate = aggregates.setdefault(
            pattern.fingerprint,
            PatternAggregate(pattern=pattern),
        )
        aggregate.add(entry, timestamp, max_examples)
    return aggregates
