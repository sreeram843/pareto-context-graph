# Feedback Learning

The server logs returned context files to the `feedback` table.
Clients can mark files as actually used via MCP `mark_used`.

## Learn weights

Run:

```bash
code-graph-mcp learn
```

This writes `.code-graph/weights.json`. During `context` ranking,
files with higher learned relevance get a positive score boost.
