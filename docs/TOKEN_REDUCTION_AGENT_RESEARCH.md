# Agent token reduction research (Awesome-Collection-Token-Reduction)

Source: [ZLKong/Awesome-Collection-Token-Reduction — Agentic Systems](https://github.com/ZLKong/Awesome-Collection-Token-Reduction#agent)

This note maps **agent-oriented** token-reduction papers to **pareto-context-graph** goals:
pick the right files first (graph retrieval), return minimal honest tiers (packing),
and avoid re-sending context on follow-ups (`already_have`).

## Papers reviewed (agent section)

| Paper | Core idea | Relevance to us |
|-------|-----------|-----------------|
| **SWE-Pruner** (ACL'26) | Self-adaptive pruning of coding-agent context using task signals | **High** — aligns with hub-aware caps, `truncated`, high-fanout fast path |
| **ACON** (ICLR'26) | Learned/context-aware compression for long-horizon agent trajectories | **High** — motivates `already_have`, counterfactual logging, feedback ranker |
| **AgentSlimming** (ACL'26) | Cost-aware multi-agent routing; reduce redundant agent turns | **Medium** — stack with in-house prune for post-retrieval compression |
| **AgentOCR** (arXiv) | Compress agent *history* via visual/textual self-representation | **Low near-term** — different layer (chat history vs repo files) |
| **S2-MAD** (NAACL'25) | Token barrier in multi-agent debate via structured summaries | **Low** — not our primary use case |
| **CARL** (arXiv) | Critical-action RL for multi-step agents | **Medium** — informs which files to log in `feedback_cite` |
| **PilotDeck** (Project'26) | Workspace-centric agent OS | **Medium** — validates MCP + graph-before-grep workflow |

Cross-cutting survey: [Token Reduction Should Go Beyond Efficiency…](https://arxiv.org/abs/2505.18227) — token reduction for *quality* (focus, dedup), not only speed.

## What we already implement (agent-aligned)

1. **Retrieve-before-read (Tier 1 summaries)** — Agent grep reads whole files; we return path + one-line summary (~30 tokens/file).
2. **Graph-constrained expansion** — Co-change BFS/RWR instead of unbounded repo scan (SWE-Pruner-style scope control).
3. **High-fanout fast path** — Degree ≥ 500: depth-1 BFS, skip hybrid/semantic/MMR (prevents hub blow-up).
4. **Deadline + cancel** — `timeout_ms`, `truncated`, MCP `notifications/cancelled` (ACON-style horizon control).
5. **Delta context** — `already_have` skips files already in the agent window.
6. **MMR diversity** — Reduces near-duplicate paths in the candidate set.
7. **Feedback loop** — `feedback_cite` / `learn` reweights files from real agent usage.

## Recommended next implementations (priority)

### P0 — Ship now (low effort, high agent impact)

| Idea | From | Implementation sketch |
|------|------|------------------------|
| **Adaptive stage1 cap from query complexity** | SWE-Pruner, ACON | **Done** — short → 25, medium → 50, long → 75; hub → 75 (`adaptive_cap.py`) |
| **Session memory file** | ACON, StructMem (language section) | **Done** — `.pareto-context-graph/session.json`; auto-fill `already_have` (`session.py`) |
| **Prune agent grep baseline in eval** | LLMLingua / Perception Compressor | **Done** — `reduction_vs_agent` on eval cases + bench `token_savings` rows |

### P1 — Next quarter

| Idea | From | Implementation sketch |
|------|------|------------------------|
| **SWE-Pruner-style tool-output pruning** | SWE-Pruner | **Done** — `PCG_FEATURE_SUMMARY_PRUNE=1`; CI `check_summary_prune_gate` on fastapi concept cases |
| **Learned prune policy from feedback** | ACON, AgentSlimming | Train ranker feature: `was_in_already_have`, `dwell_seconds`, `rejected` |
| **In-house prune stack default** | AgentSlimming | Built-in `compression: prune` + eval gate — [CONTEXT_COMPRESSION.md](CONTEXT_COMPRESSION.md) |
| **Counterfactual replay in CI** | Fewer is More (EMNLP'24) | **Done** — `check_grep_counterfactual_gate` on `make eval-check` |

### P2 — Research / optional

| Idea | From | Notes |
|------|------|-------|
| **AgentOCR-style history compression** | AgentOCR | Compress prior *tool outputs* in the IDE, not repo files — partner integration |
| **Multi-agent token budget split** | AgentSlimming | MCP policy: per-role `token_budget` in org policy file |
| **Optical/visual sketch of graph** | AgentOCR | Low value vs our tier-1 text summaries for code |

## Anti-patterns (papers warn against)

- **Whole-repo TF-IDF on every query** — We now skip semantic index on large/high-fanout graphs (linux lesson).
- **Full corpus token estimates in hot path** — `context_savings` skipped on large/truncated responses.
- **Treating all tokens equally in reasoning** — High-entropy minority tokens matter for reasoning models; for *orientation* tier-1 summaries are sufficient (NeurIPS'25 80/20 reasoning work).

## Success metrics (agent benchmark)

Track on fastapi + kubernetes eval:

| Metric | Target | Tool |
|--------|--------|------|
| `recall@5` | ≥ baseline − 2 pts | `make eval` |
| `reduction_vs_agent` | ≥ 3× vs grep-top-3 | eval `context_savings` |
| `budget_honesty` | ≥ 0.95 | tokenizer packing |
| Hub `context` p95 | < 1 s | `bench-huge` |
| `truncated_samples` at 5 s | 0 on hub seeds | `bench_results.json` |

## References

- Awesome list: https://github.com/ZLKong/Awesome-Collection-Token-Reduction#agent
- SWE-Pruner: ACL 2026 (coding-agent adaptive pruning)
- ACON: ICLR 2026 (long-horizon agent context compression)
- LLMLingua / Perception Compressor: prompt compression (stack *after* graph retrieval)
- Our stack doc: [CONTEXT_COMPRESSION.md](CONTEXT_COMPRESSION.md)
- Our benchmarks: [BENCHMARKS.md](BENCHMARKS.md)
