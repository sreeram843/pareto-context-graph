# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- PyPI publish workflow (`.github/workflows/publish.yml`) and README install one-liners.
- Tier-1 `pick_reason` on default `context` responses (full `diagnostics` still opt-in).
- `Candidate`, `ContextPipelineState`, and `PCG_ABLATE_*` ablation flags (`eval --ablation`).
- Fallback telemetry in `retrieval_confidence` (BM25→TF-IDF, Leiden→components, semantic backend).
- Context pipeline extracted to `context_pipeline.execute_context_pipeline()` (~700 lines moved out of `server.py`).
- CI quality gates: ruff, mypy, and pytest coverage.
- `taxonomy.py` — centralized query intent, file class, and noise-path rules.
- `context_ranking.py` — testable context ranking/packing helpers extracted from `server.py`.
- `retrieval_confidence` field on `context` responses.
- Versioned TTL caches (`repo_caches.py`) and automatic co-change edge decay on context requests.
- Holdout-gated ranker save in `feedback_replay.py` (LambdaMART discarded when it hurts holdout MRR).
- Phase 11.6 learned tier-1 prune (`apply_learned_tier1_prune`) + `check_learned_tier1_prune_gate`.

### Fixed
- `signing.py` no longer swallows all exceptions during Ed25519 verification.

## [0.1.0] - 2026-06-01

### Added
- Initial MCP server: git co-change graph, query-first context, eval harness, feedback loop.
