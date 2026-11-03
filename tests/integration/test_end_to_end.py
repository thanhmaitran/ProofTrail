"""From specification file to saved report, explanation and independent replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support import closure, peterson_initial, peterson_successors

from prooftrail.cli import EXIT_ERROR, EXIT_OK, EXIT_VIOLATED, main
from prooftrail.exploration.bfs import Limits
from prooftrail.model.parser import load_specification
from prooftrail.reporting.json_report import build_report, load_report
from prooftrail.verification.engine import verify
from prooftrail.verification.replay import replay_report
from prooftrail.verification.results import Status


def valid_models(examples_dir: Path) -> list[Path]:
    invalid = {p.name for p in (examples_dir / "invalid").iterdir()}
    skip = invalid | {"manifest.json", "domain_error.yaml"}
    return sorted(
        p
        for p in examples_dir.rglob("*")
        if p.suffix in {".yaml", ".yml", ".json"} and p.name not in skip
    )


def test_every_example_verifies_and_every_trace_replays(examples_dir: Path) -> None:
    models = valid_models(examples_dir)
    assert len(models) >= 14
    traces = 0
    for path in models:
        spec = load_specification(path)
        run = verify(spec.system, spec.properties, limits=Limits(max_states=200_000))
        report = build_report(run, include_timing=False, include_environment=False)
        for result in run.results:
            assert result.status in (Status.HOLDS, Status.VIOLATED), (path, result)
        outcomes = replay_report(spec.system, spec.properties, report)
        assert all(o.ok for o in outcomes), (path, [o for o in outcomes if not o.ok])
        traces += len(outcomes)
    assert traces >= 15  # sanity floor: the test must not pass vacuously


@pytest.mark.parametrize(
    ("model", "property_id", "status"),
    [
        ("counter/safe_counter.yaml", "counter-bound", Status.HOLDS),
        ("counter/unsafe_counter.yaml", "counter-bound", Status.VIOLATED),
        ("traffic_light/model.yaml", "never-both-green", Status.HOLDS),
        ("traffic_light/model.yaml", "ns-eventually-served", Status.VIOLATED),
        ("peterson/model.yaml", "mutual-exclusion", Status.HOLDS),
        ("peterson/broken_mutex.yaml", "mutual-exclusion", Status.VIOLATED),
        ("producer_consumer/model.yaml", "buffer-bounds", Status.HOLDS),
        ("producer_consumer/buffer_overflow.yaml", "buffer-bounds", Status.VIOLATED),
        ("resource_protocol/model.yaml", "deadlock-free", Status.VIOLATED),
        ("resource_protocol/ordered.yaml", "deadlock-free", Status.HOLDS),
    ],
)
def test_headline_results_of_the_example_models(
    examples_dir: Path, model: str, property_id: str, status: Status
) -> None:
    spec = load_specification(examples_dir / model)
    run = verify(spec.system, [spec.property(property_id)])
    assert run.results[0].status is status


def test_check_then_explain_pipeline(
    capsys, examples_dir: Path, tmp_path: Path
) -> None:
    for model in valid_models(examples_dir):
        report_path = tmp_path / f"{model.stem}.json"
        code = main(["check", str(model), "-o", str(report_path), "--no-traces"])
        capsys.readouterr()
        assert code in (EXIT_OK, EXIT_VIOLATED), model
        report = load_report(report_path)
        assert report["model_id"] == load_specification(model).system.id

        code = main(["explain", str(report_path), "--model", str(model)])
        out = capsys.readouterr().out
        assert code == EXIT_OK, (model, out)
        assert "replay [FAIL]" not in out


def test_yaml_and_json_specs_produce_identical_reports(examples_dir: Path) -> None:
    reports = []
    for name in ("unsafe_counter.yaml", "unsafe_counter.json"):
        spec = load_specification(examples_dir / "counter" / name)
        run = verify(spec.system, spec.properties)
        report = build_report(run, include_timing=False, include_environment=False)
        # Descriptions are free text and may differ between the two files.
        for item in report["results"]:
            item.pop("description", None)
        reports.append(json.dumps(report["results"], sort_keys=True))
    assert reports[0] == reports[1]


def test_reports_are_byte_reproducible_across_runs(capsys, examples_dir: Path) -> None:
    outputs = []
    for _ in range(2):
        main(
            [
                "check",
                str(examples_dir / "peterson" / "model.yaml"),
                "--format",
                "json",
                "--no-timing",
            ]
        )
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]


def test_invalid_examples_never_produce_a_verdict(capsys, examples_dir: Path) -> None:
    for path in sorted((examples_dir / "invalid").iterdir()):
        code = main(["check", str(path)])
        captured = capsys.readouterr()
        assert code == EXIT_ERROR, path
        assert captured.err.startswith("error: "), path
        assert "VIOLATED" not in captured.out and "HOLDS" not in captured.out
        assert "Traceback" not in captured.err


def test_a_runtime_model_error_is_reported_as_error_not_as_a_verdict(
    capsys, examples_dir: Path
) -> None:
    code = main(["check", str(examples_dir / "counter" / "domain_error.yaml")])
    out = capsys.readouterr().out
    assert code == EXIT_ERROR
    assert "outside its domain" in out and "[ERR!]" in out


def test_peterson_state_space_size_is_stable(examples_dir: Path) -> None:
    spec = load_specification(examples_dir / "peterson" / "model.yaml")
    run = verify(spec.system, spec.properties)
    assert run.exhaustive
    # Pinned. The numbers were derived by the independent hand-coded reference
    # in tests/support.py (plain depth-first closure), not by this engine; a
    # change here means the semantics of the model or the engine changed.
    states, edges = closure(peterson_initial(), peterson_successors)
    assert (len(states), edges) == (20, 34)
    assert run.statistics["states_explored"] == 20
    assert run.statistics["transitions_examined"] == 34
