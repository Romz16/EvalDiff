# EvalDiff testing guide

This guide explains how to validate the framework itself and how to test an AI system with EvalDiff. Run the examples from the repository root.

## 1. Prerequisites

- Python 3.11 or newer.
- Git, recommended for tracking versions of the evaluated system.
- PowerShell on Windows or a compatible shell on Linux and macOS.

Check Python:

```bash
python --version
```

## 2. Prepare the environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Linux and macOS:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Confirm that the CLI and example suite are available:

```bash
evaldiff --help
evaldiff doctor examples/regulatory.json
```

The `doctor` command should report `OK` for Python, SQLite, plugins, and the suite.

Without an editable installation, use this on PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m evaldiff --help
```

Or this on Linux and macOS:

```bash
PYTHONPATH=src python -m evaldiff --help
```

## 3. Run the framework test suite

Internal tests are not included in the public repository. They remain local and are ignored by Git to avoid publishing private fixtures, temporary data, or private scenarios.

If a `tests/` directory is available in your development environment, run:

```bash
python -m unittest discover -s tests -v
```

Expected result for the current local suite:

```text
Ran 19 tests

OK
```

The tests cover weighted scoring, repetitions, behavioral diffs, critical requirements, output contracts, unsupported content, immutable SQLite storage, baseline selection, report generation, and CLI commands.

## 4. Run the regulatory example

```bash
evaldiff run examples/regulatory.json --ci
```

Without an editable installation:

```bash
python -m evaldiff run examples/regulatory.json --ci
```

Expected output:

```text
EvalDiff: PASS
Overall coverage: 100.0%
```

The command creates:

- `.evaldiff/evaldiff.db`, containing immutable runs;
- `.evaldiff/reports/<run_id>.json`, the structured artifact;
- `.evaldiff/reports/<run_id>.html`, the visual report.

Open the latest HTML report on PowerShell:

```powershell
$report = Get-ChildItem .evaldiff\reports\*.html | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Invoke-Item $report.FullName
```

## 5. Verify count-based quality reporting

Requirements may declare `dimension: type` or `dimension: fact`. EvalDiff aggregates successful requirements and counts unique evidence of forbidden or unsupported content.

Run the reproducible benchmark:

```bash
python examples/message_benchmark.py
```

Expected summary:

```text
Evaluation of 16 messages: 16/16 types correct, 49/51 facts, and 0 hallucinations.
```

The benchmark also generates:

- `.evaldiff/benchmark/sixteen-messages.json`;
- `.evaldiff/benchmark/sixteen-messages.html`.

The two missing facts belong to messages 7 and 14. The benchmark therefore returns `WARN`, despite all types being correct and no hallucinations being detected.

## 6. Test the baseline and regression workflow

Use a disposable directory so the official example remains unchanged:

```bash
evaldiff init manual-test
cd manual-test
evaldiff run evals/example.json --ci
```

Copy the identifier shown on the `Run` line, then approve it as the baseline:

```bash
evaldiff baseline set run_19abc123_def456 --name last-approved
```

Only `PASS` runs can become baselines by default. The `--allow-non-pass` option provides an explicit override; avoid it in automated pipelines.

To introduce a critical regression, edit `evals/example_system.py` so the summary no longer mentions the 30-day deadline:

```python
def summarize(payload):
    text = payload.get("text", "")
    return {
        "summary": f"A corrected filing is required. {text}".strip(),
        "deadline_days": 30,
    }
```

Run against the baseline:

```powershell
evaldiff run evals/example.json --baseline last-approved --ci
$LASTEXITCODE
```

Expected result:

- status `FAIL`;
- category `LOST` for `notice-001/deadline`;
- reason `critical requirement lost`;
- exit code `20`.

On Linux and macOS, inspect the exit code with `echo $?` immediately after the command.

Restore the deadline statement and run again:

```python
def summarize(payload):
    text = payload.get("text", "")
    return {
        "summary": f"The notice gives a 30-day deadline. {text}".strip(),
        "deadline_days": 30,
    }
