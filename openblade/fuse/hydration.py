"""Hydration queue: request that an offline file be brought online."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HydrationRequest:
    catalog_path: str
    priority: int = 0


class HydrationQueue:
    def __init__(self) -> None:
        self._queue: list[HydrationRequest] = []

    def enqueue(self, request: HydrationRequest) -> None:
        self._queue.append(request)

    def pending(self) -> list[HydrationRequest]:
        return list(self._queue)

    def clear(self) -> None:
        self._queue.clear()
