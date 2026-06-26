---
name: "Spec Kit — Brownfield"
description: >
  Generate a detailed technical spec for implementing a roadmap task against
  the existing pareto-context-graph codebase. Use when: specking a phase, writing an
  implementation plan, brownfield spec, task breakdown, phase 1/2/3/4/5 spec.
argument-hint: "Phase and task ID or description, e.g. 'Phase 1 Task 1.1 — golden set'"
agent: "agent"
tools:
  - read_file
  - file_search
  - grep_search
  - semantic_search
  - run_in_terminal
---

You are a senior engineer writing a **brownfield implementation spec** for `pareto-context-graph`.
The codebase is an existing, working prototype being hardened for production.

## Your task

Given the roadmap task identified in `$input` (or ask the user if not provided):

1. **Read the roadmap** — [docs/PRODUCTION_ROADMAP.md](../../docs/PRODUCTION_ROADMAP.md)
2. **Explore existing code** — find every file that the task will touch (use `grep_search`, `file_search`, `semantic_search`)
3. **Produce the spec** in the structure below

---

## Spec structure

### 1. Task identity
- Phase N · Task X.Y — `<task name>`
- Feature flag: `PCG_FEATURE_<NAME>` (off by default)
- Branch: `phase<N>/<task-slug>`

### 2. Current state (what already exists)
List the relevant files / functions / data structures that already handle the area this task touches.
Quote key signatures where helpful. Be specific — line numbers are fine.

### 3. Gap analysis
What is missing or broken relative to the acceptance criteria in the roadmap?
Reference **Known failures** (A, B, C, D, S1–S5) where relevant.

### 4. Target state (what needs to change)
For each file that will be modified or created, describe:
- **File**: path
- **Change type**: add / modify / delete
- **What**: exact function, class, or constant to add/change
- **Why**: how it closes the gap

### 5. New dependencies
| Package | Min version | Group | Justification |
|---------|------------|-------|---------------|
| …       | …          | …     | …             |

Only list packages not already in `pyproject.toml`. Approved: `tiktoken`, `tree-sitter`, `igraph>=0.11`.

### 6. Test plan
For each change in §4, list at minimum one test:
- **File**: `tests/test_<area>.py`
- **Test name**: `test_<what>_<condition>`
- **Assertion**: what the test verifies
- **Fixtures needed**: any new fixtures in `tests/fixtures/`

Eval gate: recall@5 ≥ target, MRR, nDCG@10 (see roadmap for per-task thresholds).

### 7. Acceptance criteria checklist
Copy verbatim from the roadmap, then mark each `[ ]` as not yet done.

### 8. Rollout notes
- Feature flag toggle command / env var
- Migration steps if schema changes (SQLite WAL, index rebuild)
- CI check to add / modify

### 9. Open questions
List anything that needs a decision before implementation begins.
Flag blockers with **[BLOCKER]**.

---

## Output format

Write the spec as a Markdown document ready to paste into a GitHub issue or Notion page.
Use `pareto-context-graph` conventions: Python 3.10+, `src/pareto_context_graph/` layout,
`Store` for SQLite access, `_handle_tool_call` dispatcher in `server.py`.
