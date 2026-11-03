"""The corpus runner: it must pass real corpora and *fail* wrong expectations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from prooftrail.corpus import run_corpus
from prooftrail.errors import ProofTrailError


def manifest_with(tmp_path: Path, examples_dir: Path, cases: list[dict]) -> Path:
    shutil.copytree(examples_dir, tmp_path / "examples")
    path = tmp_path / "examples" / "custom.json"
    path.write_text(json.dumps({"corpus_version": "1.0", "cases": cases}))
    return path


def case(**changes) -> dict:
    base = {
        "id": "c1",
        "model": "counter/unsafe_counter.yaml",
        "expected": [
            {
                "property_id": "counter-bound",
                "status": "VIOLATED",
                "counterexample_length": 4,
            }
        ],
    }
    base.update(changes)
    return base


def test_the_shipped_corpus_passes(examples_dir: Path) -> None:
    outcomes = run_corpus(examples_dir / "manifest.json")
    assert len(outcomes) >= 30
    failed = [o for o in outcomes if not o.passed]
    assert not failed, [(o.case_id, o.problems) for o in failed]
    assert all(o.checked >= 1 for o in outcomes)


def test_only_selects_cases(examples_dir: Path) -> None:
    outcomes = run_corpus(examples_dir / "manifest.json", only={"counter-unsafe-001"})
    assert [o.case_id for o in outcomes] == ["counter-unsafe-001"]
    with pytest.raises(ProofTrailError, match="Unknown case id"):
        run_corpus(examples_dir / "manifest.json", only={"nope"})


def test_a_correct_custom_case_passes(tmp_path: Path, examples_dir: Path) -> None:
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [case()]))
    assert outcome.passed and outcome.checked == 1


@pytest.mark.parametrize(
    ("expected", "fragment"),
    [
        (
            [{"property_id": "counter-bound", "status": "HOLDS"}],
            "expected status HOLDS, got VIOLATED",
        ),
        (
            [
                {
                    "property_id": "counter-bound",
                    "status": "VIOLATED",
                    "counterexample_length": 2,
                }
            ],
            "expected counterexample length 2, got 4",
        ),
        (
            [
                {
                    "property_id": "counter-bound",
                    "status": "VIOLATED",
                    "reason": "deadlock",
                }
            ],
            "expected reason",
        ),
        (
            [
                {
                    "property_id": "counter-bound",
                    "status": "VIOLATED",
                    "states_explored": 99,
                }
            ],
            "states explored",
        ),
        (
            [{"property_id": "counter-bound", "status": "VIOLATED", "lasso": True}],
            "expected lasso=True",
        ),
        (
            [
                {
                    "property_id": "counter-bound",
                    "status": "VIOLATED",
                    "witness_length": 1,
                }
            ],
            "expected witness length",
        ),
        (
            [{"property_id": "counter-bound", "status": "VIOLATED", "bogus": 1}],
            "unknown expectation key",
        ),
        (
            [{"property_id": "other", "status": "HOLDS"}],
            "must cover exactly the declared properties",
        ),
    ],
)
def test_wrong_expectations_fail_the_case(
    tmp_path: Path, examples_dir: Path, expected: list, fragment: str
) -> None:
    (outcome,) = run_corpus(
        manifest_with(tmp_path, examples_dir, [case(expected=expected)])
    )
    assert not outcome.passed
    assert any(fragment in problem for problem in outcome.problems), outcome.problems


def test_a_model_that_should_be_rejected_but_loads_fails(
    tmp_path: Path, examples_dir: Path
) -> None:
    bad = case(expected={"load_error": "anything"})
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [bad]))
    assert not outcome.passed and "accepted" in outcome.problems[0]


def test_a_rejection_with_the_wrong_message_fails(
    tmp_path: Path, examples_dir: Path
) -> None:
    bad = case(model="invalid/syntax_error.yaml", expected={"load_error": "banana"})
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [bad]))
    assert not outcome.passed and "unexpected message" in outcome.problems[0]


def test_a_model_that_fails_to_load_fails_the_case(
    tmp_path: Path, examples_dir: Path
) -> None:
    bad = case(model="invalid/syntax_error.yaml")
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [bad]))
    assert not outcome.passed and "failed to load" in outcome.problems[0]


def test_options_are_honoured_and_validated(tmp_path: Path, examples_dir: Path) -> None:
    limited = case(
        options={"max_states": 2},
        expected=[
            {"property_id": "counter-bound", "status": "VIOLATED"},
        ],
    )
    unknown = case(
        id="c2",
        model="counter/safe_counter.yaml",
        options={"max_states": 2},
        expected=[
            {
                "property_id": "counter-bound",
                "status": "UNKNOWN",
                "reason": "max_states",
            },
            {"property_id": "can-reach-max", "status": "UNKNOWN"},
            {"property_id": "four-reachable", "status": "UNKNOWN"},
            {"property_id": "eventually-max", "status": "UNKNOWN"},
            {"property_id": "stops-only-at-max", "status": "UNKNOWN"},
            {"property_id": "never-stops", "status": "UNKNOWN"},
        ],
    )
    bad_option = case(id="c3", options={"colour": "red"})
    outcomes = run_corpus(
        manifest_with(tmp_path, examples_dir, [limited, unknown, bad_option])
    )
    by_id = {o.case_id: o for o in outcomes}
    # With only 2 states the violation (depth 4) is not found.
    assert not by_id["c1"].passed
    assert by_id["c2"].passed, by_id["c2"].problems
    assert not by_id["c3"].passed and "options" in by_id["c3"].problems[0]


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ("not json", "cannot read corpus manifest"),
        ('{"cases": []}', "unsupported corpus_version"),
        ('{"corpus_version": "1.0"}', "'cases' list"),
        ("[]", "'cases' list"),
        (
            '{"corpus_version": "1.0", "cases": [{"id": "a"}, {"id": "a"}]}',
            "duplicate case ids",
        ),
    ],
)
def test_bad_manifests(tmp_path: Path, document: str, fragment: str) -> None:
    path = tmp_path / "m.json"
    path.write_text(document)
    with pytest.raises(ProofTrailError, match=fragment):
        run_corpus(path)
    with pytest.raises(ProofTrailError, match="cannot read"):
        run_corpus(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "x"},
        {"id": "x", "model": "counter/unsafe_counter.yaml", "expected": []},
        {"id": "x", "model": "counter/unsafe_counter.yaml", "expected": ["a"]},
    ],
)
def test_malformed_cases_fail_cleanly(
    tmp_path: Path, examples_dir: Path, broken: dict
) -> None:
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [broken]))
    assert not outcome.passed and outcome.problems


def test_a_case_without_an_id_is_reported(tmp_path: Path, examples_dir: Path) -> None:
    (outcome,) = run_corpus(manifest_with(tmp_path, examples_dir, [{"model": "x"}]))
    assert outcome.case_id == "<invalid>" and not outcome.passed
