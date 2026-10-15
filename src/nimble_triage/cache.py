"""Persistent cache for pattern classifications."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from nimble_triage.client import TriageError
from nimble_triage.patterns import NORMALIZER_VERSION, NormalizedPattern
from nimble_triage.questions import FULL_QUESTIONS
from nimble_triage.triage import Classification

CACHE_SCHEMA_VERSION = 1
CLASSIFICATION_SCHEMA_VERSION = 1
QUESTIONS_HASH = hashlib.sha256(
    json.dumps(FULL_QUESTIONS, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def default_cache_path() -> Path:
    """Return the platform-appropriate cache database path."""

    override = os.environ.get("NIMBLE_TRIAGE_CACHE")
    if override:
        return Path(override).expanduser()

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        root = Path(xdg_cache)
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"])
    else:
        root = Path.home() / ".cache"
    return root / "nimble-triage" / "classifications.sqlite3"


def cache_key(pattern: NormalizedPattern, model_identity: str) -> str:
    parts = (
        pattern.fingerprint,
        model_identity,
        QUESTIONS_HASH,
        str(NORMALIZER_VERSION),
        str(CLASSIFICATION_SCHEMA_VERSION),
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


class ClassificationCache:
    """SQLite-backed cache that never stores original log entries."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            if str(path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(path))
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise TriageError(f"cannot open classification cache {path!s}: {exc}") from exc

    def _initialize(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, CACHE_SCHEMA_VERSION):
            raise TriageError(
                f"cache schema version {version} is not supported; "
                f"remove {self.path!s} to rebuild it"
            )

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS classifications (
                cache_key TEXT PRIMARY KEY,
                pattern TEXT NOT NULL,
                model_identity TEXT NOT NULL,
                questions_hash TEXT NOT NULL,
                normalizer_version INTEGER NOT NULL,
                classification_schema_version INTEGER NOT NULL,
                severity TEXT NOT NULL,
                severity_confidence REAL NOT NULL,
                category TEXT NOT NULL,
                category_confidence REAL NOT NULL,
                attention_probability REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
        self.connection.commit()

    def get(
        self,
        pattern: NormalizedPattern,
        model_identity: str,
    ) -> Classification | None:
        try:
            row = self.connection.execute(
                """
                SELECT severity, severity_confidence, category,
                       category_confidence, attention_probability
                FROM classifications
                WHERE cache_key = ?
                """,
                (cache_key(pattern, model_identity),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise TriageError(f"cannot read classification cache: {exc}") from exc

        if row is None:
            return None
        return Classification(
            severity=row[0],
            severity_confidence=row[1],
            category=row[2],
            category_confidence=row[3],
            attention_probability=row[4],
        )

    def store(
        self,
        pattern: NormalizedPattern,
        model_identity: str,
        classification: Classification,
    ) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO classifications (
                        cache_key, pattern, model_identity, questions_hash,
                        normalizer_version, classification_schema_version,
                        severity, severity_confidence, category,
                        category_confidence, attention_probability
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cache_key(pattern, model_identity),
                        pattern.text,
                        model_identity,
                        QUESTIONS_HASH,
                        NORMALIZER_VERSION,
                        CLASSIFICATION_SCHEMA_VERSION,
                        classification.severity,
                        classification.severity_confidence,
                        classification.category,
                        classification.category_confidence,
                        classification.attention_probability,
                    ),
                )
        except sqlite3.Error as exc:
            raise TriageError(f"cannot write classification cache: {exc}") from exc

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ClassificationCache:  # noqa: PYI034 (Self needs 3.11)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
