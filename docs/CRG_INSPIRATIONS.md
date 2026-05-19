# Inspirations from code-review-graph

[code-review-graph](https://github.com/tirth8205/code-review-graph) is a mature
open-source project (16.8k stars, v2.3.3) that solves an overlapping problem from
a different angle.  This document catalogs the features most worth incorporating
into `code-graph-mcp`, skipping anything already covered by the Production Roadmap
(tree-sitter symbol index → Phase 3 Task 3.1; BM25 → Phase 3 Task 3.2; embeddings
→ Phase 3 Task 3.3; VS Code extension → Phase 5 Task 5.7).

Dependencies are now acceptable.  Each section notes what to `pip install`.

---

## 1  Structural edge types (calls, inheritance, test coverage)

### What CRG does
After tree-sitter parsing, CRG emits three classes of edges in addition to
co-change:

| Edge class | Derived from | Example |
|---|---|---|
| `calls` | Call-site extraction | `payment_service.py → stripe_client.py` |
| `inherits` | Class definition | `PatientController → ApplicationController` |
| `tests` | File + class naming conventions + import analysis | `test_payment.py → payment_service.py` |

Each edge carries a confidence tag: `EXTRACTED` (found in AST), `INFERRED`
(heuristic e.g. naming), `AMBIGUOUS` (dynamic dispatch, reflection).

### Why it matters for us
Co-change edges capture **behavioural coupling** — files that break together.
Structural edges capture **intentional coupling** — what the author meant.  Together
they find files that are both logically and historically related, which is the
highest-confidence signal.

Test-coverage edges are particularly valuable for the failing-test debugging
intent: seed with a broken test file, traverse `tests` edges to the SUT, then
co-change edges from the SUT to its historical travel companions.

### Implementation plan

**Dependencies**: `tree-sitter>=0.22`, per-language grammar wheels (e.g.
`tree-sitter-python`, `tree-sitter-ruby`, `tree-sitter-javascript`,
`tree-sitter-typescript`, `tree-sitter-go`, `tree-sitter-java`, `tree-sitter-rust`).
These are already approved for Phase 3.

1. Extend `graph.py` `build_graph()` to accept an `edge_kinds` set.
2. Add `structural.py` with a `StructuralExtractor` that, for each language,
   runs tree-sitter and yields `(src, dst, kind, confidence)` tuples.
3. Store edge kind + confidence in a new `edge_meta` SQLite table alongside
   the existing `edges` table.
4. In `server.py` ranking, apply edge-kind weights:
   ```python
   EDGE_KIND_WEIGHT = {"co_change": 1.0, "calls": 0.9, "tests": 0.85, "inherits": 0.7}
   ```
5. Expose `edge_kind` filter on `neighbours` and `blast` commands.

**Feature flag**: `CGMCP_FEATURE_STRUCTURAL_EDGES`

---

## 2  Leiden community detection

### What CRG does
Replaces naive connected-component clustering with the Leiden algorithm, which
optimises modularity.  Communities above a size threshold are recursively
sub-divided.  Resolution is tuned per profile (small repo → high resolution →
more, smaller communities; large repo → lower resolution → fewer, coarser ones).

### Why it matters for us
The current `communities` command groups files by connected component, which
produces one giant "everything" cluster on any well-connected repo.  Leiden
returns meaningful architectural boundaries (payments, auth, reporting, …) that
are useful for onboarding and for scoping context calls.

### Implementation plan

**Dependency**: `igraph>=0.11` (Leiden is built in; pure C, fast).

1. New function `leiden_communities(store: Store, resolution: float) -> list[list[str]]`
   in a new `community.py` module.
2. Replace the connected-component logic in the `communities` command handler
   in `server.py`.
3. Resolution defaults: `tiny` → 1.5, `medium` → 1.0, `large` → 0.7, `huge` → 0.5
   (configurable via profile).
4. Auto-split communities larger than `max_community_size` (profile default: 50
   files) by re-running Leiden on the sub-graph.
5. Return community label and modularity score in the response.

**Feature flag**: `CGMCP_FEATURE_LEIDEN`

---

## 3  Interactive graph visualisation + export

### What CRG does
- Renders a D3.js force-directed graph in a local web UI (served by the daemon).
- Exports to **GraphML** (Gephi, yEd), **Neo4j Cypher**, **Obsidian vault**
  (markdown notes with `[[wikilinks]]`), and SVG snapshot.

### Why it matters for us
None of our tools give developers a way to *see* the graph.  Visualisation
immediately communicates architectural hotspots and cluster boundaries that take
paragraphs to describe in text.  The Obsidian export is particularly useful for
offline browsing and annotating large repos.

### Implementation plan

**Dependencies**: none for export formats (pure Python). For the local web UI:
`flask>=3.0` or `fastapi>=0.110` + `uvicorn` (lightweight; optional install group).

#### 3a  Export commands (no new deps)

New `export.py` module with:

```python
def to_graphml(store: Store, path: str) -> None: ...
def to_cypher(store: Store, path: str) -> None: ...   # Neo4j LOAD CSV / MERGE statements
def to_obsidian(store: Store, vault_path: str) -> None: ...  # one .md per file-node
```

Expose as CLI subcommand: `code-graph export --format graphml|cypher|obsidian --out <path>`
and as MCP command `export`.

#### 3b  Local web UI (optional group)

- `daemon.py` already runs a background process — add a `/graph` HTTP endpoint
  that serves a single-page D3 app (self-contained HTML; no build step).
- Node colour = community; node size = degree; edge opacity = weight.
- Clicking a node highlights its neighbours and shows path + top-5 co-change
  partners in a side panel.
- Gate behind `--ui` flag on `serve`.

**Feature flag**: `CGMCP_FEATURE_EXPORT`, `CGMCP_FEATURE_UI`

---

## 4  Platform auto-configuration  (`install` command)

### What CRG does
`code-review-graph install` detects which AI coding assistants are present (Cursor,
Claude Code, Windsurf, Zed, Continue, Copilot, OpenCode, etc.) and writes or
patches their MCP config files automatically.  Users get a working setup with one
command.

### Why it matters for us
Our current install story is "manually add JSON to your editor's config".  This
is a significant adoption barrier, especially for engineers who use multiple tools.

### Implementation plan

**Dependencies**: none (file system detection).

Extend `cli.py` `install` subcommand:

1. Scan known config locations for each platform (hard-coded path map in
   `install.py`).
2. If found, patch the MCP server list using `json.loads / json.dumps` or a
   simple string search for YAML-based configs.
3. Print a summary: `✓ Cursor   ✓ Claude Code   – Windsurf (not found)`.
4. Add `--dry-run` flag to preview changes without writing.
5. Add `--uninstall` to remove the entry (useful for version upgrades that change
   the config key).

Config locations to support (priority order):

| Platform | Config path |
|---|---|
| Cursor | `~/.cursor/mcp.json` |
| Claude Code | `~/.claude/mcp.json` |
| Windsurf | `~/.windsurf/mcp.json` |
| Zed | `~/.config/zed/settings.json` |
| Continue | `~/.continue/config.json` |
| GitHub Copilot (VS Code) | `.vscode/mcp.json` in workspace |
| OpenCode | `~/.opencode/config.toml` |

**Feature flag**: none — this is purely additive CLI.

---

## 5  MCP prompt templates

### What CRG does
Ships 5 named prompt templates that a client can invoke by name.  They chain
multiple tool calls into a structured workflow:

| Template | What it does |
|---|---|
| `code_review` | blast radius → context → risk summary |
| `architecture_overview` | communities → hotspots → narrative |
| `debug_issue` | search → neighbours → context → reproduction steps |
| `onboard_file` | context → community → wiki snippet |
| `pre_merge_check` | changed-files → blast → savings |

### Why it matters for us
MCP supports a `prompts` capability alongside `tools`.  Prompt templates let
clients (Copilot, Claude, Cursor) surface high-level workflows in their UI without
requiring the user to know which tool to call.

### Implementation plan

**Dependencies**: none.

1. Add a `prompts/` list to the MCP `initialize` response in `server.py`.
2. Implement `prompts/get` JSON-RPC method that returns the template text with
   `{{placeholders}}` filled from arguments.
3. Initial templates to ship (match CRG names for cross-tool familiarity):
   - `code_review(files)` — calls `blast`, then `context`, then summarises risk.
   - `architecture_overview()` — calls `communities`, then `hotspots`.
   - `debug_issue(query)` — calls `search`, then `context`.
   - `onboard_file(file)` — calls `context` + `neighbours`.
   - `pre_merge_check(files)` — calls `blast` + `savings`.
4. `CodeGraph` Python API gets a `run_prompt(name, **kwargs) -> str` method.

**Feature flag**: `CGMCP_FEATURE_PROMPTS`

---

## 6  Graph diff  (`detect_changes` command)

### What CRG does
Compares the graph at two commits (or two timestamps) and reports:
- New edges added (newly coupled files)
- Edges removed (decoupled files)
- Nodes whose degree changed significantly (emerging hubs)
- Community membership changes (refactoring signals)

### Why it matters for us
This is directly useful for code review: given a PR's changed files, show which
files have *become* more coupled since the last release and which coupling has
been intentionally broken.

### Implementation plan

**Dependencies**: none (uses existing `Store` + SQLite snapshots).

1. Extend `snapshot.py` to save a graph fingerprint (edge list + weights) at a
   named point (tag, commit SHA, or timestamp).
2. New `detect_changes(since: str) -> dict` function in `graph.py` that diffs
   the current edge set against the snapshot.
3. Return shape:
   ```json
   {
     "new_edges": [["a.py", "b.py", 0.8]],
     "removed_edges": [["c.py", "d.py"]],
     "emerging_hubs": ["payments/gateway.py"],
     "community_shifts": [{"file": "auth.py", "was": 3, "now": 1}]
   }
   ```
4. Expose as MCP command `detect_changes` and CLI `code-graph diff --since <ref>`.

**Feature flag**: `CGMCP_FEATURE_GRAPH_DIFF`

---

## 7  Knowledge gap analysis

### What CRG does
Identifies files that are heavily referenced (high in-degree in the structural
graph) but have no test coverage edge pointing at them.  These are the highest-risk
untested paths.

### Why it matters for us
This complements our existing `hotspots` command (which surfaces high-churn files)
with a *risk dimension*: a hotspot with no tests is much more dangerous than one
with good coverage.

### Implementation plan

**Dependencies**: none (uses the `tests` structural edges from Feature 1).

Prerequisite: Feature 1 (structural edges) must ship first.

1. New `knowledge_gaps(top_n: int = 20) -> list[dict]` function in `blast.py`.
2. Logic: for each file, compute `risk_score = co_change_degree × (1 − test_coverage)`.
   `test_coverage = 1` if any `tests` edge points at the file, else `0` (binary
   for now; fractional when we have per-function coverage data).
3. Return top-N sorted by `risk_score`, including `co_change_degree`,
   `has_tests`, and `last_changed` fields.
4. Expose as MCP command `knowledge_gaps` and `CodeGraph.knowledge_gaps()`.

**Feature flag**: `CGMCP_FEATURE_KNOWLEDGE_GAPS`

---

## 8  Risk-scored PR review

### What CRG does
Given a list of changed files (a PR diff), assigns a risk score to the PR by
combining blast radius size, edge confidence, community disruption, and test
coverage gaps.

### Why it matters for us
CI / PR automation can consume this as a structured signal.  A risk score ≥ 0.8
could trigger a reviewer assignment rule or a mandatory context summary comment.

### Implementation plan

**Dependencies**: none.

Prerequisite: Features 1 and 7 for best signal quality; works at reduced accuracy
with co-change only.

1. New `review_risk(changed_files: list[str]) -> dict` in `blast.py`.
2. Score components:
   - `blast_size`: normalised blast radius count.
   - `hub_disruption`: fraction of blast set that are hubs (degree > 2σ).
   - `test_gap`: fraction of blast set with no test coverage edge.
   - `community_span`: number of distinct communities touched.
3. Final score: weighted sum, clamped to [0, 1].
4. Return `{"risk": 0.74, "components": {...}, "top_risky_files": [...]}`.
5. Expose as MCP command `review_risk` and CLI `code-graph review-risk <file...>`.
6. GitHub Actions example in `docs/` showing how to call it in CI.

**Feature flag**: `CGMCP_FEATURE_REVIEW_RISK`

---

## 9  Bridge node detection

### What CRG does
Identifies nodes whose removal would disconnect large parts of the graph
(articulation points / high betweenness centrality).  These are architectural
choke points — changes to them have disproportionate blast radius.

### Why it matters for us
`hotspots` finds high-churn hubs.  Bridge nodes are a different concept: a file
can be low-churn but be the only path between two major clusters, making it
structurally critical.

### Implementation plan

**Dependencies**: `igraph>=0.11` (already pulled in by Feature 2 / Leiden).

1. New `bridge_nodes(top_n: int = 10) -> list[dict]` in a `topology.py` module.
2. Algorithm: compute betweenness centrality on the co-change graph; flag nodes
   that are articulation points (vertex connectivity contribution).
3. Return `{"file": "...", "betweenness": 0.91, "is_articulation_point": true}`.
4. Expose as MCP command `bridges` and `CodeGraph.bridges()`.
5. Surface in `architecture_overview` prompt template (Feature 5).

**Feature flag**: `CGMCP_FEATURE_BRIDGES`

---

## Dependency summary

| Feature | New dependency | Optional? |
|---|---|---|
| 1 – Structural edges | `tree-sitter` + grammar wheels (already approved) | No |
| 2 – Leiden communities | `igraph>=0.11` | No |
| 3a – Export formats | none | — |
| 3b – Web UI | `flask>=3.0` or `fastapi + uvicorn` | Yes (`pip install code-graph-mcp[ui]`) |
| 4 – Platform auto-config | none | — |
| 5 – Prompt templates | none | — |
| 6 – Graph diff | none | — |
| 7 – Knowledge gaps | none (needs Feature 1) | — |
| 8 – Risk-scored review | none (needs Feature 1) | — |
| 9 – Bridge nodes | `igraph` (already from Feature 2) | No |

Net new mandatory deps: **`igraph`**.  Tree-sitter is already approved.
The web UI is the only truly optional extra.

---

## Recommended sequencing

```
Feature 1 (structural edges)   ← unblocks 7, 8; depends on Phase 3 tree-sitter
Feature 2 (Leiden)             ← unblocks 9; igraph already pulled in
Feature 4 (platform install)   ← zero deps, high adoption leverage, ship early
Feature 5 (prompt templates)   ← zero deps, zero risk, ship alongside Phase 3
Feature 6 (graph diff)         ← zero deps, high PR-review value
Feature 3a (export)            ← zero deps, parallelisable with any phase
Feature 9 (bridge nodes)       ← depends on igraph (Feature 2)
Feature 7 (knowledge gaps)     ← depends on Feature 1
Feature 8 (review risk)        ← depends on Features 1 + 7
Feature 3b (web UI)            ← optional; ship last
```
