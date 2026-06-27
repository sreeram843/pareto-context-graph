"""Multi-repo resolution for `serve --repo-map` (#20)."""

from __future__ import annotations

from pathlib import Path


class RepoRegistry:
    """Map repo keys to absolute repo roots."""

    def __init__(self, repos: dict[str, Path], *, default_key: str = "default") -> None:
        if not repos:
            raise ValueError("repo registry requires at least one repo")
        self.repos = {key: Path(path).resolve() for key, path in repos.items()}
        self.default_key = default_key if default_key in self.repos else next(iter(self.repos))

    def resolve(self, repo_key: str | None = None) -> Path:
        key = (repo_key or self.default_key).strip()
        if key not in self.repos:
            known = ", ".join(sorted(self.repos))
            raise KeyError(f"unknown repo_key {key!r}; known: {known}")
        return self.repos[key]

    def keys(self) -> list[str]:
        return sorted(self.repos)


def parse_repo_map(entries: list[str]) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for raw in entries:
        if "=" not in raw:
            raise ValueError(f"invalid --repo-map entry (expected KEY=PATH): {raw}")
        key, path = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid --repo-map entry (empty key): {raw}")
        repos[key] = Path(path.strip()).resolve()
    return repos


def build_repo_registry(primary: Path, repo_map: list[str] | None = None) -> RepoRegistry:
    repos = {"default": Path(primary).resolve()}
    repos.update(parse_repo_map(repo_map or []))
    return RepoRegistry(repos)
