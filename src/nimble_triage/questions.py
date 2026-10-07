"""Question sets used by nimble-triage's full and attention modes.

This wording is the model's only guidance, so treat it like code: when you change
it, re-run tests/fixtures/sample.log and compare. Descriptions must be plain
strings, because Ollama rejects object-valued criteria.
"""

from __future__ import annotations

from typing import Any

# Ordered lowest to highest. SEVERITIES relies on this order.
SEVERITY_CRITERIA: dict[str, str] = {
    "debug": "Diagnostic detail only useful while debugging. No effect on users or the system.",
    "info": "Normal operation. A routine event that went as expected.",
    "warning": "Something unexpected or degraded, but the system kept working or recovered on its own.",
    "error": "An operation failed. A request, job or task did not complete.",
    "critical": "A service or core dependency is down, data is at risk, or many users are affected.",
}

CATEGORY_CRITERIA: dict[str, str] = {
    "auth": (
        "Logins, passwords, sessions, tokens, API keys, permissions, access denied."
    ),
    "database": "Database queries, connections, migrations, replication, locks.",
    "network": (
        "Connectivity, DNS, TLS, sockets, or calls to another host or service "
        "failing or timing out."
    ),
    "performance": "Slowness, latency, caching, or pressure on CPU, memory, disk or queues.",
    "security": "Attacks, suspicious or malicious activity, vulnerabilities, blocked exploits.",
    "application": (
        "The application's own code and business logic: exceptions, failed jobs, "
        "deploys, feature events."
    ),
    "other": "Fits none of the categories above.",
}

SEVERITIES: tuple[str, ...] = tuple(SEVERITY_CRITERIA)
CATEGORIES: tuple[str, ...] = tuple(CATEGORY_CRITERIA)

ATTENTION_INSTRUCTIONS = (
    "Should an on-call engineer look at this log entry soon? "
    "Answer yes for failures that need action, outages, data loss, security threats, "
    "or problems that will get worse without a fix (for example a certificate about to expire). "
    "Answer no for routine events, successful deploys, debug output, a single failed login "
    "or other expected user mistake, and transient errors that are being retried automatically."
)

ATTENTION_QUESTION: dict[str, Any] = {
    "type": "noul",
    "instructions": ATTENTION_INSTRUCTIONS,
    "criteria": {
        "false": "No, safe to ignore",
        "true": "Yes, a human should look at it",
    },
}

SEVERITY_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": "How severe is this log entry?",
    "criteria": SEVERITY_CRITERIA,
}

CATEGORY_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": "Which area of the system is this log entry about?",
    "criteria": CATEGORY_CRITERIA,
}

FULL_QUESTIONS: dict[str, dict[str, Any]] = {
    "severity": SEVERITY_QUESTION,
    "category": CATEGORY_QUESTION,
    "needs_attention": ATTENTION_QUESTION,
}

ATTENTION_QUESTIONS: dict[str, dict[str, Any]] = {
    "needs_attention": ATTENTION_QUESTION,
}

DETAIL_QUESTIONS: dict[str, dict[str, Any]] = {
    "severity": SEVERITY_QUESTION,
    "category": CATEGORY_QUESTION,
}

# Compatibility alias for code written against nimble-triage 0.1 and 0.2.
QUESTIONS = FULL_QUESTIONS
