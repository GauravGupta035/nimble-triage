# Roadmap

nimble-triage is evolving from a per-entry classifier into a fast, local log
triage tool that reduces large inputs to a short list of recurring patterns
that need attention.

This roadmap describes the intended direction through a stable 1.0 release. It
is not a promise that every item will ship exactly as written. Version scope
may change as the pattern engine is tested against real logs.

## Product direction

The central goal is to make model work proportional to the number of distinct,
previously unseen log patterns, rather than the total number of log entries.
A future report should look similar to this:

```text
Scanned 84,231 entries
Found 317 distinct patterns
289 patterns served from cache
28 patterns evaluated
5 patterns need attention, representing 12,990 occurrences

CRITICAL  database  12,481 occurrences  increasing
Database connection refused for host <IP>
First: 10:14:02  Last: 10:48:31

WARNING   network   416 occurrences
TLS certificate expires in <NUM> days
First: 10:02:14  Last: 10:47:52

ERROR     application  93 occurrences
Payment job <ID> exhausted all retries
First: 10:21:10  Last: 10:48:07
```

The report should answer four questions quickly:

1. What needs attention?
2. How often is it happening?
3. Is it new, recurring, or increasing?
4. Where can the original entries be found?

## Design principles

- **Local by default.** Logs and cached results stay on the user's machine
  unless a remote Ollama host is explicitly configured.
- **Pattern-first processing.** Normalize and aggregate entries before asking
  the model to classify them.
- **Safe normalization.** Do not merge values when the value can change the
  urgency of an event. For example, 3 days until certificate expiry and 300
  days until expiry must not silently share one decision.
- **No raw-log persistence by default.** Store normalized patterns and model
  results in the cache, not original entries that may contain sensitive data.
- **Evidence over assertion.** Reports include counts, time ranges, source
  locations, and representative entries where available.
- **Stable machine output.** Human-readable reports may improve over time, but
  versioned JSON output and exit-code semantics are treated as public APIs.
- **Useful partial results.** A failed classification should not discard
  successfully parsed and classified patterns when continuation is enabled.

## 0.4: Fast pattern reports

The first milestone is a complete pattern-first vertical slice.

Planned work:

- Add deterministic pattern normalization and fingerprinting.
- Normalize clearly high-cardinality values such as UUIDs, request IDs, long
  hashes, trace IDs, timestamps, and IP addresses.
- Keep ordinary numeric values until value-aware normalization is available.
- Aggregate occurrence counts and a bounded set of representative entries.
- Preserve source filename and line-number provenance.
- Classify each distinct pattern once with full severity, category, and
  attention results.
- Add a persistent SQLite cache using the Python standard library.
- Invalidate cached decisions when the model, question definitions,
  normalizer, or output schema changes.
- Add human-readable and aggregate JSON report formats.
- Show cache hits, cache misses, model requests, failures, and elapsed time in
  the summary.
- Add a pattern-only inspection command that does not require Ollama.
- Add tests covering fingerprint stability, false merges, cache invalidation,
  aggregation, and report ordering.

Candidate commands:

```console
nimble-triage report application.log
nimble-triage report --format json application.log
nimble-triage patterns application.log
```

The current no-subcommand CLI should remain available while the new interface
settles.

## 0.5: Ingestion correctness

This milestone broadens the kinds of logs that can be summarized accurately.

Planned work:

- Extract common timestamp formats and report first-seen and last-seen times.
- Read JSON Lines logs and recognize common timestamp, level, message,
  service, trace, and exception fields.
- Group common Python, Java, Go, and JavaScript stack traces into events.
- Support gzip-compressed input.
- Preserve service and source dimensions when aggregating multiple files.
- Report when entries were truncated and retain their original lengths.
- Distinguish physical lines, parsed entries, skipped entries, and patterns in
  summaries.
- Handle missing timestamps, malformed structured entries, invalid UTF-8, and
  partial files predictably.

## 0.6: Operational intelligence

This milestone focuses on finding meaningful changes rather than only listing
current patterns.

Planned work:

- Compare a current report with a previous report or baseline.
- Identify new, resolved, and recurring patterns.
- Detect material rate increases using equal time windows and minimum sample
  sizes.
- Add filtering and ranking controls such as `--top`, `--sort`, `--since`, and
  `--min-count`.
- Add project configuration for custom redaction and normalization rules.
- Allow teams to mark patterns as ignored, expected, or always escalated.
- Support temporary acknowledgements with expiry dates.
- Add value-aware normalization for quantities whose ranges affect urgency,
  such as percentages, durations, retry counts, and capacity values.

Candidate command:

```console
nimble-triage compare previous.json current.log
```

## 0.7: Trust and release readiness

This milestone makes decisions measurable and prepares the public interfaces
for long-term support.

Planned work:

- Build a labelled evaluation corpus with representative operational logs.
- Measure attention precision and recall, plus severity and category accuracy.
- Run regression checks whenever question wording or normalization changes.
- Add cache inspection, pruning, migration, and clearing commands.
- Add `nimble-triage doctor` to verify Ollama connectivity, endpoint support,
  model availability, and a small test request.
- Run tests on supported Python versions and operating systems for every pull
  request.
- Validate distributions before every release.
- Document the candidate versioned JSON schema and exit-code contract.
- Add end-to-end benchmarks for large files and warm-cache scans.

## 1.0: Stable local triage

Version 1.0 should be released when the product meets the following criteria:

- Model work is proportional to unique uncached patterns, not input size.
- Repeating an unchanged scan can be completed without new model requests.
- A 100,000-entry benchmark demonstrates fast local preprocessing, with model
  time reported separately from parsing and aggregation time.
- Raw log contents are not persisted by default.
- Cache invalidation is correct when the model, questions, normalizer, or
  schema changes.
- Plain text, JSON Lines, multiline stack traces, multiple files, compressed
  files, missing timestamps, invalid UTF-8, and partial failures have tested
  behavior.
- Every reported pattern can point to representative source entries when the
  input source supports it.
- Human-readable and aggregate JSON reports have documented semantics.
- The JSON schema and exit codes are versioned and treated as stable public
  interfaces.
- Attention quality is measured against the checked-in evaluation corpus.
- Supported Python versions and operating systems pass continuous integration.
- Installation, setup, privacy, cache behavior, troubleshooting, and migration
  paths are documented.

## Current issues to address along the way

- Scan summaries can undercount attention matches when attention succeeds but
  detail classification fails under `--continue-on-error`.
- Entries are truncated to 8,000 characters without explicit output metadata.
- Results do not yet include source filename and line-number provenance.
- The release workflow runs tests during publication, but pull requests and
  pushes do not currently have a separate continuous-integration workflow.

## Possible later work

These ideas are intentionally outside the 1.0 definition unless user feedback
makes them necessary:

- Windowed aggregation for `tail -f` and other unbounded streams.
- Markdown or HTML report export.
- Editor links for opening representative source entries.
- Optional concurrency where it produces a measured improvement with local
  models.
- Integrations with observability platforms and incident-management tools.
- A graphical interface or hosted service.

Keeping these items outside the initial stable release allows 1.0 to remain a
focused product: a trustworthy, fast, local CLI that turns noisy logs into a
short, evidence-backed action list.

## Contributing

Issues and pull requests are welcome. For roadmap work, include tests that make
the intended behavior explicit. Changes to question wording or pattern
normalization should include before-and-after examples because both can alter
classification or grouping results.
