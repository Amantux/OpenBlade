"""The assistant's read-only tool registry.

Every tool here answers a question. None of them changes anything: there is no
subprocess call, no hardware command, no ``session.commit()``, and no write method
reachable through the proxies in :mod:`openblade.assistant.readonly`.

Adding a tool is a deliberate act — :func:`build_registry` refuses to build unless
the tool's name appears in :data:`READ_ONLY_TOOL_NAMES`, so a tool added without
amending the allowlist fails closed instead of quietly extending the assistant's
reach.
"""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openblade.assistant.config import AssistantConfig, default_docs_dir
from openblade.assistant.errors import ToolNotFoundError, ToolRegistryViolationError
from openblade.assistant.readonly import ReadOnlyProxy, read_only_catalog, read_only_library

# ---------------------------------------------------------------------------
# The allowlist. This is the fail-closed guard, not documentation.
# ---------------------------------------------------------------------------
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_inventory",
        "list_volume_groups",
        "get_volume_group",
        "list_jobs",
        "get_job",
        "catalog_search",
        "get_config_summary",
        "search_docs",
    }
)

_MAX_RESULTS = 50
_SNIPPET_CHARS = 700
# Upper bound on rows pulled out of SQL for one catalog_search. Glob patterns are
# applied in Python to this bounded set, never to the whole catalog.
_SCAN_LIMIT = 500


JSONDict = dict[str, Any]
ToolHandler = Callable[["ToolContext", Mapping[str, Any]], JSONDict]


