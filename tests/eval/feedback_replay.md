# Feedback replay eval

Held-out MRR check after synthetic feedback on the training split and `learn`.

```bash
# Preferred (restores learning artifacts after run)
pareto-context-graph eval --repo-map fastapi=bench/fastapi --feedback-replay

# Module entry point
python -m pareto_context_graph.feedback_replay fastapi=bench/fastapi

# Pytest (fastapi skipped if bench not built)
pytest tests/test_feedback_replay.py -q
```

**Split:** train = seeded + `co_change` cases; holdout = query-only `concept` cases (no `seed_files`).

**Pass criteria:** held-out mean MRR improves by ≥ 0.03 after replay + learn.
