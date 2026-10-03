"""
Non-blocking file stability tracking.

The old watcher slept inside the watchdog callback (blocking every other
event). Here each scan just records (size, mtime) per file; a file is
"stable" once it has been seen unchanged for ``settle_seconds``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _Seen:
    size: int
    mtime: float
    unchanged_since: float
    first_seen: float


class StabilityTracker:

    def __init__(self, settle_seconds: float, clock=time.monotonic):
        self.settle_seconds = settle_seconds
        self.clock = clock
        self._seen: dict[Path, _Seen] = {}

    def observe(self, path: Path) -> bool:
        """Record current state; return True if the file is stable."""
        now = self.clock()
        try:
            st = path.stat()
        except FileNotFoundError:
            self._seen.pop(path, None)
            return False

        prev = self._seen.get(path)
        if prev is None or prev.size != st.st_size or prev.mtime != st.st_mtime:
            self._seen[path] = _Seen(
                st.st_size,
                st.st_mtime,
                now,
                prev.first_seen if prev else now,
            )
            return False

        return st.st_size > 0 and (now - prev.unchanged_since) >= self.settle_seconds

    def first_seen(self, path: Path) -> float | None:
        s = self._seen.get(path)
        return s.first_seen if s else None

    def forget(self, paths) -> None:
        for p in paths:
            self._seen.pop(p, None)

    def prune(self, existing: set[Path]) -> None:
        for p in list(self._seen):
            if p not in existing:
                del self._seen[p]
