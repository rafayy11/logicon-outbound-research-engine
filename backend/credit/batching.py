"""Target-count-aware batch controller. `--target 50` means 50 FINAL
campaign-ready prospects, not 50 raw companies -- this stops the pipeline
from launching new batches once that's been reached (plus a small
configurable buffer), instead of grinding through every collected
company regardless of how many are already qualified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator, TypeVar

T = TypeVar("T")


def chunk(items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class BatchController:
    target_count: int
    buffer_pct: float = 15.0
    batch_size: int = 25

    @classmethod
    def from_env(cls, target_count: int) -> "BatchController":
        buffer_pct = float(os.environ.get("TARGET_BUFFER_PCT", 15))
        batch_size = int(os.environ.get("DEFAULT_BATCH_SIZE", 25))
        return cls(target_count=target_count, buffer_pct=buffer_pct, batch_size=batch_size)

    @property
    def max_pool(self) -> int:
        return int(self.target_count * (1 + self.buffer_pct / 100))

    def should_launch_next_batch(self, current_ready_count: int) -> bool:
        """Ready = Tier A/B, decision maker found, employment verified,
        usable email -- i.e. actually export-eligible, not just researched."""
        return current_ready_count < self.target_count

    def trim_to_pool(self, ready_items: list[T]) -> list[T]:
        if len(ready_items) <= self.max_pool:
            return ready_items
        return ready_items[: self.max_pool]
