"""Structured (JSON) reports and their human-readable rendering.

A report is a plain ``dict`` so it can be written, diffed and re-read without
ProofTrail. Everything in it comes from the engine: statistics are never
hard-coded, a ``counterexample`` only appears when a validated trace exists,
and ``complete`` is true only for verdicts the algorithm actually reached.
"""

from __future__ import annotations

import json
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prooftrail import __version__
from prooftrail.errors import ProofTrailError
from prooftrail.verification.results import (
    PropertyResult,
    Status,
    Trace,
    VerificationRun,
)

REPORT_SCHEMA_VERSION = "1.0"
MAX_REPORT_BYTES = 50_000_000

_STATUSES = {status.value for status in Status}


# ------------------------------------------------------------------ building


def build_report(
    run: VerificationRun,
    *,
    source: str | None = None,
    sha256: str | None = None,
    include_timing: bool = True,
    include_environment: bool = True,
) -> dict[str, Any]:
    """Turn a :class:`VerificationRun` into a JSON-serialisable report.

    ``include_timing=False`` and ``include_environment=False`` drop everything
    that varies between runs, which makes reports byte-for-byte reproducible.
    """
    results = [
        _result_dict(r, include_timing, per_result_stats=run.strategy == "per-property")
        for r in run.results
    ]
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": "ProofTrail", "version": __version__},
        "model_id": run.model_id,
        "status": run.overall().value,
        "complete": all(r.complete for r in run.results),
        "exhaustive": run.exhaustive,
        "counts": run.counts(),
        "configuration": {
            "limits": dict(run.limits),
            "indexing": run.indexing,
            "strategy": run.strategy,
        },
        "statistics": _statistics(run.statistics, include_timing),
        "results": results,
    }
    if run.stop_reason is not None:
        report["stop_reason"] = run.stop_reason
    if source is not None or sha256 is not None:
        report["specification"] = {"path": source, "sha256": sha256}
    if include_environment:
        report["environment"] = {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        }
    return report


def _statistics(stats: Mapping[str, Any], include_timing: bool) -> dict[str, Any]:
    data = dict(stats)
    if not include_timing:
        data.pop("elapsed_seconds", None)
        data.pop("peak_memory_bytes", None)
    return data


def trace_to_dict(trace: Trace) -> dict[str, Any]:
    data = trace.to_dict()
    data["kind"] = "lasso" if trace.is_lasso else "finite"
    data["length"] = len(trace.steps) - 1
    return data


def _result_dict(
    result: PropertyResult, include_timing: bool, *, per_result_stats: bool
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "property_id": result.property_id,
        "kind": result.kind,
        "status": result.status.value,
        "complete": result.complete,
    }
    if result.description:
        item["description"] = result.description
    if result.reason:
        item["reason"] = result.reason
    if result.message:
        item["message"] = result.message
    if result.counterexample is not None:
        item["counterexample"] = trace_to_dict(result.counterexample)
    if result.witness is not None:
        item["witness"] = trace_to_dict(result.witness)
    if per_result_stats and result.statistics:
        item["statistics"] = _statistics(result.statistics, include_timing)
    return item


# --------------------------------------------------------------- reading


def write_report(report: Mapping[str, Any], path: str | Path) -> Path:
    """Write ``report`` as pretty-printed JSON, creating parent directories."""
    target = Path(path)
    if target.exists() and target.is_dir():
        raise ProofTrailError(f"{target}: is a directory, expected a file path.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise ProofTrailError(
            f"{target}: cannot write report ({exc.strerror or exc})."
        ) from None
    return target


