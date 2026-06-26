"""Graph health diagnostics and pre-build estimates."""

from __future__ import annotations

from typing import Any

from .build_estimate import gather_doctor_report

__all__ = ["gather_doctor_report", "format_doctor_text"]


def format_doctor_text(report: dict[str, Any]) -> str:
    lines = [
        f"Graph Doctor for {report.get('repo', '(unknown)')}",
        "-" * 60,
        f"Files:           {report.get('files', 0)}",
        f"Edges:           {report.get('edges', 0)}",
        f"P95 degree:      {report.get('p95_degree', 0)}",
        f"Build strategy:  {report.get('build_strategy') or 'unknown'}",
        f"Build since:     {report.get('last_build_since') or '(none)'}",
    ]
    age = report.get("graph_age_hours")
    if age is None:
        lines.append("Graph age:       unknown")
    else:
        lines.append(f"Graph age:       {age}h")

    estimate = report.get("build_estimate") or {}
    plan = report.get("build_plan") or {}
    if estimate:
        lines.append("")
        lines.append("Build estimate (cold rebuild):")
        profile = plan.get("profile") or estimate.get("profile") or "auto"
        lines.append(f"  Profile:         {profile}")
        lines.append(
            f"  Commits window:  {estimate.get('commits_in_window', '?')} "
            f"(cap {estimate.get('commits_cap', '?')})"
        )
        if estimate.get("since"):
            lines.append(f"  Since:           {estimate['since']}")
        lines.append(f"  Shards:          {estimate.get('shards', plan.get('shards', 1))}")
        lines.append(f"  Source files:    {estimate.get('tracked_source_files', '?')}")
        lines.append(
            f"  Est. build:      {estimate.get('build_range_human', estimate.get('build_human', '?'))}"
        )
        lines.append(
            f"  Est. graph.db:   {estimate.get('graph_db_range_human', estimate.get('graph_db_human', '?'))}"
        )
        lines.append(f"  Confidence:      {estimate.get('confidence', 'unknown')}")
        if estimate.get("last_build_human"):
            lines.append(f"  Last build:      {estimate['last_build_human']} (measured)")

    lines.append("Top hubs:")
    for hub in report.get("top_hubs") or []:
        lines.append(f"  - {hub['path']}: {hub['degree']}")
    return "\n".join(lines)
