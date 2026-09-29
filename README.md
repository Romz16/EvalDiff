# EvalDiff

EvalDiff is a Python framework for detecting behavioral regressions in LLM, RAG, and AI-agent applications.

It compares responses against expected requirements, output contracts, and approved baselines. Reports highlight coverage, missing facts, lost behaviors, unsupported content, and critical failures.

## Requirements

- Python 3.11 or newer
- Git

## Installation

Clone the repository and enter its directory:

```bash
git clone https://github.com/Romz16/EvalDiff.git
cd EvalDiff
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On Linux or macOS:

```bash
source .venv/bin/activate
```

Install the framework:

```bash
python -m pip install -e .
```

Verify the installation:

```bash
evaldiff --help
```

## First evaluation

Create an example project:

```bash
evaldiff init my-evaluation
cd my-evaluation
```

Run the generated suite:

```bash
evaldiff run evals/example.json --ci
```

Expected output:

```text
EvalDiff: PASS
Overall coverage: 100.0%
Strict case pass rate: 100.0%
```

Reports are written to `.evaldiff/reports/`.

## Create a suite

Create `evals/my_suite.yaml`:

```yaml
project: my-agent
suite: main-regression

system:
  adapter: callable
  target: my_system:respond

policy:
  fail_on_unmet_critical: true
  min_critical_pass_rate: 1.0
  min_coverage: 0.85
  max_format_failures: 0
  max_forbidden_content_matches: 0

cases:
  - id: message-001
    input:
      text: I need to cancel my account

    requirements:
      - id: type
        description: Classify the request as a cancellation
        evaluator: contains
        value: cancellation
        dimension: type
        critical: true

      - id: deadline
        description: State the cancellation deadline
        evaluator: contains
        value: 5 business days
        dimension: fact
        weight: 2

    forbidden_content:
      - guaranteed automatic cancellation
```

Create `evals/my_system.py`:

```python
def respond(payload):
    return "Cancellation request received. Deadline: 5 business days."
```

Run the suite:

```bash
evaldiff run evals/my_suite.yaml --ci
```

## Use a baseline

After reviewing a `PASS` run, copy its identifier:

```text
Run: run_...
```

Mark it as the approved baseline:

```bash
evaldiff baseline set run_... --name last-approved
```

Compare a new version against the baseline:

```bash
evaldiff run evals/my_suite.yaml --baseline last-approved --ci
```

EvalDiff fails the run if a critical requirement disappears, even when the average score improves.

## Count-based quality summary

Requirements marked with `dimension: type` or `dimension: fact` produce summaries such as:

```text
Quality counts: 16/16 types correct, 49/51 facts, 0 hallucinations
```

Run the included reproducible benchmark:

```bash
python examples/message_benchmark.py
```

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | PASS |
| `10` | WARN or REVIEW |
| `20` | Regression or policy failure |
| `30` | Invalid suite or configuration |
| `40` | Runtime or provider failure |

For validation examples and troubleshooting, see the [testing guide](docs/TESTING_GUIDE.md).
