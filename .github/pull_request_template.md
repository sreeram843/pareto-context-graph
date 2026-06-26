## Summary

<!-- What changed and why -->

## Eval (required for retrieval/ranking changes)

If this PR touches `server.py`, `blast.py`, `chunks.py`, `walk.py`, `store.py`, or `eval.py`:

- [ ] Ran `make eval-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'` locally (or CI is green)
- [ ] `make eval-audit REPOS='fastapi=bench/fastapi httpx=bench/httpx'` passes (no recall@5 = 0)
- [ ] Paste eval summary delta below if metrics moved

```
Mean recall@5:
Mean MRR:
Mean NDCG@10:
```

## Test plan

- [ ] `pytest -q`
- [ ] `make eval-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'` (if applicable)
