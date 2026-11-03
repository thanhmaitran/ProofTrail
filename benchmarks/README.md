# Benchmarks

```
python benchmarks/run_benchmarks.py                 # full run (several minutes)
python benchmarks/run_benchmarks.py --quick         # smoke test (seconds)
python benchmarks/run_benchmarks.py --repeats 15    # more samples per case
python benchmarks/run_benchmarks.py --output-dir /tmp/bench
```

- `generators.py` builds synthetic models whose state space has a closed form.
  The runner aborts if the engine's counts disagree with the formula.
- `run_benchmarks.py` runs six experiments (state-space growth, state indexing,
  predecessor storage, strategy and cache, property count, counterexample
  length) and writes `results.json`, `results.csv`, `RESULTS.md` and PNG charts.
- `results/` holds the committed output of one full run. All values in it are
  measured, none are typed in. See `docs/benchmark-methodology.md` for the
  procedure and threats to validity.

Absolute timings depend on hardware. Compare shapes and ratios, and re-run on
your own machine before quoting numbers.
