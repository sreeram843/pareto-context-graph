# Diagrams

Rendered **SVG/PNG** assets for the README and docs. Cursor and many local Markdown
previews do **not** execute Mermaid in fenced blocks — embedded images always show.

## Regenerate

```bash
./scripts/render_diagrams.sh
# or
make render-diagrams
```

Requires [Node.js](https://nodejs.org/) (`npx @mermaid-js/mermaid-cli`).

## Files

| Source (`.mmd`) | Output | Used in |
|-----------------|--------|---------|
| `c4-context.mmd` | C4 **System Context** | README |
| `c4-container.mmd` | C4 **Containers** | README |
| `c4-component-context.mmd` | C4 **Components** (`context` path) | README |
| `north-star.mmd` | Four measurable layers | README |
| `before-after.mmd` | With vs without tool | README |
| `token-layers.mmd` | Five savings layers | README |
| `problem-flow.mmd` | File selection decision | README |
| `build-query.mmd` | Build vs query split | README, ARCHITECTURE |
| `context-sequence.mmd` | `context` request sequence | README |
| `tier-escalation.mmd` | Tier 1 → 2 → 3 | README |
| `follow-up-delta.mmd` | `already_have` / session | README |
| `editor-integration.mmd` | IDE + MCP | README |

Edit the `.mmd` file, re-run the render script, commit both `.mmd` and `.svg`.

## C4 model

[C4](https://c4model.com/) diagrams use Mermaid's built-in `C4Context`, `C4Container`,
and `C4Component` syntax. See [c4-context.mmd](c4-context.mmd) for the template.
