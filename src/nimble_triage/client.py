"""Minimal HTTP client for Ollama's /v1/systemone endpoint"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_PORT = 11434
DEFAULT_MODEL = "nimble"
ENDPOINT = "/v1/systemone"
MAX_BODY_BYTES = 64 * 1024  # documented request limit for text-only calls - 64 KiB


class TriageError(Exception):
    """A failure the user can act on. The message is meant to be shown as-is."""


def resolve_host(host: str | None = None) -> str:
    """Return a validated Ollama base URL."""
    raw = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip()

    if "://" not in raw:
        raw = "http://" + raw

    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise TriageError(f"invalid Ollama URL {raw!r}: {exc}") from exc

    if parsed.scheme not in {"http", "https"}:
        raise TriageError(
            f"invalid Ollama URL {raw!r}: scheme must be http or https"
        )

    if not parsed.hostname:
        raise TriageError(
            f"invalid Ollama URL {raw!r}: hostname is missing"
        )

    if parsed.query or parsed.fragment:
        raise TriageError(
            f"invalid Ollama URL {raw!r}: query strings and fragments are not supported"
        )

    if port is None:
        parsed = parsed._replace(
            netloc=f"{parsed.netloc}:{DEFAULT_PORT}"
        )

    parsed = parsed._replace(path=parsed.path.rstrip("/"))

    return urllib.parse.urlunsplit(parsed)

class SystemOneClient:
    """Sends one state plus a set of named questions, returns the answers dict."""

    def __init__(
        self,
        host: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        keep_alive: str = "10m",
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise TriageError(
                f"timeout must be a positive, finite number, got {timeout!r}"
            )

        self.base_url = resolve_host(host)
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive

    def ask(self, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
            "keep_alive": self.keep_alive,
        }

        body = json.dumps(payload).encode("utf-8")

        if len(body) > MAX_BODY_BYTES:
            raise TriageError(
                f"request is {len(body)} bytes, over the {MAX_BODY_BYTES} byte limit of {ENDPOINT}"
            )

        request = urllib.request.Request(
            self.base_url + ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)

        except urllib.error.HTTPError as exc:
            raise TriageError(self._explain_http_error(exc)) from exc

        except urllib.error.URLError as exc:
            raise TriageError(
                f"cannot reach Ollama at {self.base_url} ({exc.reason}). "
                "Is it running? Start it with `ollama serve` or the Ollama app."
            ) from exc

        except TimeoutError as exc:
            raise TriageError(
                f"Ollama did not answer within {self.timeout:g}s. "
                "The first call loads the model and can be slow; try --timeout 300."
            ) from exc

        except json.JSONDecodeError as exc:
            raise TriageError(
                f"Ollama returned a response that is not JSON: {exc}"
            ) from exc

        answers = data.get("answers") if isinstance(data, dict) else None

        if not isinstance(answers, dict):
            raise TriageError(
                f"unexpected response from Ollama, no 'answers' object: {data!r:.200}"
            )

        return answers

    def _explain_http_error(self, exc: urllib.error.HTTPError) -> str:
        raw = exc.read().decode("utf-8", errors="replace").strip()

        try:
            detail = json.loads(raw).get("error", raw)
            is_json = True

        except (json.JSONDecodeError, AttributeError):
            detail, is_json = raw, False

        if exc.code == 404 and is_json:
            return f"model '{self.model}' is not available locally. Run: ollama pull {self.model}"

        if exc.code == 404:
            return f"{self.base_url} has no {ENDPOINT} endpoint. Upgrade Ollama to 0.35 or newer."

        if exc.code == 413:
            return f"log entry too large for {ENDPOINT} (limit {MAX_BODY_BYTES} bytes)"

        if exc.code == 400:
            return f"Ollama rejected the request: {detail}"

        return f"Ollama returned HTTP {exc.code}: {detail}"
