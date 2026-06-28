# Diagrams

Hand-authored **SVG** assets for the README and docs. They use a shared palette
(amber = person, blue = call surfaces, green = engine, purple = datastore, gray =
external) on a light card so they read on both light and dark GitHub themes.

These are plain SVG — edit the file directly, no toolchain or build step. Embedded
images always render (Markdown previews don't execute Mermaid).

## Files

| File | Shows | Used in |
|------|-------|---------|
| `c4-context.svg` | System context — 3 call surfaces → engine → git + graph | ARCHITECTURE |
| `c4-container.svg` | Containers — surfaces drive select → rank → pack | ARCHITECTURE |
| `build-query.svg` | Build-time vs query-time split | ARCHITECTURE |
| `context-pipeline.svg` | A `context` request, phase by phase | ARCHITECTURE |
| `before-after.svg` | Value prop — grep & guess vs ranked context | README |
| `token-layers.svg` | The five layers that reduce tokens | README |
