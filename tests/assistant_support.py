"""Shared helpers for the assistant tests.

Two things every assistant test needs: a seeded simulator + catalog, and an Ollama
that is not really there. The transport below is an ``httpx.MockTransport``, so the
production client code path (headers, JSON encoding, status handling) runs for real
while zero bytes leave the process.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from openblade.assistant.config import AssistantConfig

VOLUME_GROUP = "photo-archive"
BARCODES = ("PH000001", "PH000002")
ARCHIVED_PATH = "/photos/2019/wedding.raw"
SECOND_PATH = "/photos/2020/landscape.raw"


def assistant_config(docs_dir: Path | None = None, **overrides: Any) -> AssistantConfig:
    """An enabled config pointed at a URL nothing will ever dial."""
    base = {
        "base_url": "http://ollama.test:11434",
        "model": "llama3.2",
        "timeout_seconds": 5.0,
        "max_rounds": 6,
        "docs_dir": docs_dir,
    }
    base.update(overrides)
    return AssistantConfig(**base)  # type: ignore[arg-type]


def seed_assistant_state(context: Any) -> dict[str, Any]:
    """Seed one volume group, two tapes, two archived files and two jobs.

    Returns the ids the tests assert on.
    """
    catalog = context.catalog
    group = catalog.create_volume_group(VOLUME_GROUP)
    for barcode in BARCODES:
        cartridge = catalog.add_cartridge(barcode, group.id)
        cartridge.capacity_bytes = 12_000_000_000
        cartridge.used_bytes = 3_000_000_000
        cartridge.formatted = True
    catalog.session.commit()

    first = catalog.create_file_record(ARCHIVED_PATH, 4096, "a" * 64, group.id)
    instance = catalog.create_file_instance(first.id, BARCODES[0], "/data/wedding.raw")
    catalog.mark_instance_archived(instance.id)

    second = catalog.create_file_record(SECOND_PATH, 8192, "b" * 64, group.id)
    catalog.create_file_instance(second.id, BARCODES[1], "/data/landscape.raw")

    done = catalog.create_job("archive", {"path": ARCHIVED_PATH})
    catalog.update_job_state(done.id, "completed")
    failed = catalog.create_job("restore", {"path": SECOND_PATH})
    catalog.update_job_state(failed.id, "failed", "drive 0 reported a write error")

    return {
        "group_id": group.id,
        "file_id": first.id,
        "instance_id": instance.id,
        "completed_job_id": done.id,
        "failed_job_id": failed.id,
    }


def tool_call_response(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """An Ollama /api/chat body in which the model asks for one tool."""
    return {
        "model": "llama3.2",
        "done": True,
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments or {}}}],
        },
    }


def prose_response(content: str) -> dict[str, Any]:
    return {
        "model": "llama3.2",
        "done": True,
        "message": {"role": "assistant", "content": content},
    }


class ScriptedOllama:
    """Replays a fixed list of response bodies and records what was sent."""

    def __init__(self, responses: Sequence[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat", request.url
        self.headers.append(request.headers)
        import json

        self.requests.append(json.loads(request.content.decode()))
        if not self._responses:
            raise AssertionError("ScriptedOllama ran out of scripted responses")
        return httpx.Response(200, json=self._responses.pop(0))

    @property
    def exhausted(self) -> bool:
        return not self._responses


def scripted_client(responses: Sequence[dict[str, Any]]) -> tuple[httpx.Client, ScriptedOllama]:
    script = ScriptedOllama(responses)
    return httpx.Client(transport=httpx.MockTransport(script)), script


def failing_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def media_facade_for(context: Any, *, drive_serials: Any = (("OBLADE_D02", 1),)) -> Any:
    """A tier-2 media facade over a live app context, built the way the CLI builds it.

    The serial map is populated by default so the previews under test carry the
    drive serial — the detail an operator standing at the rack can actually check.
    """
    from openblade.assistant.media_facade import media_bundle, media_facade
    from openblade.assistant.readonly import read_only_catalog, read_only_inventory

    return media_facade(
        media_bundle(
            catalog=read_only_catalog(context.catalog),
            inventory=read_only_inventory(context.inventory_service),
            catalog_repo=context.catalog,
            library=context.library,
            ltfs=context.ltfs,
            format_service=context.format_service,
            archive_service=context.archive_service,
            restore_service=context.restore_service,
            drive_serials=drive_serials,
        )
    )
