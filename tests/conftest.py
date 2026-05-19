from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.build_repo import create_synthetic_repo


@pytest.fixture
def synthetic_repo_factory(tmp_path: Path):
    def _factory(*, commits: int = 200, files: int = 30, seed: int = 7) -> Path:
        repo = tmp_path / f"repo-{commits}-{files}-{seed}"
        return create_synthetic_repo(repo, commit_count=commits, file_count=files, seed=seed)

    return _factory


@pytest.fixture
def tiny_repo(synthetic_repo_factory):
    return synthetic_repo_factory(commits=50, files=20, seed=1)


@pytest.fixture
def medium_repo(synthetic_repo_factory):
    return synthetic_repo_factory(commits=2000, files=200, seed=2)


@pytest.fixture
def huge_repo(synthetic_repo_factory, request):
    if not request.config.getoption("--run-huge"):
        pytest.skip("huge fixture disabled; pass --run-huge")
    return synthetic_repo_factory(commits=50000, files=2000, seed=3)


def pytest_addoption(parser):
    parser.addoption(
        "--run-huge",
        action="store_true",
        default=False,
        help="run very large fixture tests",
    )