```

Expected result: `PASS`, `deadline` classified as `RETAINED`, 100% coverage, and exit code `0`.

## 7. Test output contracts

A contract can validate structure, types, and required fields:

```json
{
  "contract": {
    "schema": {
      "type": "object",
      "required": ["summary", "deadline_days"],
      "additionalProperties": false,
      "properties": {
        "summary": {"type": "string", "minLength": 20},
        "deadline_days": {"type": "integer", "enum": [30]}
      }
    }
  }
}
```

To trigger a failure, omit `deadline_days` or return it as a string:

```python
return {"summary": "The notice gives a 30-day deadline.", "deadline_days": "30"}
```

Expected result: `contract=0.0%`, status `FAIL` when `max_format_failures` is `0`, and error details in the JSON and HTML reports.

## 8. Test unsupported content

Add forbidden content to a case:

```json
{
  "forbidden_content": [
    "automatic approval",
    "guaranteed exemption"
  ]
}
```

Set the policy and make the system return one of those expressions:

```json
{"policy": {"max_forbidden_content_matches": 0}}
```

```python
return "Automatic approval is guaranteed."
```

Expected result: `forbidden_content_matches` above zero, category `UNSUPPORTED` when compared with a clean baseline, and policy status `FAIL`.

This is an objective count of matching forbidden patterns, not a semantic claim rate. For reference-grounded analysis, implement `UnsupportedClaimsJudge`. If references require semantic analysis but no judge is configured, EvalDiff returns `REVIEW` instead of inventing a conclusion.

## 9. Test repetitions and stability

Configure the suite:

```json
{"repetitions": 5, "concurrency": 2}
```

Each case reports:

- `pass_rate`, the share of repetitions that passed;
- `coverage`, mean requirement coverage;
- `system_p95`, 95th-percentile system invocation latency;
- `evaluation_p95`, evaluator latency;
- `total_p95`, queue, invocation, and evaluation latency;
- `cost_mean`, mean cost when supplied by the adapter.

Use repetitions for probabilistic systems. One correct response is not evidence of stability.

## 10. Test an HTTP endpoint

Configure the suite:

```json
{
  "system": {
    "adapter": "http",
    "endpoint": "http://localhost:8000/invoke",
    "timeout_seconds": 30
  }
}
```

EvalDiff sends:

```json
{
  "input": {"text": "case content"},
  "case_id": "notice-001"
}
```

The endpoint may return a direct result:

```json
{"summary": "The deadline is 30 days.", "deadline_days": 30}
```

It may also return an enriched result:

```json
{
  "content": {"summary": "The deadline is 30 days.", "deadline_days": 30},
  "trace": [],
  "usage": {"input_tokens": 20, "output_tokens": 12},
  "cost": 0.001
}
```

Stop the endpoint to verify error handling. The run should record an execution error and the CLI should exit with code `40`.

## 11. Compare runs and regenerate reports

Compare two stored runs:

```bash
evaldiff compare run_previous run_current
```

Regenerate reports without changing historical artifacts:

```bash
evaldiff report run_19abc123_def456 --format terminal
evaldiff report run_19abc123_def456 --format json
evaldiff report run_19abc123_def456 --format html
evaldiff report run_19abc123_def456 --format html --output reports/regression.html
```

## 12. Exit codes

| Code | Meaning | Recommended action |
|---:|---|---|
| `0` | PASS | Continue to the next pipeline stage. |
| `10` | WARN or REVIEW | Inspect warnings or uncertain semantic results. |
| `20` | Regression policy failure | Fix the regression or deliberately revise the policy. |
| `30` | Invalid suite or configuration | Fix JSON, YAML, imports, fields, or paths. |
| `40` | Runtime or provider failure | Check the endpoint, timeout, callable, network, and credentials. |

## 13. CI checklist

- The suite contains representative cases and edge cases.
- Each requirement is atomic and has a justified weight.
- Critical requirements use `critical: true`.
- `fail_on_unmet_critical` remains enabled.
- `min_critical_pass_rate` reflects the required tolerance.
- `min_case_pass_rate` is set when partial success must not pass as a warning.
- The baseline points to a reviewed and approved run.
- Deterministic contracts run before semantic judges.
- Secrets are provided through environment variables, never suite files.
- The repetition count reflects system variability.
- The pipeline preserves `.evaldiff/reports/` as an artifact.
- The pipeline handles exit codes `10`, `20`, `30`, and `40` correctly.

## 14. Troubleshooting

If `evaldiff` is not recognized, activate the virtual environment or use:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m evaldiff --help
```

For `ModuleNotFoundError` from a callable adapter, use `module:function` in `system.target`. The module must be next to the suite or installed in the environment.

If a baseline cannot be found, verify its ID and scope. Baselines are associated with the same `project` and `suite` as the run.

If YAML cannot be loaded, install the project dependencies with `python -m pip install -e .`. JSON suites work without the YAML parser.

If SQLite reports a lock, close any external tool holding an exclusive transaction on `.evaldiff/evaldiff.db`. EvalDiff closes its own connections after each operation.

A `REVIEW` result means semantic or groundedness evaluation requires a judge that is not configured. Configure the judge through the Python API or use a deterministic evaluator when appropriate.

## 15. Recommended validation command sequence

When the local test package is available, run:

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
evaldiff doctor examples/regulatory.json
evaldiff run examples/regulatory.json --ci
```

Every command should complete successfully, and the example should report `EvalDiff: PASS`.
