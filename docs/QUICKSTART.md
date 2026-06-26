# Quick start

Get **pareto-context-graph** running on a repo and wired into your editor in a few minutes.
For architecture and token strategy, see the [README](../README.md).
For commands and parameters, see [COMMANDS.md](COMMANDS.md).

---

## Prerequisites

- Python **3.10+**
- **Git** CLI in `PATH`
- A git repository with commit history (the repo you want context for)

Optional extras: `pip install pareto-context-graph[tiktoken]` or `pip install -e ".[tiktoken]"` for accurate token counts (recommended; included in Docker image).

Clear stale session paths between tasks: `pareto-context-graph session clear` or MCP `session_clear`.

---

## Standard setup (most repos)

```bash
# 1. Install
pip install -e /path/to/pareto-context-graph

# 2. Build the graph (once per repo; ~30s for 5K commits)
cd /path/to/your-repo
pareto-context-graph build

# 3. Auto-configure your editor
pareto-context-graph install                    # VS Code / Copilot
pareto-context-graph install --platform cursor  # Cursor

# 4. Restart the editor — the AI calls pareto_context_graph on every prompt
```

Verify the graph:

```bash
pareto-context-graph stats
pareto-context-graph doctor
```

`doctor` prints graph health and a **cold-build time estimate** for your repo profile.

---

## Huge-repo bootstrap (T2/T3) — snapshot first

Cold builds take **~13 min** (kubernetes) or **~10+ hours** (linux). Use a pre-built snapshot instead.

**Full guide:** [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md)

### Kubernetes (weekly CI artifact)

```bash
git clone --filter=blob:none https://github.com/kubernetes/kubernetes.git
cd kubernetes

# 1. Download kubernetes-graph-snapshot from Actions → Bench T2 (Kubernetes)
export PCG_SNAPSHOT_KEY='<CI secret>'   # if signed

# 2. Bootstrap (import + incremental update)
pareto-context-graph build --from-snapshot ~/Downloads/kubernetes-graph-snapshot.tar.gz

# 3. Verify and serve
pareto-context-graph doctor
pareto-context-graph install --platform cursor
pareto-context-graph serve --watch --interval 600
```

### Linux (team export)

```bash
git clone --filter=blob:none https://github.com/torvalds/linux.git
cd linux
pareto-context-graph build --from-snapshot /path/to/linux-graph-snapshot.tar.gz
```

Export after a one-time build: `pareto-context-graph snapshot export ./linux-graph.tar.gz`

### Day-to-day

```bash
git pull
pareto-context-graph build   # incremental when HEAD moved
```

---

## Backup a built graph

Linux/kubernetes cold builds take hours — export before risky changes:

```bash
pareto-context-graph snapshot export bench/backups/linux-graph-$(date +%Y%m%d).tar.gz
```

Import on another machine:

```bash
pareto-context-graph snapshot import ./linux-graph-YYYYMMDD.tar.gz
```

Signing: [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md).

---

## Editor integration

![Editor, MCP servers, and repo](diagrams/editor-integration.svg)

Compression after tier 3: [CONTEXT_COMPRESSION.md](CONTEXT_COMPRESSION.md) (`compression: prune`, `retrieve`).

### VS Code / GitHub Copilot

After `pareto-context-graph install`:

```json
{
  "servers": {
    "pareto-context-graph": {
      "command": "pareto-context-graph",
      "args": ["serve", "--repo", "/path/to/your/repo"],
      "type": "stdio"
    }
  }
}
```

Copilot instructions (`.github/copilot-instructions.md`) tell the AI to:

1. Call `pareto_context_graph` with `command="context"` before answering
2. Start at tier 1; escalate only as needed
3. Pass `already_have` on follow-ups; use `session_memory: false` on new tasks

### Cursor / Claude Desktop

```json
{
  "mcpServers": {
    "pareto-context-graph": {
      "command": "pareto-context-graph",
      "args": ["serve", "--repo", "/path/to/your/repo"]
    }
  }
}
```

Or: `pareto-context-graph install --platform cursor`

---

## Docker

```bash
docker build -t pareto-context-graph:latest .
docker run --rm -i -v /path/to/repo:/workspace pareto-context-graph:latest
```

See `docker-compose.yml` in the repo root.

---

## Python API (no MCP)

```python
from pareto_context_graph.api import ParetoContextGraph

cg = ParetoContextGraph("/path/to/repo")
cg.build()
result = cg.context(files=["src/main.py"], query="add logging", tier=1)
```

---

## Next steps

| Goal | Doc |
|------|-----|
| Huge repo (k8s/linux) snapshot onboarding | [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md) |
| `context` parameters and all commands | [COMMANDS.md](COMMANDS.md) |
| Understand `context` tiers and parameters | [README](../README.md) |
| Architecture + C4 diagrams | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Run golden eval / regression gate | [tests/eval/README.md](../tests/eval/README.md) |
| OSS benchmark clones | [BENCHMARK_REPOS.md](BENCHMARK_REPOS.md) |
| Feedback + `learn` loop | [FEEDBACK.md](FEEDBACK.md) |

```bash
make eval REPOS=fastapi=bench/fastapi
make eval-check REPOS=fastapi=bench/fastapi
```
