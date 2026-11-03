"""Reproducible benchmarks for ProofTrail.

Run from the repository root::

    python benchmarks/run_benchmarks.py                # full run
    python benchmarks/run_benchmarks.py --quick        # small sizes, for a smoke test

Every number written to ``benchmarks/results/`` is measured by this script on the
machine that ran it; nothing is hard-coded. State, edge and depth counts are
additionally checked against closed-form formulas from ``generators.py`` so a
benchmark cannot silently time a wrong exploration.

Methodology (see ``docs/benchmark-methodology.md``):

* one untimed warm-up run, then ``--repeats`` timed runs; median, min and
  standard deviation are reported;
* time is measured with ``time.perf_counter`` and the garbage collector is
  disabled inside each timed region (collected between runs);
* peak memory is measured in a *separate* run with ``tracemalloc`` because
  tracing slows execution; time and memory are never taken from the same run;
* parsing is excluded from timings (the specification is parsed once per case).
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import generators as gen  # noqa: E402

import prooftrail  # noqa: E402
from prooftrail import parse_specification, verify  # noqa: E402
from prooftrail.exploration.bfs import explore  # noqa: E402

BENCHMARK_VERSION = "1.0"


# ---------------------------------------------------------------- measuring


def time_runs(fn: Callable[[], Any], repeats: int) -> dict[str, float]:
    """Time ``fn`` ``repeats`` times after one warm-up run."""
    fn()
    samples: list[float] = []
    for _ in range(repeats):
        gc.collect()
        gc.disable()
        try:
            start = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - start)
        finally:
            gc.enable()
    return {
        "median_s": statistics.median(samples),
        "min_s": min(samples),
        "max_s": max(samples),
        "stdev_s": statistics.stdev(samples) if len(samples) > 1 else 0.0,
        "repeats": repeats,
    }


def peak_memory(fn: Callable[[], Any]) -> int:
    """Peak traced allocation, in bytes, of one run of ``fn``."""
    gc.collect()
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def machine_info() -> dict[str, Any]:
    cpu = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    import os

    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "prooftrail": prooftrail.__version__,
    }


def log_log_slope(xs: list[float], ys: list[float]) -> float:
    """Least-squares slope of log(y) against log(x): the empirical exponent."""
    lx = [math.log(x) for x in xs]
    ly = [math.log(y) for y in ys]
    mx, my = statistics.fmean(lx), statistics.fmean(ly)
    num = sum((a - mx) * (b - my) for a, b in zip(lx, ly, strict=True))
    den = sum((a - mx) ** 2 for a in lx)
    return num / den


# --------------------------------------------------------------- experiments


def check_counts(run: Any, expected: gen.Expected, label: str) -> None:
    stats = run.statistics
    got = (
        stats["states_explored"],
        stats["transitions_examined"],
        stats["max_depth_reached"],
    )
    want = (expected.states, expected.transitions, expected.max_depth)
    if got != want:
        raise SystemExit(
            f"benchmark sanity check failed for {label}: engine reported "
            f"(states, transitions, depth) = {got}, formula says {want}"
        )


def exp_state_space_growth(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E1: time and memory against the number of reachable states."""
    rows = []
    for n in cfg["grid_n"]:
        k = cfg["grid_k"]
        spec = parse_specification(gen.grid(n, k))
        expected = gen.grid_expected(n, k)

        def run(spec: Any = spec) -> Any:
            return verify(spec.system, spec.properties)

        check_counts(run(), expected, f"grid({n},{k})")
        timing = time_runs(run, cfg["repeats"])
        rows.append(
            {
                "experiment": "state_space_growth",
                "model": f"grid-{n}x{k}",
                "variables": n,
                "states": expected.states,
                "transitions": expected.transitions,
                "depth": expected.max_depth,
                "peak_bytes": peak_memory(run),
                **timing,
            }
        )
    return rows


