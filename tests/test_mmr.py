"""Test MMR diversity selection (Task 7)."""
from code_graph_mcp.server import _mmr_select


def test_mmr_limits_near_duplicate_specs():
    """MMR selects all diverse files before picking additional near-duplicate specs.

    With equal relevance, MMR prefers files that are dissimilar to already-selected
    items. Diverse non-spec files should all appear in the top-K selection even
    when they're outnumbered by near-duplicate spec files.
    """
    candidates = []
    # 10 spec files in same directory — high path and symbol similarity to each other
    for i in range(10):
        candidates.append({
            "path": f"spec/models/user_spec_{i}.rb",
            "weight": 100,
            "_relevance": 100.0,
            "_symbols": ["describe", "it", "expect", "User"],
        })
    # 3 diverse non-spec files with equal relevance
    diverse = [
        {"path": "app/models/user.rb", "weight": 100, "_relevance": 100.0,
         "_symbols": ["User", "has_many", "validates"]},
        {"path": "app/controllers/users_controller.rb", "weight": 100, "_relevance": 100.0,
         "_symbols": ["UsersController", "index", "show"]},
        {"path": "db/migrate/create_users.rb", "weight": 100, "_relevance": 100.0,
         "_symbols": ["CreateUsers", "change"]},
    ]
    candidates.extend(diverse)

    selected = _mmr_select(candidates, limit=8, mmr_lambda=0.7)

    # All 3 diverse files must appear: MMR penalizes the near-duplicate specs
    # enough that diverse files (lower similarity to already-selected items) win.
    selected_paths = {s["path"] for s in selected}
    for d in diverse:
        assert d["path"] in selected_paths, (
            f"Diverse file {d['path']} should be selected by MMR. "
            f"Selected: {sorted(selected_paths)}"
        )

    # Fewer than all 10 specs should appear (MMR deduplicates)
    spec_count = sum(1 for s in selected if "spec" in s["path"])
    assert spec_count < 10, f"MMR should deduplicate near-duplicate specs, got {spec_count}/10"
