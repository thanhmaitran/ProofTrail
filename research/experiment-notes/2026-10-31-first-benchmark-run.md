# First full benchmark run

Source of numbers: `benchmarks/results/RESULTS.md` (generated, not edited).

Observations from that single run on a 2-vCPU machine:

- Runtime against reachable states has a log-log slope of 1.14. In the grid
  family the number of transitions per state grows with the number of
  variables, so a slope above 1 matches `O(V + E)`.
- `packed` indexing reduced traced peak memory to about a fifth at 65 536
  states with no visible change in runtime.
- Storing predecessor links changed time and memory by less than the run-to-run
  noise, because the state objects dominate memory.
- A successor cache made a single shared exploration slightly slower and used
  far more memory (each state is expanded once, so nothing is reused). It sped
  up the per-property strategy, which expands the same states repeatedly.
- Runtime grows with the number of properties because every discovered state is
  checked against each of them.

Caveat: one machine, one run; treat as an example.