def _no_refresh() -> None:
    """Default when the caller has no session to expire (tests, plain objects)."""


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool is allowed to see.

    ``catalog`` and ``library`` are :class:`ReadOnlyProxy` instances, so a tool
    body physically cannot reach a mutating repository method.

    Nothing here holds a credential: the database URL arrives already redacted and
    the Scalar password is reduced to a boolean by :func:`build_context`. ``config``
    does carry the Ollama API key, so it is excluded from ``repr`` — a traceback
    with locals must not print it.
    """

    config: AssistantConfig = field(repr=False)
    catalog: ReadOnlyProxy
    library: ReadOnlyProxy
    backend: str
    real_hardware_enabled: bool
    database_summary: str
    refresh: Callable[[], None] = _no_refresh
    scalar_url_set: bool = False
    scalar_password_set: bool = False
    hardware_dry_run: bool = False
    docs_dir: Path = field(default_factory=default_docs_dir)


@dataclass(frozen=True)
class ReadOnlyTool:
    """A single callable exposed to the model."""

    name: str
    description: str
    parameters: JSONDict
    handler: ToolHandler

    def schema(self) -> JSONDict:
        """Ollama / OpenAI-style function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Name -> :class:`ReadOnlyTool`, validated against the allowlist."""

    def __init__(self, tools: Iterable[ReadOnlyTool]) -> None:
        by_name: dict[str, ReadOnlyTool] = {}
        for tool in tools:
            if tool.name not in READ_ONLY_TOOL_NAMES:
                raise ToolRegistryViolationError(
                    f"Tool {tool.name!r} is not on the assistant read-only allowlist. "
                    "The assistant must never expose a mutating or hardware-moving "
                    "operation; add the name to READ_ONLY_TOOL_NAMES only after "
                    "confirming the tool performs no writes."
                )
            if tool.name in by_name:
                raise ToolRegistryViolationError(f"Duplicate tool {tool.name!r}")
            by_name[tool.name] = tool
        self._tools = by_name

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get(self, name: str) -> ReadOnlyTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"Unknown tool {name!r}") from None

    def schemas(self) -> list[JSONDict]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def call(self, name: str, context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
        return self.get(name).handler(context, arguments)


# ---------------------------------------------------------------------------
# Argument helpers (the model supplies these, so nothing is trusted)
# ---------------------------------------------------------------------------


def _str_arg(arguments: Mapping[str, Any], key: str, default: str = "") -> str:
    value = arguments.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _int_arg(arguments: Mapping[str, Any], key: str, default: int, maximum: int) -> int:
    raw = arguments.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, maximum)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _get_inventory(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    inventory = context.library.inventory()
    slots = [
        {
            "slot": slot.slot_id,
            "occupied": slot.occupied,
            "barcode": str(slot.barcode) if slot.barcode is not None else None,
        }
        for slot in inventory.slots
    ]
    drives = [
        {
            "drive": drive.drive_id,
            "loaded": drive.barcode is not None,
            "barcode": str(drive.barcode) if drive.barcode is not None else None,
            "driveState": drive.drive_state.value,
            "mountState": drive.mount_state.value,
        }
        for drive in inventory.drives
    ]
    return {
        "libraryId": inventory.library_id,
        "changerState": inventory.changer_state.value,
        "slotCount": len(slots),
        "driveCount": len(drives),
        "occupiedSlots": sum(1 for slot in slots if slot["occupied"]),
        "slots": slots,
        "drives": drives,
    }


def _volume_group_summary(group: Any) -> JSONDict:
    cartridges = list(group.cartridges)
    capacity = sum(int(cartridge.capacity_bytes) for cartridge in cartridges)
    used = sum(int(cartridge.used_bytes) for cartridge in cartridges)
    return {
        "id": group.id,
        "name": group.name,
        "tapeCount": len(cartridges),
        "barcodes": list(group.barcodes),
        "capacityBytes": capacity,
        "usedBytes": used,
        "freeBytes": max(capacity - used, 0),
        "usedPercent": round(used / capacity * 100, 2) if capacity else 0.0,
    }


def _list_volume_groups(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    groups = context.catalog.list_volume_groups()
    return {
        "count": len(groups),
        "volumeGroups": [_volume_group_summary(group) for group in groups],
    }


def _get_volume_group(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    name = _str_arg(arguments, "name")
    if not name:
        return {"error": "name is required"}
    group = context.catalog.get_volume_group(name)
    if group is None:
        known = [existing.name for existing in context.catalog.list_volume_groups()]
        return {"found": False, "name": name, "knownVolumeGroups": known}
    summary = _volume_group_summary(group)
    summary["found"] = True
    summary["tapes"] = [
        {
            "barcode": cartridge.barcode,
            "state": cartridge.state,
            "formatted": bool(cartridge.formatted),
            "capacityBytes": int(cartridge.capacity_bytes),
            "usedBytes": int(cartridge.used_bytes),
        }
        for cartridge in sorted(group.cartridges, key=lambda item: str(item.barcode))
    ]
    return summary


def _job_summary(job: Any, *, include_metadata: bool) -> JSONDict:
    summary: JSONDict = {
        "id": job.id,
        "type": job.job_type,
        "state": job.state,
        "error": job.error,
        "createdAt": _iso(job.created_at),
        "updatedAt": _iso(job.updated_at),
    }
    if include_metadata:
        try:
            summary["metadata"] = job.metadata_dict
        except (ValueError, TypeError):
            summary["metadata"] = {}
    return summary


def _list_jobs(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    state = _str_arg(arguments, "state") or None
    limit = _int_arg(arguments, "limit", 20, _MAX_RESULTS)
    jobs = context.catalog.list_jobs(state)
    ordered = sorted(jobs, key=lambda job: job.created_at, reverse=True)
    return {
        "count": len(ordered),
        "returned": min(len(ordered), limit),
        "filterState": state,
        "jobs": [_job_summary(job, include_metadata=False) for job in ordered[:limit]],
    }


def _get_job(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    job_id = _str_arg(arguments, "job_id")
    if not job_id:
        return {"error": "job_id is required"}
    job = context.catalog.get_job(job_id)
    if job is None:
        return {"found": False, "jobId": job_id}
    summary = _job_summary(job, include_metadata=True)
    summary["found"] = True
    return summary


_GLOB_CHARACTERS = "*?["


def _is_glob(pattern: str) -> bool:
    return any(character in pattern for character in _GLOB_CHARACTERS)


def _literal_prefix(pattern: str) -> str:
    """The leading glob-free run of a pattern, usable as a SQL ``ilike`` filter.

    ``/photos/*/*.raw`` -> ``/photos/``. Narrowing in SQL is what keeps
    :func:`_catalog_search` from loading a real archive's whole catalog into
    memory; the glob itself is then applied to the bounded candidate set.
    """
    for index, character in enumerate(pattern):
        if character in _GLOB_CHARACTERS:
            return pattern[:index]
    return pattern


def _matches(path: str, pattern: str) -> bool:
    if _is_glob(pattern):
        # ``*`` deliberately crosses ``/`` (fnmatch semantics), so ``/photos/*.raw``
        # finds nested files too. ``**`` therefore behaves the same as ``*``.
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.lower(), pattern.lower())
    return pattern.lower() in path.lower()


def _catalog_search(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    pattern = _str_arg(arguments, "pattern")
    if not pattern:
        return {"error": "pattern is required"}
    limit = _int_arg(arguments, "limit", 20, _MAX_RESULTS)

    # Narrow in SQL first. The model chooses this pattern and may call the tool
    # once per round, so an unbounded full-catalog scan is a self-inflicted DoS.
    sql_filter = _literal_prefix(pattern) if _is_glob(pattern) else pattern
    candidates, total = context.catalog.list_catalog_files(
        limit=_SCAN_LIMIT, offset=0, search=sql_filter or None
    )
    matched = [record for record in candidates if _matches(record.path, pattern)]
    matched.sort(key=lambda record: record.path)

    # One lookup for every volume-group name, rather than a lazy load per record.
    group_names = {group.id: group.name for group in context.catalog.list_volume_groups()}

    results: list[JSONDict] = []
    for record in matched[:limit]:
        instances = [
            {
                "instanceId": instance.id,
                "barcode": instance.barcode,
                "tapePath": instance.tape_path,
                "state": instance.state,
                "checksumVerified": bool(instance.checksum_verified),
                "archivedAt": _iso(instance.archived_at),
            }
            for instance in record.instances
        ]
        results.append(
            {
                "fileId": record.id,
                "path": record.path,
                "sizeBytes": int(record.size_bytes),
                "checksumSha256": record.checksum_sha256,
                "volumeGroup": group_names.get(record.volume_group_id),
                "shardCount": record.shard_count,
                "tapes": sorted({instance["barcode"] for instance in instances}),
                "instances": instances,
            }
        )
    return {
        "pattern": pattern,
        "matchCount": len(matched),
        "returned": len(results),
        # Tell the model when it is looking at a truncated view, so it says so
        # rather than reporting "only 500 files match".
        "scanTruncated": total > len(candidates),
        "files": results,
    }


def _redact_db_url(db_url: str) -> str:
    """Report the database backend without ever echoing credentials.

    SQLite URLs carry only a path today, but ``OPENBLADE_DB_URL`` is operator
    supplied and a Postgres/MySQL DSN embeds a password. Report the scheme and, for
    local files, the filename only.
    """
    parts = urlsplit(db_url)
    scheme = parts.scheme or "unknown"
    if scheme.startswith("sqlite"):
        name = Path(parts.path).name or "<memory>"
        return f"{scheme}:.../{name}"
    return f"{scheme}://<redacted>"


def _get_config_summary(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    inventory = context.library.inventory()
    real_operations_permitted = context.backend == "real" and context.real_hardware_enabled
    return {
        "backend": context.backend,
        "simulator": context.backend != "real",
        "database": context.database_summary,
        "driveCount": len(inventory.drives),
        "slotCount": len(inventory.slots),
        "safetyGates": {
            "realHardwareGate": {
                "backendIsReal": context.backend == "real",
                "realHardwareEnabled": context.real_hardware_enabled,
                "realOperationsPermitted": real_operations_permitted,
                "requires": [
                    "OPENBLADE_BACKEND=real",
                    "OPENBLADE_REAL_HARDWARE_ENABLED=true",
                ],
            },
            "hardwareDryRun": context.hardware_dry_run,
            "formatConfirmation": (
                "Format requires a dry run, the expected barcode, and a one-time "
                "safety token bound to that barcode."
            ),
            "mountStateUnloadGate": "Unload is rejected unless LTFS state is 'unmounted'.",
            "sourceRetentionGate": "Source deletion is never implicit.",
        },
        "scalarEndpointConfigured": context.scalar_url_set,
        "scalarCredentialSet": context.scalar_password_set,
        "assistant": {
            "model": context.config.model,
            "maxToolRounds": context.config.max_rounds,
            "readOnly": True,
        },
    }


def _iter_doc_files(docs_dir: Path) -> list[Path]:
    if not docs_dir.is_dir():
        return []
    return sorted(path for path in docs_dir.rglob("*.md") if path.is_file())


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split Markdown into ``(heading, body)`` pairs, preamble first.

    Fence-aware: a ``#`` inside a ``` or ~~~ block is a shell comment, not a
    heading. Without this the OpenBlade docs shred — ``docs/sharding.md`` alone has
    bash comments that split one procedure into four fragments and attach it to
    headings that do not exist, in the one tool whose job is accurate quoting.
    """
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    fence = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            buffer.append(line)
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            buffer.append(line)
            continue
        if line.startswith("#"):
            if heading or any(item.strip() for item in buffer):
                sections.append((heading, "\n".join(buffer).strip()))
            heading = line.lstrip("#").strip()
            buffer = []
        else:
            buffer.append(line)
    if heading or any(item.strip() for item in buffer):
        sections.append((heading, "\n".join(buffer).strip()))
    return sections


def _score(terms: Sequence[str], heading: str, body: str) -> int:
    heading_lower = heading.lower()
    body_lower = body.lower()
    score = 0
    for term in terms:
        if term in heading_lower:
            score += 10
        score += min(body_lower.count(term), 5)
    return score


def _search_docs(context: ToolContext, arguments: Mapping[str, Any]) -> JSONDict:
    query = _str_arg(arguments, "query")
    if not query:
        return {"error": "query is required"}
    limit = _int_arg(arguments, "limit", 5, 20)
    docs_dir = context.docs_dir
    terms = [term for term in query.lower().split() if term]
    hits: list[tuple[int, str, str, str]] = []
    for path in _iter_doc_files(docs_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(docs_dir).as_posix()
        path_bonus = 3 * sum(1 for term in terms if term in relative.lower())
        for heading, body in _split_sections(text):
            score = _score(terms, heading, body) + path_bonus
            if score <= path_bonus:
                continue
            hits.append((score, relative, heading, body))
    hits.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))
    return {
        "query": query,
        # Deliberately not the absolute path: it is usually /home/<operator>/...
        # and with a cloud endpoint it would leave the machine for no benefit.
        "docsRoot": "docs/",
        "matchCount": len(hits),
        "sections": [
            {
                "doc": relative,
                "heading": heading or "(top of document)",
                "score": score,
                "excerpt": body[:_SNIPPET_CHARS],
                "truncated": len(body) > _SNIPPET_CHARS,
            }
            for score, relative, heading, body in hits[:limit]
        ],
    }


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------

_NO_ARGS: JSONDict = {"type": "object", "properties": {}}


def _tool_definitions() -> list[ReadOnlyTool]:
    return [
        ReadOnlyTool(
            name="get_inventory",
            description=(
                "Current library inventory: every slot with its barcode and occupancy, "
                "every drive with its loaded barcode, drive state and LTFS mount state, "
                "plus the changer state."
            ),
            parameters=_NO_ARGS,
            handler=_get_inventory,
        ),
        ReadOnlyTool(
            name="list_volume_groups",
            description=(
                "All volume groups (pools) with tape count, member barcodes, and "
                "aggregate capacity/used/free bytes."
            ),
            parameters=_NO_ARGS,
            handler=_list_volume_groups,
        ),
        ReadOnlyTool(
            name="get_volume_group",
            description=(
                "One volume group by name, including each member tape's barcode, state, "
                "format status and capacity usage."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Volume group name."}},
                "required": ["name"],
            },
            handler=_get_volume_group,
        ),
        ReadOnlyTool(
            name="list_jobs",
            description="Recent archive/restore/format jobs, newest first, optionally by state.",
            parameters={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Filter by state, e.g. pending, running, completed, failed.",
                    },
                    "limit": {"type": "integer", "description": "Max jobs to return (default 20)."},
                },
            },
            handler=_list_jobs,
        ),
        ReadOnlyTool(
            name="get_job",
            description="One job by id, including its metadata and error text if it failed.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "Job id."}},
                "required": ["job_id"],
            },
            handler=_get_job,
        ),
        ReadOnlyTool(
            name="catalog_search",
            description=(
                "Find archived files by path substring or glob pattern and report which "
                "tape barcode and instance each copy lives on."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Path substring, or a glob such as /photos/**/*.raw",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max files to return (default 20).",
                    },
                },
                "required": ["pattern"],
            },
            handler=_catalog_search,
        ),
        ReadOnlyTool(
            name="get_config_summary",
            description=(
                "Backend mode (simulator or real), drive and slot counts, and the state of "
                "each safety gate. Credentials are never included."
            ),
            parameters=_NO_ARGS,
            handler=_get_config_summary,
        ),
        ReadOnlyTool(
            name="search_docs",
            description=(
                "Search the OpenBlade documentation tree (docs/, including the wiki and "
                "runbooks) and return the best-matching sections verbatim."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search words."},
                    "limit": {
                        "type": "integer",
                        "description": "Max sections to return (default 5).",
                    },
                },
                "required": ["query"],
            },
            handler=_search_docs,
        ),
    ]


