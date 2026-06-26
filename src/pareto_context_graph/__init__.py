"""pareto-context-graph: Pareto-ranked, token-budgeted context for AI coding assistants."""

__version__ = "0.1.0"

from .api import ParetoContextGraph  # noqa: F401 — public API

__all__ = ["ParetoContextGraph", "__version__"]
