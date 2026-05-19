# Benchmarks

Run benchmark scripts after each major optimization:

```bash
python tests/perf/bench_build.py
python tests/perf/bench_query.py
```

Track:
- build wall time
- query p50/p95
- token usage at tier 1/2/3

Record results here per profile (`tiny`, `medium`, `large`, `huge`).

## Latest Results

Environment:
- Python: `3.11`
- Command prefix: `PYTHONPATH=.`

### Build

Command:

```bash
python3.11 tests/perf/bench_build.py
```

Results:
- tiny: `0.040s`
- medium: `0.647s`

### Query

Command:

```bash
python3.11 tests/perf/bench_query.py
```

Results:
- p50: `0.364s`
- p95: `0.378s`
- samples: `3`

### Notes

- Current benchmark scripts report `tiny` and `medium` for build and aggregate query latency stats.
- `huge` fixture numbers were not emitted by the current benchmark script run.
