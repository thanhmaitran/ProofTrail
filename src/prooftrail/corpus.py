"""Running a model corpus: models paired with independently derived answers.

A corpus manifest (``examples/manifest.json``) lists cases. Each case names a
model file and the verdict every declared property is expected to have, plus a
``derivation`` explaining how the answer was obtained *without* running the
tool. ``prooftrail corpus`` re-checks all of them, so a regression in the
engine shows up as a failed case.

Manifest format (version ``1.0``)::

    {"corpus_version": "1.0",
     "cases": [{
        "id": "counter-unsafe-001",
        "model": "counter/unsafe_counter.yaml",
        "purpose": "...", "derivation": "...",
        "options": {"max_states": 3},                       # optional
        "expected": [{"property_id": "counter-bound",
                      "status": "VIOLATED",
                      "counterexample_length": 4}]}]}      # optional extras

A case whose model is *supposed* to be rejected uses
``"expected": {"load_error": "substring of the error message"}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prooftrail.errors import ProofTrailError, SpecError
from prooftrail.exploration.bfs import Limits
from prooftrail.model.parser import load_specification
from prooftrail.reporting.json_report import build_report
from prooftrail.verification.engine import verify
from prooftrail.verification.replay import replay_report
from prooftrail.verification.results import PropertyResult

SUPPORTED_CORPUS_VERSIONS = ("1.0",)
_OPTION_KEYS = {
    "max_states",
    "max_depth",
    "timeout_seconds",
    "indexing",
    "strategy",
    "cache",
}
_EXPECT_KEYS = {
    "property_id",
    "status",
    "reason",
    "counterexample_length",
    "witness_length",
    "lasso",
    "states_explored",
}


@dataclass
class CaseOutcome:
    case_id: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    checked: int = 0  # number of expectations compared


def run_corpus(
    manifest_path: str | Path, *, only: set[str] | None = None
) -> list[CaseOutcome]:
    """Run every case (or just those in ``only``) and report pass/fail per case."""
    path = Path(manifest_path)
    manifest = _read_manifest(path)
    cases = manifest["cases"]
    ids = [c.get("id") for c in cases if isinstance(c, dict)]
    if len(ids) != len(set(ids)):
        raise ProofTrailError(f"{path}: duplicate case ids in the manifest.")
    if only:
        missing = only - set(ids)
        if missing:
            raise ProofTrailError(f"Unknown case id(s): {sorted(missing)}.")

    outcomes = []
    for case in cases:
        if only and case.get("id") not in only:
            continue
        outcomes.append(_run_case(case, path.parent))
    return outcomes


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProofTrailError(f"{path}: cannot read corpus manifest ({exc}).") from None
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ProofTrailError(
            f"{path}: manifest must be an object with a 'cases' list."
        )
    if data.get("corpus_version") not in SUPPORTED_CORPUS_VERSIONS:
        raise ProofTrailError(
            f"{path}: unsupported corpus_version {data.get('corpus_version')!r}."
        )
    return data


def _run_case(case: Any, base: Path) -> CaseOutcome:
    if not isinstance(case, dict) or not isinstance(case.get("id"), str):
        return CaseOutcome("<invalid>", False, ["a case needs a string 'id'"])
    outcome = CaseOutcome(case["id"], True)

    def fail(message: str) -> None:
        outcome.passed = False
        outcome.problems.append(message)

    model = case.get("model")
    expected = case.get("expected")
    if not isinstance(model, str) or expected is None:
        fail("a case needs 'model' and 'expected'")
        return outcome
    options = case.get("options", {})
    if not isinstance(options, dict) or set(options) - _OPTION_KEYS:
        fail(f"'options' may only contain {sorted(_OPTION_KEYS)}")
        return outcome
    items = expected if isinstance(expected, list) else [expected]
    if not items or not all(isinstance(i, dict) for i in items):
        fail("'expected' must be an object or a non-empty list of objects")
        return outcome

    # --- a model that must be rejected ---------------------------------------
    if any("load_error" in item for item in items):
        needle = str(items[0].get("load_error", "")).lower()
        try:
            load_specification(base / model)
        except SpecError as exc:
            outcome.checked = 1
            if needle not in str(exc).lower():
                fail(f"rejected with an unexpected message: {exc}")
        else:
            fail("the model was accepted but was expected to be rejected")
        return outcome

    # --- a model that must load and verify -----------------------------------
    try:
        spec = load_specification(base / model)
    except ProofTrailError as exc:
        fail(f"the model failed to load: {exc}")
        return outcome

    limits = Limits(
        max_states=options.get("max_states", 1_000_000),
        max_depth=options.get("max_depth"),
        timeout_seconds=options.get("timeout_seconds"),
    )
    run = verify(
        spec.system,
        spec.properties,
        limits=limits,
        indexing=options.get("indexing", "object"),
        strategy=options.get("strategy", "shared"),
        successor_cache=bool(options.get("cache", False)),
    )
    by_id = {r.property_id: r for r in run.results}

    declared = {p.id for p in spec.properties}
    expected_ids = {i.get("property_id") for i in items}
    if declared != expected_ids:
        fail(
            "expected results must cover exactly the declared properties "
            f"(declared {sorted(declared)}, expected {sorted(map(str, expected_ids))})"
        )
        return outcome

    for item in items:
        unknown = set(item) - _EXPECT_KEYS
        if unknown:
            fail(f"unknown expectation key(s) {sorted(unknown)}")
            continue
        _compare(by_id[item["property_id"]], item, run.statistics, fail)
        outcome.checked += 1

    # Every trace must replay against the model, independently of the engine.
    report = build_report(run, include_timing=False, include_environment=False)
    for replay in replay_report(spec.system, spec.properties, report):
        if not replay.ok:
            fail(
                f"{replay.property_id}: {replay.trace_kind} failed replay: "
                f"{replay.message}"
            )
    return outcome


def _compare(
    result: PropertyResult, item: dict[str, Any], stats: dict, fail: Any
) -> None:
    pid = item["property_id"]
    if result.status.value != item.get("status"):
        fail(
            f"{pid}: expected status {item.get('status')}, got {result.status.value}"
            + (f" ({result.message})" if result.message else "")
        )
        return
    if "reason" in item and result.reason != item["reason"]:
        fail(f"{pid}: expected reason {item['reason']!r}, got {result.reason!r}")
    trace = result.counterexample
    if "counterexample_length" in item:
        got = None if trace is None else len(trace.steps) - 1
        if got != item["counterexample_length"]:
            fail(
                f"{pid}: expected counterexample length "
                f"{item['counterexample_length']}, got {got}"
            )
    if "witness_length" in item:
        got = None if result.witness is None else len(result.witness.steps) - 1
        if got != item["witness_length"]:
            fail(f"{pid}: expected witness length {item['witness_length']}, got {got}")
    if "lasso" in item:
        shown = trace or result.witness
        got = None if shown is None else shown.is_lasso
        if got != item["lasso"]:
            fail(f"{pid}: expected lasso={item['lasso']}, got {got}")
    if (
        "states_explored" in item
        and stats.get("states_explored") != item["states_explored"]
    ):
        fail(
            f"{pid}: expected {item['states_explored']} states explored, "
            f"got {stats.get('states_explored')}"
        )
