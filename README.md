# nimble-triage

Pipe your logs through a local decision model. In the default full mode, every
entry gets a **severity**, a **category** and a **needs-attention** flag,
computed by [Nimble](https://ollama.com/library/nimble) through Ollama's
`/v1/systemone` endpoint. No API key is required. By default, logs are sent only
to Ollama running on your own machine.

```console
$ nimble-triage --format pretty app.log
  error     auth        0.06  ERROR auth: invalid password for user alice@example.com
! warning   network     1.00  WARN tls: certificate for api.example.com expires in 3 days
  info      other       0.05  INFO http GET /health 200 2ms
  error?    application 0.11  ERROR payment-worker: retry 1/5 failed, retrying in 2s
! critical  database    1.00  CRITICAL kernel: Out of memory: Killed process 4121 (postgres)
5 entries, 2 need attention
```

`!` marks entries that meet the attention threshold. The number is the model's
attention score, and `?` marks a severity confidence below `0.6`. Scores help
rank entries but should not be interpreted as calibrated guarantees.

## Requirements

- Python 3.10 or newer
- [Ollama](https://ollama.com) 0.35.0 or newer (the first release with `/v1/systemone`)
- The Nimble model, about a 9 GB download (16 GB of RAM recommended):

  ```bash
  ollama pull nimble
  ```

## Install

```bash
pipx install nimble-triage    # recommended for command-line tools
# or
pip install nimble-triage
```

nimble-triage has no dependencies outside the Python standard library.

## Usage

```bash
# Full classification
nimble-triage application.log                  # JSON Lines to stdout
nimble-triage -f pretty app.log                # aligned, coloured output
nimble-triage -f pretty -a app.log             # only entries that need attention
tail -f app.log | nimble-triage -f pretty -a   # live
grep -v DEBUG app.log | nimble-triage          # pre-filter to save time
nimble-triage app.log | jq 'select(.needs_attention)'
nimble-triage --continue-on-error app.log      # keep going after failed entries

# Faster attention classification
nimble-triage --mode attention application.log

# Full classification, but print only flagged entries
nimble-triage --flagged-only application.log

# Fast classification and print only flagged entries
nimble-triage --mode attention --flagged-only application.log

# Fast scan: find flagged entries, then add severity and category
nimble-triage --mode scan application.log

# Group repeated entries and classify each uncached pattern once
nimble-triage report application.log
nimble-triage report --format json application.log

# Inspect grouping without starting Ollama
nimble-triage patterns application.log
```

| Option | Default | Meaning |
|---|---|---|
| `FILE ...` | stdin | Log files to read. `-` also means stdin. |
| `-f, --format` | `jsonl` | `jsonl` or `pretty` |
| `-t, --threshold` | `0.5` | Attention probability at or above which an entry is flagged |
| `--model` | `nimble` | Ollama model to use |
| `--host` | `$OLLAMA_HOST` or `http://localhost:11434` | Where Ollama is running |
| `--timeout` | `120` | Seconds to wait for each answer |
| `--continue-on-error` | off | Report failed entries to stderr and continue; exit with code 1 if any fail |
| `-m, --mode {full,attention,scan}` | `full` | Select full, attention-only, or attention-first scan mode |
| `-a, --flagged-only` | off | Print only entries at or above the attention threshold |

### Fast pattern reports

`nimble-triage report` normalizes high-cardinality values, groups matching
entries, and sends each distinct uncached pattern to Nimble once. It preserves
occurrence counts, timestamps when a supported leading timestamp is present,
and representative source locations.

Before model work starts, report progress is written to stderr with the number
of entries, distinct patterns, and uncached evaluations. Progress continues as
each pattern is evaluated. By default, a report stops before making more than
50 uncached model requests. Use `--max-model-requests N` to select another cap,
or `--max-model-requests 0` to disable the safeguard.

```console
$ nimble-triage report application.log
Scanned 84,231 entries
Found 317 distinct patterns
289 patterns served from cache
28 patterns evaluated
5 patterns need attention, representing 12,990 occurrences
Completed in 57.31s

CRITICAL  database     12,481 occurrences
Database connection refused for host <IP>
First: 2026-10-09 10:14:02  Last: 2026-10-09 10:48:31
Sources: application.log:18 to application.log:83911
```

Pretty reports show patterns needing attention and failed classifications by
default. Pass `--all` to include patterns below the attention threshold. The
JSON report always contains every pattern and includes `schema_version` for
downstream consumers.

Classifications are cached in SQLite. The default path is
`~/Library/Caches/nimble-triage/classifications.sqlite3` on macOS,
`$XDG_CACHE_HOME/nimble-triage/classifications.sqlite3` when
`XDG_CACHE_HOME` is set, and `~/.cache/nimble-triage/classifications.sqlite3`
on other Unix-like systems. Use `--cache PATH` to select another database or
`--no-cache` for an uncached run. Cache keys include the normalized pattern,
Ollama endpoint, model name and digest when available, question definitions,
normalizer version, and classification schema version. Changing the attention
threshold does not require reclassification.

`nimble-triage patterns` runs only normalization and aggregation. It never
connects to Ollama, which makes it useful for inspecting grouping rules before
running a report.

The first normalizer deliberately keeps ordinary numbers distinct. It replaces
UUIDs, explicit request and trace identifiers, contextual process identifiers,
long hexadecimal hashes, common ISO, syslog, and Apache timestamps, IP
addresses, email addresses, and common credential values. This avoids merging
events whose numeric values change their meaning, such as a certificate
expiring in 3 days versus 300 days.

### JSON Lines output

One object per non-blank input line:

```json
{"severity": "warning", "severity_confidence": 0.9624, "category": "network", "category_confidence": 0.94, "attention_probability": 0.9996, "needs_attention": true, "line": "WARN tls: certificate for api.example.com expires in 3 days"}
```

- `severity`: one of `debug`, `info`, `warning`, `error`, `critical`
- `category`: one of `auth`, `database`, `network`, `performance`, `security`, `application`, `other`
- `*_confidence`, `attention_probability`: numbers between 0 and 1
- `needs_attention`: `attention_probability >= --threshold`

Severity and needs-attention are separate judgements: a certificate that expires in three days is only a warning but needs a human, while a single failed login is an error that does not.

### Attention mode output

`--mode attention` sends only the needs-attention question to Nimble instead of
all three questions. Use it when severity and category are not required.

```json
{"attention_probability": 0.9996, "needs_attention": true, "line": "WARN tls: certificate expires in 3 days"}
```

### Scan mode

`--mode scan` first asks only whether each entry needs attention. Entries below
the threshold are discarded. Flagged entries receive a second request for
severity and category, then use the same output schema as full mode. Scan mode
is useful when only a small proportion of the input is expected to need action.
Because scan mode asks the questions in separate requests, its scores and
classifications can differ slightly from full mode.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime error, or at least one failed entry or pattern with `--continue-on-error` |
| 2 | Invalid command-line arguments |
| 130 | Interrupted with Ctrl-C |

By default, nimble-triage stops on the first model or response error. Use
`--continue-on-error` for long streams where partial results are preferable.
Errors are written to stderr, successful results remain on stdout, and the
command exits with code 1 if any entry or report pattern failed.

## How it works

In full mode, each log line is sent to Ollama's `/v1/systemone` endpoint with
three typed questions: two `choice` questions for severity and category, and
one `noul` (yes/no) question for attention.

With `--mode attention`, each line is sent with only the attention question.
With `--mode scan`, each line receives the attention question first, and only
flagged entries receive the severity and category questions.
With `report`, input is normalized and aggregated locally before one full
request is made for each uncached pattern. Successful classifications are
cached immediately so an interrupted run can reuse completed work.
Nimble is a decision model: rather than generating text, it scores the options
and returns probabilities. The wording of the questions lives in
[`questions.py`](src/nimble_triage/questions.py) and is the main lever on
accuracy.

## Privacy

Log entries are sent only to the Ollama server selected by `--host` or
`OLLAMA_HOST`. The default is `http://localhost:11434`, which keeps requests on
the local machine. If you configure another hostname, your logs are transmitted
to that server. Use HTTPS and review logs for credentials or personal data
before sending them to a remote host.

Pattern reports keep representative original entries only in memory and in the
requested output. The persistent cache stores normalized patterns and model
results, not representative raw entries. Normalization redacts common
credential fields and email addresses, but it cannot guarantee removal of all
sensitive application-specific values.

## Limits

- **Speed:** the original full, attention, and scan modes still make at least
  one model request per entry. Use `report` for large files so model work is
  proportional to distinct uncached patterns. Local preprocessing still reads
  every entry and keeps one aggregate plus bounded examples per distinct
  pattern in memory.
- **Independent entries:** each non-blank line is classified independently.
  Pattern reports group normalized single-line entries, but multiline stack
  traces and surrounding log context are not yet combined into events.
- **Long lines:** entries are cut to their first 8,000 characters before being
  sent to Ollama. Pattern-report JSON marks truncated examples and includes
  their original character lengths.
- **Uncalibrated scores:** confidence and attention scores are model outputs,
  not guarantees of correctness. Tune `--threshold` against representative
  logs before relying on it.
- **Human review:** use the results to decide what to inspect first. Do not use
  nimble-triage as the only source for alerting, security decisions, or incident
  response.

## Development

```bash
git clone https://github.com/GauravGupta035/nimble-triage
cd nimble-triage
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest    # runs without Ollama
```

Run development commands while the virtual environment is active. If you also
have a pipx installation, `command -v nimble-triage` may resolve to that older
copy instead of `.venv/bin/nimble-triage`. To make the global command use the
current checkout, install it as an editable pipx package:

```bash
pipx install --force --editable .
nimble-triage --version
```

See [ROADMAP.md](ROADMAP.md) for the planned path from pattern-first reports to
a stable 1.0 release.

## License

MIT
