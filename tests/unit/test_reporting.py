"""JSON reports: structure, honesty rules, round-trips, text rendering, replay."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from prooftrail.errors import ProofTrailError
from prooftrail.exploration.bfs import Limits
from prooftrail.model.parser import load_specification
from prooftrail.reporting.json_report import (
    build_report,
    format_trace,
    load_report,
    render_report_text,
    validate_report,
    write_report,
)
from prooftrail.verification.engine import verify
from prooftrail.verification.replay import replay_report


def run_example(examples_dir: Path, name: str, **options):
    spec = load_specification(examples_dir / name)
    return spec, verify(spec.system, spec.properties, **options)


@pytest.fixture()
def unsafe(examples_dir: Path):
    spec, run = run_example(examples_dir, "counter/unsafe_counter.yaml")
    return spec, build_report(run, source="x.yaml", sha256=spec.sha256)


def test_report_structure_for_a_violation(unsafe) -> None:
    _, report = unsafe
    assert report["schema_version"] == "1.0"
    assert report["tool"]["name"] == "ProofTrail"
    assert report["model_id"] == "unsafe-counter"
    assert report["status"] == "VIOLATED"
    assert report["complete"] is True
    (item,) = report["results"]
    assert item["status"] == "VIOLATED" and item["complete"] is True
    steps = item["counterexample"]["steps"]
    assert [s["state"]["counter"] for s in steps] == [0, 1, 2, 3, 4]
    assert "action" not in steps[0] and steps[1]["action"] == "increment"
    assert item["counterexample"]["length"] == 4
    assert item["counterexample"]["kind"] == "finite"
    assert report["statistics"]["states_explored"] == 5
    assert report["specification"]["sha256"]
    validate_report(report)


def test_unknown_result_is_incomplete_and_has_no_counterexample(examples_dir) -> None:
    _, run = run_example(
        examples_dir, "counter/safe_counter.yaml", limits=Limits(max_states=2)
    )
    report = build_report(run)
    assert report["complete"] is False and report["exhaustive"] is False
    assert report["stop_reason"] == "max_states"
    unknown = [r for r in report["results"] if r["status"] == "UNKNOWN"]
    assert unknown and all(
        not r["complete"] and "counterexample" not in r for r in unknown
    )
    validate_report(report)


def test_counterexamples_only_appear_with_a_trace(examples_dir) -> None:
    _, run = run_example(examples_dir, "counter/safe_counter.yaml")
    report = build_report(run)
    for item in report["results"]:
        if item["status"] == "HOLDS" and item["kind"] != "reachability":
            assert "counterexample" not in item
        if item["kind"] == "reachability" and item["status"] == "HOLDS":
            assert "witness" in item


def test_timing_and_environment_can_be_omitted_for_reproducibility(
    examples_dir,
) -> None:
    spec, _ = run_example(examples_dir, "traffic_light/model.yaml")
    texts = []
    for _ in range(2):
        run = verify(spec.system, spec.properties)
        texts.append(
            json.dumps(
                build_report(
                    run, source="m", include_timing=False, include_environment=False
                ),
                sort_keys=True,
            )
        )
    assert texts[0] == texts[1]
    data = json.loads(texts[0])
    assert "environment" not in data and "elapsed_seconds" not in data["statistics"]
    full = build_report(verify(spec.system, spec.properties))
    assert "environment" in full and "elapsed_seconds" in full["statistics"]


def test_per_property_strategy_reports_per_result_statistics(examples_dir) -> None:
    _, run = run_example(
        examples_dir, "counter/safe_counter.yaml", strategy="per-property"
    )
    report = build_report(run)
    assert all("statistics" in item for item in report["results"])
    assert report["configuration"]["strategy"] == "per-property"


def test_lasso_traces_are_marked(examples_dir) -> None:
    _, run = run_example(examples_dir, "misc/cycle.yaml")
    report = build_report(run)
    lassos = [
        r["counterexample"]
        for r in report["results"]
        if "counterexample" in r and r["counterexample"]["kind"] == "lasso"
    ]
    assert lassos and "loop" in lassos[0]


def test_write_and_load_roundtrip(tmp_path: Path, unsafe) -> None:
    _, report = unsafe
    target = write_report(report, tmp_path / "nested" / "dir" / "report.json")
    assert target.exists()
    assert load_report(target) == report


def test_write_report_errors(tmp_path: Path, unsafe) -> None:
    _, report = unsafe
    with pytest.raises(ProofTrailError, match="is a directory"):
        write_report(report, tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ProofTrailError, match="cannot write"):
        write_report(report, blocker / "child.json")


def test_load_report_errors(tmp_path: Path) -> None:
    with pytest.raises(ProofTrailError, match="cannot read"):
        load_report(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    with pytest.raises(ProofTrailError, match="not a valid JSON"):
        load_report(bad)
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(ProofTrailError, match="not a valid JSON"):
        load_report(bad)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r.update(schema_version="9"), "Unsupported report schema_version"),
        (lambda r: r.pop("results"), "missing the 'results'"),
        (lambda r: r.update(status="MAYBE"), "invalid status"),
        (lambda r: r.update(results={}), "must be a list"),
        (lambda r: r["results"].append(5), "must be an object"),
        (lambda r: r["results"][0].pop("kind"), "missing 'kind'"),
        (lambda r: r["results"][0].update(status="NOPE"), "Invalid result status"),
        (lambda r: r["results"][0].update(complete=False), "contradicts"),
        (
            lambda r: r["results"][0].update(status="UNKNOWN", complete=False),
            "UNKNOWN result has a counterexample",
        ),
        (
            lambda r: r["results"][0]["counterexample"].pop("steps"),
            "Malformed trace",
        ),
    ],
)
def test_validate_report_rejects_inconsistent_reports(unsafe, mutate, fragment) -> None:
    _, report = unsafe
    bad = copy.deepcopy(report)
    mutate(bad)
    with pytest.raises(ProofTrailError, match=fragment):
        validate_report(bad)


def test_validate_report_rejects_non_objects() -> None:
    with pytest.raises(ProofTrailError, match="JSON object"):
        validate_report([])


# ------------------------------------------------------------------ rendering


def test_text_rendering_of_a_violation(unsafe) -> None:
    _, report = unsafe
    text = render_report_text(report)
    assert "[FAIL] counter-bound  (invariant) -> VIOLATED" in text
    assert "counterexample (finite, 4 step(s))" in text
    assert "[1] --increment--> counter=1" in text
    assert "result: VIOLATED (1 violated)" in text
    assert "5 states" in text and "stopped early" in text
    assert "counterexample" not in render_report_text(report, show_traces=False)


def test_text_rendering_marks_incomplete_runs_loudly(examples_dir) -> None:
    _, run = run_example(
        examples_dir, "counter/safe_counter.yaml", limits=Limits(max_states=2)
    )
    text = render_report_text(build_report(run))
    assert "[????]" in text and "INCOMPLETE (max_states)" in text
    assert "not a proof" in text


def test_text_rendering_of_holding_results(examples_dir) -> None:
    _, run = run_example(examples_dir, "traffic_light/model.yaml")
    text = render_report_text(build_report(run))
    assert "[PASS] never-both-green" in text
    assert "exploration exhaustive" in text
    assert "witness (" in text  # reachability witness is shown


def test_lasso_rendering_names_the_loop(examples_dir) -> None:
    _, run = run_example(examples_dir, "misc/cycle.yaml")
    text = render_report_text(build_report(run))
    assert "the path repeats forever" in text


def test_format_trace_booleans_are_lowercase() -> None:
    lines = format_trace(
        {"steps": [{"index": 0, "state": {"b": True, "n": 3}}]}, indent=""
    )
    assert lines == ["[0] (initial) b=true, n=3"]


# --------------------------------------------------------------------- replay


def test_replay_accepts_real_reports_for_every_example(examples_dir: Path) -> None:
    manifest = json.loads((examples_dir / "manifest.json").read_text())
    checked = 0
    for case in manifest["cases"]:
        if "load_error" in json.dumps(case["expected"]):
            continue
        spec = load_specification(examples_dir / case["model"])
        if (
            any(p.kind == "ctl" or p.kind == "response" for p in spec.properties)
            and len(spec.system.variables) > 4
        ):
            continue  # keep the unit test fast; the corpus replays everything
        run = verify(spec.system, spec.properties, limits=Limits(max_states=5000))
        report = build_report(run, include_timing=False, include_environment=False)
        for outcome in replay_report(spec.system, spec.properties, report):
            assert outcome.ok, (case["id"], outcome)
            checked += 1
    assert checked > 10


def test_replay_rejects_a_tampered_trace(examples_dir: Path) -> None:
    spec, run = run_example(examples_dir, "counter/unsafe_counter.yaml")
    report = build_report(run)
    (outcome,) = replay_report(spec.system, spec.properties, report)
    assert outcome.ok and outcome.semantic

    bad = copy.deepcopy(report)
    bad["results"][0]["counterexample"]["steps"][2]["state"]["counter"] = 7
    (outcome,) = replay_report(spec.system, spec.properties, bad)
    assert not outcome.ok

    bad = copy.deepcopy(report)
    bad["results"][0]["counterexample"]["steps"].pop()  # no longer violating
    (outcome,) = replay_report(spec.system, spec.properties, bad)
    assert not outcome.ok and "does not violate" in outcome.message


def test_replay_flags_unknown_properties(examples_dir: Path) -> None:
    spec, run = run_example(examples_dir, "counter/unsafe_counter.yaml")
    report = build_report(run)
    report["results"][0]["property_id"] = "renamed"
    (outcome,) = replay_report(spec.system, spec.properties, report)
    assert not outcome.ok and "not in the model" in outcome.message


def test_replay_of_response_and_ctl_traces(examples_dir: Path) -> None:
    spec, run = run_example(examples_dir, "traffic_light/model.yaml")
    report = build_report(run)
    outcomes = {
        o.property_id: o for o in replay_report(spec.system, spec.properties, report)
    }
    assert (
        outcomes["ns-eventually-served"].ok
        and outcomes["ns-eventually-served"].semantic
    )

    # A response counterexample must really avoid the response forever.
    bad = copy.deepcopy(report)
    item = next(r for r in bad["results"] if r["property_id"] == "ns-eventually-served")
    item["counterexample"].pop("loop")
    (outcome,) = [
        o
        for o in replay_report(spec.system, spec.properties, bad)
        if o.property_id == "ns-eventually-served"
    ]
    assert not outcome.ok and "lasso" in outcome.message
