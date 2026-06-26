<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->

# Code Graph MCP — Context Instructions

## IMPORTANT: Always scope your context before acting

Before answering any question or performing any task in this repository,
call the `pareto_context_graph` MCP tool to get relevant context.

### How to use:

1. Identify which file(s) the user is asking about or working in.
2. Call `pareto_context_graph` with command="context" and those file paths:
   ```json
   {"command": "context", "files": ["path/to/file.rb"], "query": "user's question", "tier": 1}
   ```
3. Tier 1 (default) gives summaries. If you need more detail on specific files,
   call again with tier=2 (signatures) or tier=3 (code chunks).
4. On follow-up prompts, pass `already_have` with files you already read:
   ```json
   {"command": "context", "files": [...], "already_have": ["file1.rb", "file2.rb"]}
   ```
5. Start a **new task** with a fresh session — call `session_clear` or
   `pareto-context-graph session clear` so stale paths are not auto-merged:
   ```json
   {"command": "session_clear"}
   ```
6. Do NOT scan, grep, or read other files unless the tool's results are insufficient.

### Why:
This repository has a co-change graph built from git history plus import/keyword analysis.
The tool identifies the files most likely to matter — ones that provably changed together,
share imports, or follow naming conventions — so you read the right files, not random ones.
Less noise = fewer hallucinations = more accurate answers.

### Tiers:
- tier=1: File paths + 1-line summaries (cheapest, use for orientation — ~30 tokens/file)
- tier=2: Function/class signatures (use when you need API shape)
- tier=3: Relevant code chunks (use when you need implementation details)

### Delta context (multi-turn):
On follow-up prompts, always pass `already_have` with files already in the conversation.
This skips redundant re-reads and keeps each turn focused on new context only.
For a **new user task**, call `session_clear` first (or CLI: `pareto-context-graph session clear`).

### Token budgets:
Install the tiktoken extra for honest budgets: `pip install -e '.[tiktoken]'`.
Use `diagnostics: true` on context for per-candidate score breakdown.
Every `context` response includes `suggested_next` (tier escalation / compression hints).

### Other commands:
- `search` — Find files by name/path (e.g. `{"command": "search", "query": "patient"}`)
- `neighbours` — Co-change neighbours for a file (e.g. `{"command": "neighbours", "path": "app/models/patient.rb"}`)
- `blast` — Files affected by current git diff
- `stats` — File/edge counts for the graph
- `hotspots` — Most co-changed files (top N)
- `communities` — Detected file clusters (architectural modules)
