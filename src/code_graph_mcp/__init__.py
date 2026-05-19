"""code-graph-mcp: Git-based blast-radius analysis for token-efficient AI code reviews."""

__version__ = "0.1.0"

from .api import CodeGraph  # noqa: F401 — public API

__all__ = ["CodeGraph", "__version__"]
