"""Reproducible 16-message benchmark with count-based quality reporting."""

from pathlib import Path

from evaldiff import Case, OutputContract, Policy, PythonCallableAdapter, Requirement, Suite, evaluate
from evaldiff.reporters import HTMLReporter, JSONReporter, TerminalReporter


MESSAGE_TYPES = ("billing", "technical", "cancellation", "compliance")
INTENTIONALLY_MISSING = {(7, 3), (14, 2)}


def build_suite() -> Suite:
    cases = []
    for index in range(1, 17):
        expected_type = MESSAGE_TYPES[(index - 1) % len(MESSAGE_TYPES)]
        fact_count = 4 if index <= 3 else 3
        facts = [f"fact-{index}-{fact_index}" for fact_index in range(1, fact_count + 1)]
        requirements = [Requirement(
            id="message_type",
            description=f"Classify message as {expected_type}",
            weight=2,
            critical=True,
            evaluator="json_path",
            value={"path": "type", "equals": expected_type},
            dimension="type",
        )]
        requirements.extend(
            Requirement(
                id=f"fact_{fact_index}",
                description=f"Include {fact}",
                evaluator="contains",
                value=fact,
                dimension="fact",
            )
            for fact_index, fact in enumerate(facts, start=1)
        )
        cases.append(Case(
            id=f"message-{index:02d}",
            input={"index": index, "expected_type": expected_type, "facts": facts},
            requirements=requirements,
            forbidden_content=["fabricated-claim"],
            contract=OutputContract(json_schema={
                "type": "object",
                "required": ["type", "message"],
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string"},
                    "message": {"type": "string"},
                },
            }),
        ))
    return Suite(
        name="sixteen-message-benchmark",
        project="evaldiff-validation",
        cases=cases,
        concurrency=4,
        policy=Policy(
            fail_on_unmet_critical=True,
            min_critical_pass_rate=1.0,
            min_coverage=0.95,
            max_format_failures=0,
            max_forbidden_content_matches=0,
        ),
    )


def message_system(payload):
    index = payload["index"]
    included_facts = [
        fact
        for fact_index, fact in enumerate(payload["facts"], start=1)
        if (index, fact_index) not in INTENTIONALLY_MISSING
    ]
    return {"type": payload["expected_type"], "message": " ".join(included_facts)}


def main() -> int:
    run = evaluate(build_suite(), PythonCallableAdapter(message_system))
    output_dir = Path(".evaldiff/benchmark")
    JSONReporter().render(run, output_dir / "sixteen-messages.json")
    HTMLReporter().render(run, output_dir / "sixteen-messages.html")
    TerminalReporter().render(run)
    quality = run.metadata["quality_summary"]
    print(
        "Evaluation of 16 messages: "
        f"{quality['types_correct']}/{quality['types_total']} types correct, "
        f"{quality['facts_correct']}/{quality['facts_total']} facts, and "
        f"{quality['hallucinations']} hallucinations."
    )
    assert quality == {
        "types_correct": 16,
        "types_total": 16,
        "facts_correct": 49,
        "facts_total": 51,
        "hallucinations": 0,
    }
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
