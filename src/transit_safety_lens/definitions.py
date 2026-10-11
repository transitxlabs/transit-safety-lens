"""Conservative, cited comparability boundaries, never inferred from the response."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_timeline(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["changes"]


def regime(date: str, timeline: list[dict[str, Any]], metric: str = "total_events") -> str:
    boundaries = [
        str(row["effective_date"])
        for row in timeline
        if metric in row["break_metrics"] and str(row["effective_date"]) <= date[:10]
    ]
    return max(boundaries, default="2014-01-01")


def assert_comparable(dates: list[str], timeline: list[dict[str, Any]],
                      metric: str = "total_events") -> str:
    if not dates:
        raise ValueError("No dates to compare")
    regimes = {regime(date, timeline, metric) for date in dates}
    if len(regimes) != 1:
        raise ValueError("Comparison crosses a cited reporting-definition boundary")
    return regimes.pop()
