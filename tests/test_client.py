"""SystemOneClient without a running Ollama."""

import io
import urllib.error

import pytest

from nimble_triage.client import SystemOneClient, TriageError, resolve_host


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("0.0.0.0:11434", "http://0.0.0.0:11434"),
        ("127.0.0.1", "http://127.0.0.1:11434"),
        ("http://localhost:11434/", "http://localhost:11434"),
        ("https://ollama.example.com:443", "https://ollama.example.com:443"),
    ],
)
def test_resolve_host_normalises(given, expected):
    assert resolve_host(given) == expected


def test_resolve_host_reads_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:9999")
    assert resolve_host() == "http://10.0.0.5:9999"


def test_resolve_host_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert resolve_host() == "http://localhost:11434"


def test_oversized_request_rejected_before_sending():
    client = SystemOneClient(host="localhost:1")  # nothing listens there
    with pytest.raises(TriageError, match="byte limit"):
        client.ask("x" * 70_000, {})


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/v1/systemone", code, "err", {}, io.BytesIO(body.encode()))


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        (404, '{"error":"model \\"nimble\\" not found, try pulling it first"}', "ollama pull nimble"),
        (404, "404 page not found", "Upgrade Ollama"),
        (400, '{"error":"criteria must contain 2-26 candidates"}', "criteria must contain"),
        (413, "", "too large"),
        (500, '{"error":"boom"}', "HTTP 500: boom"),
    ],
)
def test_http_errors_become_actionable_messages(code, body, expected):
    message = SystemOneClient()._explain_http_error(_http_error(code, body))
    assert expected in message


def test_unreachable_host_raises_triage_error():
    with pytest.raises(TriageError, match="cannot reach Ollama"):
        SystemOneClient(host="localhost:1", timeout=2).ask("x", {})
