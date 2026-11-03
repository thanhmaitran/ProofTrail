"""The command-line interface: output, files and exit codes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from prooftrail import __version__
from prooftrail.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_UNKNOWN,
    EXIT_VIOLATED,
    build_parser,
    main,
)


def run(capsys, *argv: object) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def counter(examples_dir: Path) -> Path:
    return examples_dir / "counter"


# ----------------------------------------------------------------- validate


def test_validate_summarises_the_model(capsys, counter: Path) -> None:
    code, out, _ = run(capsys, "validate", counter / "safe_counter.yaml")
    assert code == EXIT_OK
    assert "model: safe-counter" in out
    assert "counter: integer in [0, 3], initial {0}" in out
    assert "transitions (1): increment" in out
    assert "counter-bound (invariant)" in out


def test_validate_warns_about_unbounded_integers(capsys, spec_file) -> None:
    path = spec_file(
        'schema_version: "1.0"\nmodel:\n  id: m\n  variables:\n'
        "    x: {type: integer, initial: 0}\n"
    )
    code, out, _ = run(capsys, "validate", path)
    assert code == EXIT_OK and "without both bounds: x" in out


def test_validate_rejects_a_bad_file_with_exit_2(capsys, examples_dir: Path) -> None:
    code, out, err = run(
        capsys, "validate", examples_dir / "invalid" / "syntax_error.yaml"
    )
    assert code == EXIT_ERROR and out == "" and err.startswith("error: ")
    assert "syntax error" in err


# -------------------------------------------------------------------- check


def test_check_a_violation(capsys, counter: Path) -> None:
    code, out, _ = run(capsys, "check", counter / "unsafe_counter.yaml")
    assert code == EXIT_VIOLATED
    assert "VIOLATED" in out and "[4] --increment--> counter=4" in out


def test_check_a_model_where_everything_holds(capsys, examples_dir: Path) -> None:
    code, out, _ = run(
        capsys,
        "check",
        examples_dir / "traffic_light" / "model.yaml",
        "-p",
        "never-both-green",
        "-p",
        "ns-can-go",
    )
    assert code == EXIT_OK
    assert out.count("[PASS]") == 2 and "result: HOLDS" in out


def test_check_unknown_property_is_an_error(capsys, counter: Path) -> None:
    code, _, err = run(capsys, "check", counter / "safe_counter.yaml", "-p", "nope")
    assert code == EXIT_ERROR and "Unknown property 'nope'" in err


def test_check_without_properties_is_an_error(capsys, spec_file) -> None:
    path = spec_file(
        'schema_version: "1.0"\nmodel:\n  id: m\n  variables:\n'
        "    x: {type: boolean, initial: false}\n"
    )
    code, _, err = run(capsys, "check", path)
    assert code == EXIT_ERROR and "declares no properties" in err


def test_check_with_a_resource_limit_is_inconclusive_exit_3(
    capsys, examples_dir: Path
) -> None:
    code, out, _ = run(
        capsys,
        "check",
        examples_dir / "counter" / "safe_counter.yaml",
        "-p",
        "counter-bound",
        "--max-states",
        "2",
    )
    assert code == EXIT_UNKNOWN
    assert "UNKNOWN" in out and "INCOMPLETE (max_states)" in out and "[PASS]" not in out


def test_json_format_and_report_file(capsys, counter: Path, tmp_path: Path) -> None:
    target = tmp_path / "reports" / "counter.json"
    code, out, _ = run(
        capsys,
        "check",
        counter / "unsafe_counter.yaml",
        "--format",
        "json",
        "--output",
        target,
        "--no-timing",
    )
    assert code == EXIT_VIOLATED
    printed = json.loads(out)
    assert printed == json.loads(target.read_text())
    assert printed["status"] == "VIOLATED"
    assert (
        "environment" not in printed and "elapsed_seconds" not in printed["statistics"]
    )


def test_text_format_mentions_the_report_file(
    capsys, counter: Path, tmp_path: Path
) -> None:
    target = tmp_path / "r.json"
    _, out, _ = run(capsys, "check", counter / "unsafe_counter.yaml", "-o", target)
    assert f"report written to {target}" in out and target.exists()


def test_unwritable_report_path_is_a_clean_error(
    capsys, counter: Path, tmp_path: Path
) -> None:
    code, _, err = run(capsys, "check", counter / "unsafe_counter.yaml", "-o", tmp_path)
    assert code == EXIT_ERROR and "is a directory" in err


def test_no_traces_flag(capsys, counter: Path) -> None:
    _, out, _ = run(capsys, "check", counter / "unsafe_counter.yaml", "--no-traces")
    assert "VIOLATED" in out and "-->" not in out


@pytest.mark.parametrize("flag", ["--max-states", "--max-depth"])
@pytest.mark.parametrize("value", ["0", "-3", "abc"])
def test_numeric_options_are_validated(
    capsys, counter: Path, flag: str, value: str
) -> None:
    with pytest.raises(SystemExit) as info:
        main(["check", str(counter / "unsafe_counter.yaml"), flag, value])
    assert info.value.code == 2
    assert "argument" in capsys.readouterr().err


def test_timeout_option_is_validated(capsys, counter: Path) -> None:
    for value in ("0", "-1", "soon"):
        with pytest.raises(SystemExit):
            main(["check", str(counter / "unsafe_counter.yaml"), "--timeout", value])
    capsys.readouterr()


def test_engine_options_do_not_change_the_verdict(capsys, counter: Path) -> None:
    code, out, _ = run(
        capsys,
        "check",
        counter / "unsafe_counter.yaml",
        "--indexing",
        "packed",
        "--strategy",
        "per-property",
        "--cache",
        "--memory",
        "--timeout",
        "30",
        "--max-depth",
        "50",
    )
    assert code == EXIT_VIOLATED and "VIOLATED" in out


# ------------------------------------------------------------------ explain


def test_explain_shows_a_saved_report_and_replays_it(
    capsys, counter: Path, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    run(capsys, "check", counter / "unsafe_counter.yaml", "-o", report)
    code, out, _ = run(capsys, "explain", report)
    assert code == EXIT_OK and "counterexample" in out

    code, out, _ = run(
        capsys, "explain", report, "--model", counter / "unsafe_counter.yaml"
    )
    assert (
        code == EXIT_OK and "replay [ok  ] counter-bound counterexample: valid" in out
    )


def test_explain_detects_a_forged_report(capsys, counter: Path, tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    run(capsys, "check", counter / "unsafe_counter.yaml", "-o", report)
    data = json.loads(report.read_text())
    data["results"][0]["counterexample"]["steps"][3]["state"]["counter"] = 2
    report.write_text(json.dumps(data))
    code, out, _ = run(
        capsys, "explain", report, "--model", counter / "unsafe_counter.yaml"
    )
    assert code == EXIT_ERROR and "replay [FAIL]" in out


def test_explain_warns_when_the_model_changed(
    capsys, counter: Path, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    run(capsys, "check", counter / "unsafe_counter.yaml", "-o", report)
    edited = tmp_path / "unsafe_counter.yaml"
    edited.write_text((counter / "unsafe_counter.yaml").read_text() + "\n# edited\n")
    _, out, _ = run(capsys, "explain", report, "--model", edited)
    assert "sha256 mismatch" in out


def test_explain_rejects_a_report_for_another_model(
    capsys, counter: Path, tmp_path: Path, examples_dir: Path
) -> None:
    report = tmp_path / "r.json"
    run(capsys, "check", counter / "unsafe_counter.yaml", "-o", report)
    code, _, err = run(
        capsys,
        "explain",
        report,
        "--model",
        examples_dir / "traffic_light" / "model.yaml",
    )
    assert code == EXIT_ERROR and "is for model" in err


def test_explain_without_traces(capsys, counter: Path, tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    run(
        capsys,
        "check",
        counter / "safe_counter.yaml",
        "-p",
        "counter-bound",
        "-o",
        report,
    )
    code, out, _ = run(
        capsys, "explain", report, "--model", counter / "safe_counter.yaml"
    )
    assert code == EXIT_OK and "no traces to re-validate" in out


def test_explain_missing_or_invalid_report(capsys, tmp_path: Path) -> None:
    code, _, err = run(capsys, "explain", tmp_path / "nope.json")
    assert code == EXIT_ERROR and "cannot read report" in err
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    code, _, err = run(capsys, "explain", bad)
    assert code == EXIT_ERROR and "schema_version" in err


# -------------------------------------------------------------------- graph


def test_graph_formats(capsys, counter: Path, tmp_path: Path) -> None:
    code, out, _ = run(capsys, "graph", counter / "unsafe_counter.yaml")
    assert code == EXIT_OK and out.startswith("flowchart LR")

    code, out, _ = run(
        capsys,
        "graph",
        counter / "unsafe_counter.yaml",
        "--format",
        "dot",
        "--highlight",
        "counter-bound",
    )
    assert out.startswith("digraph") and "#b91c1c" in out

    target = tmp_path / "g" / "graph.mmd"
    code, out, _ = run(capsys, "graph", counter / "unsafe_counter.yaml", "-o", target)
    assert target.read_text().startswith("flowchart") and f"wrote {target}" in out


def test_graph_png(capsys, counter: Path, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("networkx")
    code, out, _ = run(
        capsys,
        "graph",
        counter / "unsafe_counter.yaml",
        "--format",
        "png",
        "-o",
        tmp_path / "g.png",
    )
    assert code == EXIT_OK and (tmp_path / "g.png").read_bytes().startswith(b"\x89PNG")
    code, _, err = run(
        capsys, "graph", counter / "unsafe_counter.yaml", "--format", "png"
    )
    assert code == EXIT_ERROR and "--output is required" in err


def test_graph_truncation_note(capsys, counter: Path) -> None:
    _, _, err = run(
        capsys, "graph", counter / "unsafe_counter.yaml", "--max-nodes", "2"
    )
    assert "only the first 2 states are drawn" in err


def test_graph_highlight_unknown_property(capsys, counter: Path) -> None:
    code, _, err = run(
        capsys, "graph", counter / "unsafe_counter.yaml", "--highlight", "zz"
    )
    assert code == EXIT_ERROR and "Unknown property" in err


# ------------------------------------------------------------------- corpus


def test_corpus_command(capsys, examples_dir: Path) -> None:
    code, out, _ = run(capsys, "corpus", examples_dir / "manifest.json")
    assert code == EXIT_OK and "33/33 corpus cases passed." in out

    code, out, _ = run(
        capsys, "corpus", examples_dir / "manifest.json", "--case", "counter-unsafe-001"
    )
    assert code == EXIT_OK and "1/1 corpus cases passed." in out


def test_corpus_command_reports_failures(
    capsys, tmp_path: Path, examples_dir: Path
) -> None:
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "corpus_version": "1.0",
                "cases": [
                    {
                        "id": "wrong",
                        "model": str(examples_dir / "counter" / "unsafe_counter.yaml"),
                        "expected": [
                            {"property_id": "counter-bound", "status": "HOLDS"}
                        ],
                    }
                ],
            }
        )
    )
    code, out, _ = run(capsys, "corpus", manifest)
    assert code == EXIT_VIOLATED and "[FAIL] wrong" in out and "0/1" in out


# --------------------------------------------------------------------- misc


def test_version_and_help(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert f"prooftrail {__version__}" in capsys.readouterr().out
    assert "check" in build_parser().format_help()


def test_a_command_is_required(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2
    capsys.readouterr()


def test_installed_entry_point_style_invocation(counter: Path, root_dir: Path) -> None:
    env_path = str(root_dir / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "prooftrail",
            "check",
            str(counter / "unsafe_counter.yaml"),
        ],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": env_path, "PATH": ""},
        check=False,
    )
    assert proc.returncode == EXIT_VIOLATED
    assert "VIOLATED" in proc.stdout


def test_keyboard_interrupt_exit_code(monkeypatch, capsys, counter: Path) -> None:
    from prooftrail import cli

    def boom(args):
        raise KeyboardInterrupt

    monkeypatch.setitem(cli._COMMANDS, "validate", boom)
    assert main(["validate", str(counter / "safe_counter.yaml")]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_broken_pipe_is_not_an_error(monkeypatch, counter: Path) -> None:
    from prooftrail import cli

    def boom(args):
        raise BrokenPipeError

    monkeypatch.setitem(cli._COMMANDS, "validate", boom)
    assert main(["validate", str(counter / "safe_counter.yaml")]) == EXIT_OK