def exp_indexing(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E2: ``object`` versus ``packed`` state indexing."""
    rows = []
    for n in cfg["grid_n"]:
        k = cfg["grid_k"]
        spec = parse_specification(gen.grid(n, k))
        expected = gen.grid_expected(n, k)
        for kind in ("object", "packed"):

            def run(spec: Any = spec, kind: str = kind) -> Any:
                return verify(spec.system, spec.properties, indexing=kind)

            check_counts(run(), expected, f"grid({n},{k}) indexing={kind}")
            rows.append(
                {
                    "experiment": "indexing",
                    "variant": kind,
                    "model": f"grid-{n}x{k}",
                    "states": expected.states,
                    "peak_bytes": peak_memory(run),
                    **time_runs(run, cfg["repeats"]),
                }
            )
    return rows


def exp_predecessors(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E3: cost of storing predecessor links (needed for counterexamples)."""
    rows = []
    for n in cfg["grid_n"]:
        k = cfg["grid_k"]
        spec = parse_specification(gen.grid(n, k))
        for track in (False, True):

            def run(spec: Any = spec, track: bool = track) -> Any:
                return explore(spec.system, track_parents=track)

            rows.append(
                {
                    "experiment": "predecessors",
                    "variant": "with predecessors" if track else "without",
                    "model": f"grid-{n}x{k}",
                    "states": gen.grid_expected(n, k).states,
                    "peak_bytes": peak_memory(run),
                    **time_runs(run, cfg["repeats"]),
                }
            )
    return rows


def exp_strategy_and_cache(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E4: shared versus per-property exploration, with and without a cache."""
    n, k, props = cfg["strategy_n"], cfg["grid_k"], cfg["strategy_props"]
    spec = parse_specification(gen.many_properties(gen.grid(n, k), props))
    states = gen.grid_expected(n, k).states
    rows = []
    for strategy in ("shared", "per-property"):
        for cache in (False, True):

            def run(spec: Any = spec, strategy: str = strategy, cache: bool = cache):
                return verify(
                    spec.system,
                    spec.properties,
                    strategy=strategy,
                    successor_cache=cache,
                )

            result = run()
            if any(r.status.value != "HOLDS" for r in result.results):
                raise SystemExit("strategy benchmark expects only HOLDS results")
            rows.append(
                {
                    "experiment": "strategy_cache",
                    "variant": f"{strategy}{' + cache' if cache else ''}",
                    "model": f"grid-{n}x{k}",
                    "properties": props,
                    "states": states,
                    "explorations": result.statistics["explorations"],
                    "peak_bytes": peak_memory(run),
                    **time_runs(run, cfg["repeats"]),
                }
            )
    return rows


def exp_property_count(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E5: cost against the number of properties on a fixed model."""
    n, k = cfg["strategy_n"], cfg["grid_k"]
    rows = []
    for count in cfg["property_counts"]:
        spec = parse_specification(gen.many_properties(gen.grid(n, k), count))

        def run(spec: Any = spec) -> Any:
            return verify(spec.system, spec.properties)

        rows.append(
            {
                "experiment": "property_count",
                "model": f"grid-{n}x{k}",
                "properties": count,
                "states": gen.grid_expected(n, k).states,
                **time_runs(run, cfg["repeats"]),
            }
        )
    return rows


def exp_counterexample_length(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """E6: cost of finding and validating a counterexample of growing length."""
    rows = []
    for length in cfg["chain_lengths"]:
        spec = parse_specification(gen.chain(length, violated=True))

        def run(spec: Any = spec) -> Any:
            return verify(spec.system, spec.properties)

        result = run()
        got = len(result.results[0].counterexample) - 1  # steps, not states
        if result.results[0].status.value != "VIOLATED" or got != length:
            raise SystemExit(f"chain({length}) counterexample has {got} steps")
        rows.append(
            {
                "experiment": "counterexample_length",
                "model": f"chain-{length}-bad",
                "trace_steps": length,
                "peak_bytes": peak_memory(run),
                **time_runs(run, cfg["repeats"]),
            }
        )
    return rows


# -------------------------------------------------------------------- output


def write_outputs(
    rows: list[dict[str, Any]], meta: dict[str, Any], out_dir: Path
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, indent=2) + "\n", encoding="utf-8"
    )
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def make_charts(rows: list[dict[str, Any]], out_dir: Path) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping charts", file=sys.stderr)
        return []

    blue, orange, aqua, violet = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6"
    ink, muted, grid_color, surface = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
    plt.rcParams.update(
        {
            "figure.facecolor": surface,
            "axes.facecolor": surface,
            "axes.edgecolor": grid_color,
            "axes.labelcolor": muted,
            "xtick.color": muted,
            "ytick.color": muted,
            "text.color": ink,
            "axes.grid": True,
            "grid.color": grid_color,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 10,
        }
    )

    def pick(experiment: str, variant: str | None = None) -> list[dict[str, Any]]:
        return [
            r
            for r in rows
            if r["experiment"] == experiment
            and (variant is None or r.get("variant") == variant)
        ]

    def finish(fig: Any, ax: Any, name: str, title: str, subtitle: str) -> None:
        ax.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=22)
        ax.text(
            0,
            1.03,
            subtitle,
            transform=ax.transAxes,
            fontsize=9,
            color=muted,
            va="bottom",
        )
        fig.tight_layout()
        fig.savefig(out_dir / name, dpi=160)
        plt.close(fig)

    written: list[str] = []

    # runtime vs states (log-log) with empirical exponent
    data = pick("state_space_growth")
    xs = [r["states"] for r in data]
    ys = [r["median_s"] for r in data]
    slope = log_log_slope(xs, ys)
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.errorbar(
        xs,
        ys,
        yerr=[r["stdev_s"] for r in data],
        color=blue,
        marker="o",
        markersize=6,
        linewidth=2,
        capsize=3,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("reachable states")
    ax.set_ylabel("median runtime (s)")
    finish(
        fig,
        ax,
        "runtime-vs-states.png",
        "Runtime vs. reachable states",
        f"Log-log slope {slope:.2f} (1.0 = linear). Bars: one standard deviation.",
    )
    written.append("runtime-vs-states.png")

    # memory by indexing variant
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for variant, color in (("object", blue), ("packed", orange)):
        d = pick("indexing", variant)
        ax.plot(
            [r["states"] for r in d],
            [r["peak_bytes"] / 2**20 for r in d],
            color=color,
            marker="o",
            markersize=6,
            linewidth=2,
            label=variant,
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("reachable states")
    ax.set_ylabel("peak traced memory (MiB)")
    ax.legend(frameon=False)
    finish(
        fig,
        ax,
        "memory-vs-indexing.png",
        "Peak memory by state-indexing strategy",
        "tracemalloc peak of one verification run.",
    )
    written.append("memory-vs-indexing.png")

    # runtime by indexing variant
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for variant, color in (("object", blue), ("packed", orange)):
        d = pick("indexing", variant)
        ax.plot(
            [r["states"] for r in d],
            [r["median_s"] for r in d],
            color=color,
            marker="o",
            markersize=6,
            linewidth=2,
            label=variant,
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("reachable states")
    ax.set_ylabel("median runtime (s)")
    ax.legend(frameon=False)
    finish(
        fig,
        ax,
        "runtime-vs-indexing.png",
        "Runtime by state-indexing strategy",
        "Same exploration, same verdicts; only the index representation differs.",
    )
    written.append("runtime-vs-indexing.png")

    # strategy / cache variants
    d = pick("strategy_cache")
    labels = [r["variant"] for r in d]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.bar(labels, [r["median_s"] for r in d], color=aqua, width=0.55)
    ax.errorbar(
        labels,
        [r["median_s"] for r in d],
        yerr=[r["stdev_s"] for r in d],
        fmt="none",
        ecolor=ink,
        capsize=3,
    )
    ax.set_ylabel("median runtime (s)")
    ax.tick_params(axis="x", labelsize=8)
    finish(
        fig,
        ax,
        "strategy-and-cache.png",
        "Exploration strategy and successor cache",
        f"{d[0]['properties']} properties on {d[0]['model']} "
        f"({d[0]['states']} states).",
    )
    written.append("strategy-and-cache.png")

    # property count
    d = pick("property_count")
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(
        [r["properties"] for r in d],
        [r["median_s"] for r in d],
        color=violet,
        marker="o",
        markersize=6,
        linewidth=2,
    )
    ax.set_xlabel("properties checked")
    ax.set_ylabel("median runtime (s)")
    finish(
        fig,
        ax,
        "runtime-vs-properties.png",
        "Runtime vs. number of properties",
        f"Fixed model {d[0]['model']} ({d[0]['states']} states), shared exploration.",
    )
    written.append("runtime-vs-properties.png")

    # counterexample length
    d = pick("counterexample_length")
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(
        [r["trace_steps"] for r in d],
        [r["median_s"] for r in d],
        color=orange,
        marker="o",
        markersize=6,
        linewidth=2,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("counterexample length (steps)")
    ax.set_ylabel("median runtime (s)")
    finish(
        fig,
        ax,
        "runtime-vs-trace-length.png",
        "Runtime vs. counterexample length",
        "Includes reconstruction and independent re-validation of the trace.",
    )
    written.append("runtime-vs-trace-length.png")
    return written


def summary_markdown(
    rows: list[dict[str, Any]], meta: dict[str, Any], charts: list[str]
) -> str:
    def table(experiment: str, columns: list[tuple[str, str, Callable[[Any], str]]]):
        sel = [r for r in rows if r["experiment"] == experiment]
        head = "| " + " | ".join(c[0] for c in columns) + " |"
        sep = "|" + "|".join("---" for _ in columns) + "|"
        body = [
            "| " + " | ".join(fmt(r.get(key)) for _, key, fmt in columns) + " |"
            for r in sel
        ]
        return "\n".join([head, sep, *body])

    ms = lambda v: f"{v * 1000:.2f}"  # noqa: E731
    mib = lambda v: f"{v / 2**20:.2f}"  # noqa: E731
    text = lambda v: str(v)  # noqa: E731
    num = lambda v: f"{v:,}"  # noqa: E731

    growth = [r for r in rows if r["experiment"] == "state_space_growth"]
    slope = log_log_slope(
        [r["states"] for r in growth], [r["median_s"] for r in growth]
    )
    m = meta["machine"]
    lines = [
        "# Benchmark results",
        "",
        "Generated by `benchmarks/run_benchmarks.py`; do not edit by hand.",
        "",
        f"- ProofTrail {m['prooftrail']}, {m['implementation']} {m['python']}",
        f"- {m['platform']} ({m['machine']}), {m['logical_cpus']} logical CPUs",
        f"- CPU: {m['cpu']}",
        f"- Repeats per case: {meta['config']['repeats']} (after one warm-up run)",
        f"- Run mode: {'quick' if meta['quick'] else 'full'}",
        "",
        "## E1. State-space growth",
        "",
        table(
            "state_space_growth",
            [
                ("model", "model", text),
                ("states", "states", num),
                ("transitions", "transitions", num),
                ("median ms", "median_s", ms),
                ("stdev ms", "stdev_s", ms),
                ("peak MiB", "peak_bytes", mib),
            ],
        ),
        "",
        f"Empirical log-log slope of runtime against states: **{slope:.2f}**.",
        "",
        "## E2. State indexing",
        "",
        table(
            "indexing",
            [
                ("variant", "variant", text),
                ("model", "model", text),
                ("median ms", "median_s", ms),
                ("peak MiB", "peak_bytes", mib),
            ],
        ),
        "",
        "## E3. Predecessor storage",
        "",
        table(
            "predecessors",
            [
                ("variant", "variant", text),
                ("model", "model", text),
                ("median ms", "median_s", ms),
                ("peak MiB", "peak_bytes", mib),
            ],
        ),
        "",
        "## E4. Strategy and successor cache",
        "",
        table(
            "strategy_cache",
            [
                ("variant", "variant", text),
                ("explorations", "explorations", text),
                ("median ms", "median_s", ms),
                ("peak MiB", "peak_bytes", mib),
            ],
        ),
        "",
        "## E5. Number of properties",
        "",
        table(
            "property_count",
            [
                ("properties", "properties", text),
                ("median ms", "median_s", ms),
                ("stdev ms", "stdev_s", ms),
            ],
        ),
        "",
        "## E6. Counterexample length",
        "",
        table(
            "counterexample_length",
            [
                ("steps", "trace_steps", num),
                ("median ms", "median_s", ms),
                ("peak MiB", "peak_bytes", mib),
            ],
        ),
        "",
        "## Charts",
        "",
        *[f"![{c}]({c})" for c in charts],
        "",
    ]
    return "\n".join(lines)


CONFIGS: dict[str, dict[str, Any]] = {
    "full": {
        "repeats": 7,
        "grid_k": 4,
        "grid_n": [2, 3, 4, 5, 6, 7, 8],
        "strategy_n": 6,
        "strategy_props": 4,
        "property_counts": [1, 2, 4, 8, 16, 32, 64],
        "chain_lengths": [100, 300, 1000, 3000, 10000, 30000],
    },
    "quick": {
        "repeats": 3,
        "grid_k": 3,
        "grid_n": [2, 3, 4, 5],
        "strategy_n": 4,
        "strategy_props": 3,
        "property_counts": [1, 4, 16],
        "chain_lengths": [50, 200, 800],
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="small sizes (smoke test)")
    parser.add_argument("--repeats", type=int, help="timed repeats per case")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "benchmarks" / "results",
        help="where to write results.json/csv, charts and RESULTS.md",
    )
    args = parser.parse_args(argv)

    cfg = dict(CONFIGS["quick" if args.quick else "full"])
    if args.repeats:
        cfg["repeats"] = args.repeats

    rows: list[dict[str, Any]] = []
    for experiment in (
        exp_state_space_growth,
        exp_indexing,
        exp_predecessors,
        exp_strategy_and_cache,
        exp_property_count,
        exp_counterexample_length,
    ):
        print(f"running {experiment.__name__} ...", flush=True)
        rows.extend(experiment(cfg))

    meta = {
        "benchmark_version": BENCHMARK_VERSION,
        "quick": args.quick,
        "config": cfg,
        "machine": machine_info(),
    }
    write_outputs(rows, meta, args.output_dir)
    charts = make_charts(rows, args.output_dir)
    (args.output_dir / "RESULTS.md").write_text(
        summary_markdown(rows, meta, charts), encoding="utf-8"
    )
    print(f"wrote results to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
