"""Clay credit/action budget tracking.

Important: Clay's Public API exposes no endpoint to read the workspace's
actual account credit balance (confirmed against the published OpenAPI
spec -- there is no /credits or /usage path). "actual_usage" here is a
local counter of Clay operations this run issued (one unit per routine-
run item, one per search-result row fetched); "remaining budget" is
computed against an optional operator-supplied ceiling
(CLAY_MAX_BUDGET), not against Clay's real balance. Never presented as
an authoritative account balance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class CreditBudget:
    max_budget: int | None = None
    actual_usage: int = 0
    usage_by_stage: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "CreditBudget":
        raw = os.environ.get("CLAY_MAX_BUDGET")
        return cls(max_budget=int(raw) if raw else None)

    def record(self, stage: str, units: int = 1) -> None:
        self.actual_usage += units
        self.usage_by_stage[stage] = self.usage_by_stage.get(stage, 0) + units

    @property
    def remaining(self) -> int | None:
        if self.max_budget is None:
            return None
        return self.max_budget - self.actual_usage

    def has_budget_for(self, estimated_additional: int) -> bool:
        if self.max_budget is None:
            return True
        return self.remaining is not None and self.remaining >= estimated_additional

    def top_stages(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self.usage_by_stage.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def report(self) -> dict:
        return {
            "estimated_usage": self.actual_usage,  # MVP: no separate pre-estimate model yet
            "actual_usage": self.actual_usage,
            "max_budget": self.max_budget,
            "remaining_budget": self.remaining,
            "usage_by_stage": dict(self.usage_by_stage),
        }