def load_report(path: str | Path) -> dict[str, Any]:
    """Read and structurally validate a report written by :func:`write_report`."""
    source = Path(path)
    try:
        with source.open("rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
    except OSError as exc:
        raise ProofTrailError(
            f"{source}: cannot read report ({exc.strerror or exc})."
        ) from None
    if len(raw) > MAX_REPORT_BYTES:
        raise ProofTrailError(f"{source}: report is larger than the size limit.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProofTrailError(f"{source}: not a valid JSON report ({exc}).") from None
    validate_report(data)
    return data


def validate_report(report: object) -> None:
    """Raise :class:`ProofTrailError` if ``report`` is not a well-formed report."""
    if not isinstance(report, dict):
        raise ProofTrailError("Report must be a JSON object.")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ProofTrailError(
            f"Unsupported report schema_version {report.get('schema_version')!r}; "
            f"expected {REPORT_SCHEMA_VERSION!r}."
        )
    for key in ("model_id", "status", "results", "statistics", "configuration"):
        if key not in report:
            raise ProofTrailError(f"Report is missing the {key!r} field.")
    if report["status"] not in _STATUSES:
        raise ProofTrailError(f"Report has an invalid status {report['status']!r}.")
    results = report["results"]
    if not isinstance(results, list):
        raise ProofTrailError("Report 'results' must be a list.")
    for item in results:
        if not isinstance(item, dict):
            raise ProofTrailError("Each report result must be an object.")
        for key in ("property_id", "kind", "status", "complete"):
            if key not in item:
                raise ProofTrailError(f"A report result is missing {key!r}.")
        if item["status"] not in _STATUSES:
            raise ProofTrailError(f"Invalid result status {item['status']!r}.")
        if item["complete"] != (item["status"] in ("HOLDS", "VIOLATED")):
            raise ProofTrailError(
                f"Result {item['property_id']!r}: 'complete' contradicts its status."
            )
        if item["status"] == "UNKNOWN" and "counterexample" in item:
            raise ProofTrailError(
                f"Result {item['property_id']!r}: an UNKNOWN result has a "
                "counterexample."
            )
        for trace_key in ("counterexample", "witness"):
            if trace_key in item:
                Trace.from_dict(item[trace_key])


def trace_from_report(item: Mapping[str, Any], key: str) -> Trace | None:
    data = item.get(key)
    return Trace.from_dict(data) if data is not None else None


# --------------------------------------------------------------- rendering

_MARK = {"HOLDS": "PASS", "VIOLATED": "FAIL", "UNKNOWN": "????", "ERROR": "ERR!"}


def format_trace(trace_data: Mapping[str, Any], indent: str = "    ") -> list[str]:
    """Render a trace dictionary as aligned text lines."""
    lines: list[str] = []
    loop = trace_data.get("loop")
    loop_back = loop["back_to"] if loop else None
    for step in trace_data["steps"]:
        state = ", ".join(f"{k}={_fmt(v)}" for k, v in sorted(step["state"].items()))
        index = step["index"]
        prefix = f"{indent}[{index}]"
        if "action" in step:
            lines.append(f"{prefix} --{step['action']}--> {state}")
        else:
            lines.append(f"{prefix} (initial) {state}")
    if loop_back is not None:
        last = trace_data["steps"][-1]["index"]
        lines.append(
            f"{indent}loop: [{last}] --{loop['action']}--> [{loop_back}] "
            "(the path repeats forever)"
        )
    return lines


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_report_text(
    report: Mapping[str, Any],
    *,
    show_traces: bool = True,
    show_statistics: bool = True,
) -> str:
    """Human-readable summary of a report (used by ``check`` and ``explain``)."""
    lines = [f"ProofTrail {report['tool']['version']} - model {report['model_id']!r}"]
    spec = report.get("specification")
    if spec and spec.get("path"):
        lines.append(f"specification: {spec['path']}")
    lines.append("")

    for item in report["results"]:
        mark = _MARK[item["status"]]
        lines.append(
            f"[{mark}] {item['property_id']}  ({item['kind']}) -> {item['status']}"
        )
        if item.get("description"):
            lines.append(f"       {item['description']}")
        if item.get("reason"):
            lines.append(f"       reason: {item['reason']}")
        if item.get("message"):
            lines.append(f"       {item['message']}")
        if show_traces:
            for key, title in (
                ("counterexample", "counterexample"),
                ("witness", "witness"),
            ):
                if key in item:
                    trace = item[key]
                    lines.append(
                        f"       {title} ({trace['kind']}, {trace['length']} step(s)):"
                    )
                    lines.extend(format_trace(trace, indent="         "))

    counts = report.get("counts", {})
    summary = (
        ", ".join(f"{n} {name}" for name, n in counts.items() if n) or "no properties"
    )
    lines.append("")
    lines.append(f"result: {report['status']} ({summary})")
    if show_statistics:
        stats = report["statistics"]
        parts = [
            f"{stats.get('states_explored', 0)} states",
            f"{stats.get('transitions_examined', 0)} transitions",
            f"depth {stats.get('max_depth_reached', 0)}",
        ]
        if "elapsed_seconds" in stats:
            parts.append(f"{stats['elapsed_seconds']:.4f}s")
        stop = report.get("stop_reason")
        if report.get("exhaustive"):
            how = "exploration exhaustive"
        elif stop == "stopped_early":
            how = "exploration stopped early (every property was already decided)"
        else:
            how = f"exploration INCOMPLETE ({stop or 'unknown reason'})"
        lines.append(f"statistics: {', '.join(parts)}; {how}")
    return "\n".join(lines)
