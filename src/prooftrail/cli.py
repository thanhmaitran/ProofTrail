"""Command-line interface.

Commands: ``validate``, ``check``, ``explain``, ``graph``, ``corpus``.

Exit codes
----------
``check`` / ``corpus``: 0 every property holds (every case passes),
1 a property is violated (a case failed), 2 error (bad input, model error),
3 inconclusive (resource limit hit) with nothing violated.
Other commands: 0 success, 2 error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from prooftrail import __version__
from prooftrail.corpus import run_corpus
from prooftrail.errors import ProofTrailError
from prooftrail.exploration.bfs import Limits
from prooftrail.exploration.indexing import INDEXING_KINDS
from prooftrail.model.parser import Specification, load_specification
from prooftrail.reporting.json_report import (
    build_report,
    load_report,
    render_report_text,
    write_report,
)
from prooftrail.verification.engine import STRATEGIES, verify
from prooftrail.verification.replay import replay_report
from prooftrail.verification.results import Status, Trace

EXIT_OK = 0
EXIT_VIOLATED = 1
EXIT_ERROR = 2
EXIT_UNKNOWN = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prooftrail",
        description="ProofTrail: an explicit-state model checking workbench.",
    )
    parser.add_argument(
        "--version", action="version", version=f"prooftrail {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("validate", help="check a specification and print a summary")
    p.add_argument("model", type=Path, help="specification file (.yaml, .yml, .json)")

    p = sub.add_parser(
        "check", help="verify the properties declared in a specification"
    )
    p.add_argument("model", type=Path)
    p.add_argument(
        "--property",
        "-p",
        action="append",
        dest="properties",
        metavar="ID",
        help="check only this property (repeatable)",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="what to print on stdout (default: text)",
    )
    p.add_argument(
        "--output", "-o", type=Path, help="also write the JSON report to this file"
    )
    p.add_argument(
        "--max-states",
        type=_positive_int,
        default=1_000_000,
        metavar="N",
        help="stop exploring after N states (default: 1000000)",
    )
    p.add_argument(
        "--max-depth",
        type=_positive_int,
        metavar="N",
        help="do not explore beyond depth N",
    )
    p.add_argument(
        "--timeout",
        type=_positive_float,
        metavar="SECONDS",
        help="stop exploring after this many seconds",
    )
    p.add_argument("--indexing", choices=INDEXING_KINDS, default="object")
    p.add_argument("--strategy", choices=STRATEGIES, default="shared")
    p.add_argument("--cache", action="store_true", help="memoise successor lists")
    p.add_argument(
        "--memory",
        action="store_true",
        help="measure peak memory with tracemalloc (slows the run)",
    )
    p.add_argument(
        "--no-timing",
        action="store_true",
        help="omit timings and environment so reports are reproducible",
    )
    p.add_argument(
        "--no-traces",
        action="store_true",
        help="do not print counterexample/witness traces in text output",
    )

    p = sub.add_parser("explain", help="show the evidence stored in a JSON report")
    p.add_argument("report", type=Path)
    p.add_argument(
        "--model", type=Path, help="re-validate every trace against this model file"
    )

    p = sub.add_parser("graph", help="draw the reachable state space")
    p.add_argument("model", type=Path)
    p.add_argument("--format", choices=("dot", "mermaid", "png"), default="mermaid")
    p.add_argument("--output", "-o", type=Path, help="output file (required for png)")
    p.add_argument("--max-nodes", type=_positive_int, default=200, metavar="N")
    p.add_argument(
        "--highlight",
        metavar="ID",
        help="highlight the counterexample of this property",
    )

    p = sub.add_parser(
        "corpus", help="verify a model corpus against its expected answers"
    )
    p.add_argument("manifest", type=Path)
    p.add_argument(
        "--case",
        action="append",
        dest="cases",
        metavar="ID",
        help="run only this case (repeatable)",
    )
    return parser


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


# ----------------------------------------------------------------- commands


def _cmd_validate(args: argparse.Namespace) -> int:
    spec = load_specification(args.model)
    system = spec.system
    print(f"OK  {args.model}")
    print(
        f"model: {system.id}"
        + (f" - {system.description}" if system.description else "")
    )
    print(f"schema_version: {spec.schema_version}")
    print(f"variables ({len(system.variables)}):")
    for decl in system.variables:
        initial = ", ".join(str(v) for v in decl.initial)
        print(f"  {decl.name}: {decl.describe_domain()}, initial {{{initial}}}")
    print(
        f"transitions ({len(system.transitions)}): "
        + (", ".join(t.id for t in system.transitions) or "none")
    )
    if system.labels:
        print(f"labels ({len(system.labels)}): " + ", ".join(system.labels))
    print(f"properties ({len(spec.properties)}):")
    for prop in spec.properties:
        print(f"  {prop.id} ({prop.kind})")

    unbounded = [
        d.name
        for d in system.variables
        if d.kind == "integer" and (d.minimum is None or d.maximum is None)
    ]
    if unbounded:
        print(
            "note: integer variable(s) without both bounds: "
            + ", ".join(unbounded)
            + "; exploration is only guaranteed to finish if the guards bound them."
        )
    return EXIT_OK


def _select(spec: Specification, wanted: list[str] | None) -> list:
    if not wanted:
        return list(spec.properties)
    chosen = []
    for pid in wanted:
        chosen.append(spec.property(pid))
    return chosen


def _cmd_check(args: argparse.Namespace) -> int:
    spec = load_specification(args.model)
    properties = _select(spec, args.properties)
    if not properties:
        raise ProofTrailError(
            f"{args.model}: the specification declares no properties."
        )
    run = verify(
        spec.system,
        properties,
        limits=Limits(args.max_states, args.max_depth, args.timeout),
        indexing=args.indexing,
        strategy=args.strategy,
        successor_cache=args.cache,
        measure_memory=args.memory,
    )
    report = build_report(
        run,
        source=str(args.model),
        sha256=spec.sha256,
        include_timing=not args.no_timing,
        include_environment=not args.no_timing,
    )
    if args.output:
        write_report(report, args.output)
    if args.format == "json":
        import json

        print(json.dumps(report, indent=2))
    else:
        print(render_report_text(report, show_traces=not args.no_traces))
        if args.output:
            print(f"report written to {args.output}")
    return run.exit_code()


def _cmd_explain(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    print(render_report_text(report))
    if args.model is None:
        return EXIT_OK

    spec = load_specification(args.model)
    recorded = (report.get("specification") or {}).get("sha256")
    print()
    if recorded and spec.sha256 and recorded != spec.sha256:
        print(
            "warning: the model file differs from the one the report was made from "
            "(sha256 mismatch)."
        )
    if report["model_id"] != spec.system.id:
        raise ProofTrailError(
            f"The report is for model {report['model_id']!r}, "
            f"but {args.model} defines {spec.system.id!r}."
        )
    outcomes = replay_report(spec.system, spec.properties, report)
    if not outcomes:
        print("replay: the report contains no traces to re-validate.")
        return EXIT_OK
    bad = 0
    for outcome in outcomes:
        mark = "ok  " if outcome.ok else "FAIL"
        print(
            f"replay [{mark}] {outcome.property_id} {outcome.trace_kind}: "
            f"{outcome.message}"
        )
        bad += not outcome.ok
    return EXIT_ERROR if bad else EXIT_OK


def _cmd_graph(args: argparse.Namespace) -> int:
    from prooftrail.verification.engine import verify as run_verify
    from prooftrail.visualization import (
        build_state_graph,
        render_png,
        to_dot,
        to_mermaid,
    )

    spec = load_specification(args.model)
    trace: Trace | None = None
    if args.highlight:
        prop = spec.property(args.highlight)
        result = run_verify(spec.system, [prop]).results[0]
        trace = result.counterexample or result.witness
        if result.status is Status.ERROR:
            raise ProofTrailError(result.message or "verification failed")
    graph = build_state_graph(spec.system, max_nodes=args.max_nodes, trace=trace)

    if args.format == "png":
        if args.output is None:
            raise ProofTrailError("--output is required for --format png.")
        render_png(graph, args.output, title=spec.system.id)
        print(f"wrote {args.output}")
        return EXIT_OK
    text = (
        to_dot(graph, title=spec.system.id)
        if args.format == "dot"
        else to_mermaid(graph)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    if graph.truncated:
        print(
            f"note: only the first {graph.total_states} states are drawn.",
            file=sys.stderr,
        )
    return EXIT_OK


def _cmd_corpus(args: argparse.Namespace) -> int:
    outcomes = run_corpus(args.manifest, only=set(args.cases) if args.cases else None)
    failed = 0
    for outcome in outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"[{mark}] {outcome.case_id}  ({outcome.checked} expectation(s))")
        for problem in outcome.problems:
            print(f"       - {problem}")
        failed += not outcome.passed
    print(f"\n{len(outcomes) - failed}/{len(outcomes)} corpus cases passed.")
    return EXIT_VIOLATED if failed else EXIT_OK


_COMMANDS = {
    "validate": _cmd_validate,
    "check": _cmd_check,
    "explain": _cmd_explain,
    "graph": _cmd_graph,
    "corpus": _cmd_corpus,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except ProofTrailError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:  # e.g. `prooftrail check ... | head`
        return EXIT_OK
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
