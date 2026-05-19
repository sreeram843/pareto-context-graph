# CI Snapshots

Use CI to pre-build and publish `.code-graph` snapshots for large repos.

## Suggested workflow

1. Nightly on default branch, run:
   - `code-graph-mcp build --profile huge`
   - `code-graph-mcp snapshot export ./graph-snapshot.tar.gz`
2. Upload `graph-snapshot.tar.gz` to your artifact bucket.
3. Developers bootstrap locally:
   - `code-graph-mcp build --from-snapshot <url-or-path>`

This avoids rebuilding large histories from scratch on every machine.
