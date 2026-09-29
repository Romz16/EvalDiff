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

## Understand the evaluation flow

EvalDiff separates the model input, its actual output, and the expected behavior:

| Item | Purpose |
|---|---|
| `Case.input` | Data sent to the model, RAG pipeline, or agent |
| Adapter result | Actual output produced by the system |
| `requirements` | Facts and behaviors that the output must preserve |
| `contract` | Expected output structure and field types |
| `forbidden_content` | Claims or expressions that must not appear |
| Baseline | A previously approved immutable run |

The normal flow is:

```text
Case.input -> your system -> actual output -> validation -> baseline comparison -> PASS/FAIL
```

## Use EvalDiff inside Python code

The adapter calls your real function. That function may call an LLM, a RAG pipeline, an API, or an agent.

```python
from evaldiff import (
    Case,
    OutputContract,
    Policy,
    PythonCallableAdapter,
    Requirement,
    RunStatus,
    Suite,
    evaluate,
)
from evaldiff.reporters import TerminalReporter


def answer_customer(payload):
    # Replace this body with your real model or agent call.
    if "charged" in payload["message"].lower():
        return {
            "type": "billing",
            "answer": "The transaction will be reviewed by our billing team.",
            "deadline_days": None,
        }

    return {
        "type": "cancellation",
        "answer": "Your account will be cancelled within 5 business days.",
        "deadline_days": 5,
    }


cancellation_case = Case(
    id="cancellation-001",
    input={"message": "I want to cancel my account"},
    requirements=[
        Requirement(
            id="correct-type",
            description="Classify the request as a cancellation",
            evaluator="json_path",
            value={"path": "type", "equals": "cancellation"},
            dimension="type",
            critical=True,
        ),
        Requirement(
            id="deadline",
            description="State the five-business-day deadline",
            evaluator="contains",
            value="5 business days",
            dimension="fact",
            critical=True,
        ),
    ],
    forbidden_content=["guaranteed immediate cancellation"],
    contract=OutputContract(
        json_schema={
            "type": "object",
            "required": ["type", "answer", "deadline_days"],
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string"},
                "answer": {"type": "string"},
                "deadline_days": {"type": "integer", "enum": [5]},
            },
        }
    ),
)

suite = Suite(
    name="customer-service",
    project="my-agent",
    cases=[cancellation_case],
    repetitions=3,
    policy=Policy(
        fail_on_unmet_critical=True,
        min_critical_pass_rate=1.0,
        min_coverage=1.0,
        max_format_failures=0,
        max_forbidden_content_matches=0,
    ),
)

run = evaluate(suite, PythonCallableAdapter(answer_customer))
TerminalReporter().render(run)

if run.status in {RunStatus.FAIL, RunStatus.ERROR}:
    raise RuntimeError(f"AI regression detected: {run.decision_reasons}")
```

For code already running inside an event loop, use `await evaluate_async(...)` instead of `evaluate(...)`.

## Validate an output you already have

If your application has already called the model, wrap the captured output in an adapter:

```python
actual_output = {
    "type": "cancellation",
    "answer": "Your account will be cancelled within 5 business days.",
    "deadline_days": 5,
}

run = evaluate(
    suite,
    PythonCallableAdapter(lambda _: actual_output),
)
```

`actual_output` is the model response. The expected result remains defined by the case requirements, contract, references, and forbidden content.

## Add another test case

Create another `Case` and add it to `Suite.cases`:

```python
billing_case = Case(
    id="billing-001",
    input={"message": "Why was I charged twice?"},
    requirements=[
        Requirement(
            id="correct-type",
            description="Classify the request as billing",
            evaluator="json_path",
            value={"path": "type", "equals": "billing"},
            dimension="type",
            critical=True,
        ),
        Requirement(
            id="manual-review",
            description="Explain that the transaction will be reviewed",
            evaluator="contains",
            value="review",
            dimension="fact",
        ),
    ],
    forbidden_content=["refund guaranteed"],
)

suite = Suite(
    name="customer-service",
    project="my-agent",
    cases=[cancellation_case, billing_case],
)
```

For every new test, provide a unique ID, the system input, atomic requirements, and optionally an output contract, references, tags, and forbidden content.

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

## Store history from Python

The CLI stores every run automatically in `.evaldiff/evaldiff.db`. When using the Python API directly, save and load runs explicitly:

```python
from evaldiff import PythonCallableAdapter, RunStatus, evaluate
from evaldiff.store import SQLiteRunStore

store = SQLiteRunStore(".evaldiff/evaldiff.db")

try:
    baseline = store.load_baseline(
        project=suite.project,
        suite_name=suite.name,
        name="production",
    )
except KeyError:
    baseline = None

run = evaluate(
    suite,
    PythonCallableAdapter(answer_customer),
    baseline=baseline,
)
store.save(run)

# Promote only after review or deployment approval.
if run.status is RunStatus.PASS:
    store.set_baseline(run.id, name="production")
```

Stored runs are immutable. A baseline is only a named pointer to one approved run, so previous results remain available for audits and explicit comparisons.

## CI/CD workflow

On a pull request, restore the approved baseline database and run:

```bash
evaldiff run evals/my_suite.yaml \
  --baseline production \
  --store .evaldiff/evaldiff.db \
  --reports-dir .evaldiff/reports \
  --ci
```

The pipeline should upload `.evaldiff/reports/` even when the evaluation fails. Exit code `20` blocks behavioral regressions. After merge and approval, promote the successful run to `production` and persist the updated SQLite database in private artifact or object storage for the next pipeline run.

The database must be restored between CI jobs. A fresh runner can validate absolute requirements, but it cannot compare historical evolution unless the approved baseline is available.

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
