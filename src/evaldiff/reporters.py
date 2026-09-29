from __future__ import annotations

import html
import json
from pathlib import Path
from typing import TextIO

from .models import DiffCategory, EvalRun


class TerminalReporter:
    def render(self, run: EvalRun, stream: TextIO | None = None) -> str:
        strict_pass_rate = (
            sum(case.pass_rate for case in run.cases) / len(run.cases) if run.cases else 1.0
        )
        quality = run.metadata.get("quality_summary", {})
        lines = [
            f"EvalDiff: {run.status.value}",
            f"Run: {run.id}",
            f"Suite: {run.project}/{run.suite_name}",
            f"Overall coverage: {run.coverage:.1%}",
            f"Strict case pass rate: {strict_pass_rate:.1%}",
        ]
        if quality.get("types_total", 0) or quality.get("facts_total", 0):
            lines.append(
                "Quality counts: "
                f"{quality.get('types_correct', 0)}/{quality.get('types_total', 0)} types correct, "
                f"{quality.get('facts_correct', 0)}/{quality.get('facts_total', 0)} facts, "
                f"{quality.get('hallucinations', 0)} hallucinations"
            )
        if "coverage_delta" in run.metadata:
            lines.append(f"Coverage delta: {run.metadata['coverage_delta']:+.1%}")
        if run.decision_reasons:
            lines.append("Reasons:")
            lines.extend(f"  - {reason}" for reason in run.decision_reasons)
        if run.diffs:
            lines.append("Behavioral diff:")
            symbols = {
                DiffCategory.NEW: "+",
                DiffCategory.RETAINED: "=",
                DiffCategory.LOST: "-",
                DiffCategory.IMPROVED: "~",
                DiffCategory.REGRESSED: "!",
                DiffCategory.UNSUPPORTED: "?",
                DiffCategory.FORMAT_CHANGE: "#",
            }
            for item in run.diffs:
                target = f"{item.case_id}/{item.requirement_id}" if item.requirement_id else item.case_id
                lines.append(
                    f"  {symbols[item.category]} {item.category.value:<13} {target} "
                    f"{_format_score(item.previous_score)} -> {_format_score(item.current_score)}"
                )
        lines.append("Cases:")
        for case in run.cases:
            lines.append(
                f"  {case.case_id}: coverage={case.coverage:.1%} pass_rate={case.pass_rate:.1%} "
                f"contract={case.contract_pass_rate:.1%} system_p95={case.system_latency_p95_ms:.1f}ms "
                f"evaluation_p95={case.evaluation_latency_p95_ms:.1f}ms total_p95={case.latency_p95_ms:.1f}ms"
            )
        result = "\n".join(lines)
        if stream is None:
            print(result)
        else:
            stream.write(result + "\n")
        return result


class JSONReporter:
    def render(self, run: EvalRun, destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


class HTMLReporter:
    def render(self, run: EvalRun, destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in run.decision_reasons) or "<li>No policy violations.</li>"
        quality = run.metadata.get("quality_summary", {})
        quality_line = (
            f"{quality.get('types_correct', 0)}/{quality.get('types_total', 0)} types correct, "
            f"{quality.get('facts_correct', 0)}/{quality.get('facts_total', 0)} facts, "
            f"{quality.get('hallucinations', 0)} hallucinations"
        )
        diff_rows = "".join(
            "<tr>"
            f"<td><span class='badge {item.category.value.lower()}'>{item.category.value}</span></td>"
            f"<td>{html.escape(item.case_id)}</td>"
            f"<td>{html.escape(item.requirement_id or '-')}</td>"
            f"<td>{_format_score(item.previous_score)}</td>"
            f"<td>{_format_score(item.current_score)}</td>"
            f"<td>{html.escape(' | '.join(item.current_evidence) or '-')}</td>"
            "</tr>"
            for item in run.diffs
        ) or "<tr><td colspan='6'>No baseline diff is available for this run.</td></tr>"
        case_rows = "".join(
            "<tr>"
            f"<td>{html.escape(case.case_id)}</td>"
            f"<td>{case.coverage:.1%}</td><td>{case.pass_rate:.1%}</td>"
            f"<td>{case.contract_pass_rate:.1%}</td><td>{case.system_latency_p95_ms:.1f} ms</td>"
            f"<td>{case.evaluation_latency_p95_ms:.1f} ms</td><td>{case.latency_p95_ms:.1f} ms</td>"
            "</tr>"
            for case in run.cases
        )
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EvalDiff report {html.escape(run.id)}</title>
<style>
:root{{--ink:#17202a;--muted:#5f6b7a;--line:#dfe5ec;--bg:#f6f8fb;--panel:#fff;--pass:#16794b;--fail:#bd2c2c;--warn:#a86100;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1120px;margin:40px auto;padding:0 24px}} header{{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;margin-bottom:24px}}
h1{{margin:0;font-size:32px}} h2{{margin:30px 0 12px}} .muted{{color:var(--muted)}}
.status{{font-weight:800;font-size:24px;color:var(--{run.status.value.lower() if run.status.value.lower() in {'pass','fail','warn'} else 'warn'})}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px}}
.metric{{font-size:28px;font-weight:750}} table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line)}} th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#edf2f7}}
.badge{{display:inline-block;padding:2px 8px;border-radius:99px;background:#e8edf3;font-size:12px;font-weight:750}} .lost,.regressed{{background:#fde8e8;color:#9f1d1d}} .new,.improved{{background:#ddf5e8;color:#11633e}} .retained{{color:#455364}} .format_change{{background:#fff1d6;color:#875100}}
ul{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 16px 16px 36px}}
</style></head><body><main>
<header><div><h1>EvalDiff report</h1><div class="muted">{html.escape(run.project)} / {html.escape(run.suite_name)} / {html.escape(run.id)}</div></div><div class="status">{run.status.value}</div></header>
<section class="grid"><div class="card"><div class="muted">Coverage</div><div class="metric">{run.coverage:.1%}</div></div><div class="card"><div class="muted">Strict case pass</div><div class="metric">{(sum(case.pass_rate for case in run.cases) / len(run.cases) if run.cases else 1.0):.1%}</div></div><div class="card"><div class="muted">Behavior changes</div><div class="metric">{len(run.diffs)}</div></div><div class="card"><div class="muted">Baseline</div><div>{html.escape(run.baseline_run_id or 'Not set')}</div></div></section>
<p><strong>Quality counts:</strong> {html.escape(quality_line)}</p>
<h2>Policy decision</h2><ul>{reasons}</ul>
<h2>Behavioral diff</h2><table><thead><tr><th>Category</th><th>Case</th><th>Requirement</th><th>Previous</th><th>Current</th><th>Evidence</th></tr></thead><tbody>{diff_rows}</tbody></table>
<h2>Cases</h2><table><thead><tr><th>Case</th><th>Coverage</th><th>Strict pass rate</th><th>Contract</th><th>System P95</th><th>Evaluation P95</th><th>Total P95</th></tr></thead><tbody>{case_rows}</tbody></table>
</main></body></html>"""
        path.write_text(page, encoding="utf-8")
        return path


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"
