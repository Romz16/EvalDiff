# EvalDiff implementation architecture

This document maps the v0.1 implementation to the architecture specification. The core remains local-first: the CLI and Python API own evaluation, policy, persistence, and report artifacts. A future server or dashboard should consume those artifacts instead of recomputing canonical results.

## Runtime flow

1. `loader.py` parses JSON or YAML into validated domain dataclasses.
2. `adapters.py` invokes a Python callable or HTTP endpoint and normalizes the result.
3. `runner.py` schedules cases and repetitions with an async concurrency limit.
4. `evaluators.py` runs output contracts before requirement and unsupported-claim checks.
5. `runner.py` aggregates repetition-level scores, strict pass rates, separated system/evaluation/queue/total latency, and cost.
6. `diffing.py` compares the current immutable run with a baseline.
7. `policies.py` applies critical-loss and numeric release gates.
8. `store.py` persists the run as an immutable JSON artifact in SQLite.
9. `reporters.py` renders terminal, JSON, and static HTML views from the stored result.

## Module boundaries

| Module | Responsibility |
|---|---|
| `models.py` | Stable typed report and configuration schema |
| `loader.py` | Suite parsing and aliases for documented YAML fields |
| `adapters.py` | System invocation and output normalization |
| `evaluators.py` | Deterministic contracts, requirements, and judge protocols |
| `runner.py` | Async orchestration, repetitions, metrics, and aggregation |
| `diffing.py` | Requirement-aware semantic diff categories |
| `policies.py` | PASS, WARN, and FAIL decisions |
| `store.py` | Immutable runs and mutable named baseline pointers |
| `reporters.py` | Presentation without score recomputation |
| `plugins.py` | Python entry-point discovery and failure isolation |
| `cli.py` | CI-safe commands and documented exit codes |

## Evaluation semantics

Coverage uses the weighted requirement equation from the specification. A missing or errored requirement contributes zero rather than disappearing from the denominator. This prevents evaluator outages from inflating the score. Critical requirements fail closed on the current run, even without a baseline, and can require a 100% repetition pass rate independently from aggregate coverage.

Contract compliance is separate from coverage. Deterministic forbidden-content checks report an integer match count. A true unsupported-claim rate is reserved for reference-grounded semantic analysis through the optional judge protocol. When semantic evidence is required but a judge is absent, the framework returns `REVIEW` with zero confidence instead of manufacturing a binary result.

Repeated runs aggregate each requirement by arithmetic mean. Case pass rate reports how many repetitions satisfied every requirement and the output contract. P50 and P95 use nearest-rank percentiles, which remain deterministic for small CI samples.

System invocation latency is measured separately from evaluation and queue time. Performance policies use system P95 so local evaluator overhead cannot masquerade as provider regression.

## Storage guarantees

Run IDs identify immutable serialized reports. Inserting the same run ID twice is rejected. Baseline names are pointers scoped by project and suite; updating a pointer does not mutate the historical run. Only `PASS` runs are accepted as baselines unless the caller uses an explicit override. SQLite connections are explicitly closed so local and Windows CI jobs do not retain database locks.

The suite fingerprint hashes the complete canonical evaluation definition: cases, inputs, requirements, weights, criticality, contracts, references, repetitions, concurrency, policy, and suite metadata. Baseline pointers and system configuration remain separate concerns.

## Extension points

Python entry points expose adapters, evaluators, reporters, and stores. Semantic requirement judges and unsupported-claim judges are Python protocols that preserve evidence, confidence, and deterministic/probabilistic provenance. Provider-specific implementations can therefore live outside the core package.

## Deferred components

The FastAPI service, React dashboard, Postgres store, JUnit and pull-request reporters, tool-trace evaluation, provider rate limiters, result caching, and security packs are later phases. The current schemas and immutable artifacts are designed so those components can be added without moving canonical evaluation logic into a service.