def build_registry(extra: Sequence[ReadOnlyTool] = ()) -> ToolRegistry:
    """Build the tool registry.

    ``extra`` exists so tests can prove the allowlist guard fires; production code
    passes nothing.
    """
    return ToolRegistry([*_tool_definitions(), *extra])


def _session_refresher(catalog: object) -> Callable[[], None]:
    """Return a callable that expires the catalog session's identity map.

    The CLI holds one long-lived SQLAlchemy ``Session`` built with
    ``expire_on_commit=False``. In a REPL that session pins whatever it read first,
    so without this the assistant reports a job as "pending" long after another
    process finished it — the exact failure the "look it up, don't guess" prompt
    rule is meant to prevent. ``expire_all()`` writes nothing; it only discards
    cached rows so the next SELECT hits the database.
    """
    session = getattr(catalog, "session", None)
    expire_all = getattr(session, "expire_all", None)
    if not callable(expire_all):
        return _no_refresh

    def refresh() -> None:
        expire_all()

    return refresh


def build_context(
    *,
    config: AssistantConfig,
    catalog: object,
    library: object,
    backend: str,
    real_hardware_enabled: bool,
    db_url: str,
    scalar_url: str | None = None,
    scalar_password: str = "",
    hardware_dry_run: bool = False,
) -> ToolContext:
    """Wrap live objects in read-only proxies and drop every secret at the boundary.

    Credentials are reduced to booleans and the DSN to a scheme *here*, before the
    context exists — so no tool, log line or traceback can surface one even by
    accident. This is the redaction site; :func:`_get_config_summary` only reports
    what it is handed.
    """
    return ToolContext(
        config=config,
        catalog=read_only_catalog(catalog),
        library=read_only_library(library),
        backend=backend,
        real_hardware_enabled=real_hardware_enabled,
        database_summary=_redact_db_url(db_url),
        refresh=_session_refresher(catalog),
        scalar_url_set=bool(scalar_url),
        scalar_password_set=bool(scalar_password),
        hardware_dry_run=hardware_dry_run,
        docs_dir=config.docs_dir or default_docs_dir(),
    )


def render_result(result: JSONDict) -> str:
    """Serialize a tool result for the model."""
    return json.dumps(result, default=str, ensure_ascii=False)
