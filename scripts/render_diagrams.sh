#!/usr/bin/env bash
# Render docs/diagrams/*.mmd to SVG (and PNG) for README embedding.
# Requires: npx @mermaid-js/mermaid-cli  (or: npm i -g @mermaid-js/mermaid-cli)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIAG="$ROOT/docs/diagrams"
MMDC="${MMDC:-npx --yes @mermaid-js/mermaid-cli}"

if ! command -v npx >/dev/null 2>&1; then
  echo "error: npx not found" >&2
  exit 1
fi

for src in "$DIAG"/*.mmd; do
  base="$(basename "$src" .mmd)"
  echo "render $base"
  $MMDC -i "$src" -o "$DIAG/$base.svg" -b transparent
  $MMDC -i "$src" -o "$DIAG/$base.png" -b white -w 1200 2>/dev/null || true
done

echo "done — SVGs in $DIAG"
