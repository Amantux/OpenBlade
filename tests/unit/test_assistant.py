"""Assistant behaviour: config, provider, tools, and the bounded loop.

Zero network: every test drives the real client through ``httpx.MockTransport``.
The read-only safety guarantees are tested separately in
``tests/safety/test_assistant_read_only.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from openblade.assistant import create_session
from openblade.assistant.config import DISABLED_MESSAGE, load_assistant_config
from openblade.assistant.errors import (
    AssistantDisabledError,
    AssistantLoopLimitError,
    AssistantUpstreamError,
    SetupRefusedError,
    SetupRegistryViolationError,
)
from openblade.assistant.provider import OllamaClient, ToolCall
from openblade.assistant.session import MAX_CALLS_PER_ROUND, AssistantSession
from openblade.assistant.setup_facade import setup_facade
from openblade.assistant.setup_tools import PendingAction, build_setup_registry, log_action
from openblade.assistant.tools import READ_ONLY_TOOL_NAMES, build_context, build_registry
from tests.assistant_support import (
    ARCHIVED_PATH,
    BARCODES,
    VOLUME_GROUP,
    ScriptedOllama,
    assistant_config,
    failing_client,
    prose_response,
    scripted_client,
    seed_assistant_state,
    tool_call_response,
)

_OLLAMA_ENV = (
    "OPENBLADE_OLLAMA_URL",
    "OPENBLADE_OLLAMA_MODEL",
    "OPENBLADE_OLLAMA_API_KEY",
    "OPENBLADE_OLLAMA_TIMEOUT",
    "OPENBLADE_ASSISTANT_MAX_ROUNDS",
    "OPENBLADE_DOCS_DIR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _OLLAMA_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def seeded(app_context: Any) -> dict[str, Any]:
    return seed_assistant_state(app_context)


def _context(app_context: Any, docs_dir: Path | None = None) -> Any:
    config = assistant_config(docs_dir=docs_dir)
    return build_context(
        config=config,
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend=app_context.config.backend.value,
        real_hardware_enabled=app_context.config.real_hardware_enabled,
        db_url=app_context.config.db_url,
        scalar_url="https://scalar.internal",
        scalar_password="hunter2",
    )


def _session(app_context: Any, responses: list[dict[str, Any]], docs_dir: Path | None = None):
    client, script = scripted_client(responses)
    config = assistant_config(docs_dir=docs_dir)
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context, docs_dir),
        config=config,
    )
    return session, script


# ---------------------------------------------------------------------------
# Configuration / disabled path
# ---------------------------------------------------------------------------


def test_assistant_is_disabled_without_a_url(clean_env: None) -> None:
    config = load_assistant_config()
    assert config.enabled is False
    assert config.base_url is None
    assert config.model == "llama3.2"


def test_blank_url_counts_as_unset(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_OLLAMA_URL", "   ")
    assert load_assistant_config().enabled is False


def test_create_session_raises_curated_disabled_message(clean_env: None, app_context: Any) -> None:
    with pytest.raises(AssistantDisabledError) as excinfo:
        create_session(app_context)
    message = str(excinfo.value)
    assert message == DISABLED_MESSAGE
    assert "OPENBLADE_OLLAMA_URL" in message
    assert "ollama.com" in message


def test_config_reads_url_model_and_key(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_OLLAMA_URL", "https://ollama.com/")
    monkeypatch.setenv("OPENBLADE_OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("OPENBLADE_OLLAMA_API_KEY", "secret-key")
    config = load_assistant_config()
    assert config.enabled is True
    assert config.base_url == "https://ollama.com"  # trailing slash trimmed
    assert config.model == "qwen3:8b"
    assert config.api_key == "secret-key"


def test_api_key_is_sent_as_bearer(app_context: Any) -> None:
    client, script = scripted_client([prose_response("hello")])
    config = assistant_config(api_key="secret-key")
    OllamaClient(config, client=client).chat([{"role": "user", "content": "hi"}])
    assert script.headers[0]["Authorization"] == "Bearer secret-key"


def test_no_authorization_header_without_a_key(app_context: Any) -> None:
    client, script = scripted_client([prose_response("hello")])
    OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "hi"}])
    assert "Authorization" not in script.headers[0]


# ---------------------------------------------------------------------------
# Upstream errors are curated, never raw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "OPENBLADE_OLLAMA_API_KEY"),
        (404, "ollama pull"),
        (429, "rate limiting"),
        (500, "server error"),
        (418, "HTTP 418"),
    ],
)
def test_http_errors_are_curated(status: int, expected: str) -> None:
    client = failing_client(lambda request: httpx.Response(status, text="raw upstream detail"))
    with pytest.raises(AssistantUpstreamError) as excinfo:
        OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "x"}])
    message = str(excinfo.value)
    assert expected in message
    assert "raw upstream detail" not in message


def test_connect_failure_is_curated_and_hides_socket_text() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused to 10.1.2.3:11434")

    client = failing_client(boom)
    with pytest.raises(AssistantUpstreamError) as excinfo:
        OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "x"}])
    message = str(excinfo.value)
    assert "OPENBLADE_OLLAMA_URL" in message
    assert "Errno 111" not in message
    assert "10.1.2.3" not in message


def test_timeout_is_curated() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AssistantUpstreamError, match="did not respond in time"):
        OllamaClient(assistant_config(), client=failing_client(slow)).chat(
            [{"role": "user", "content": "x"}]
        )


def test_non_json_body_is_curated() -> None:
    client = failing_client(lambda request: httpx.Response(200, text="<html>proxy login</html>"))
    with pytest.raises(AssistantUpstreamError) as excinfo:
        OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "x"}])
    assert "not JSON" in str(excinfo.value)
    assert "proxy login" not in str(excinfo.value)


def test_error_field_in_body_is_curated() -> None:
    client = failing_client(
        lambda request: httpx.Response(200, json={"error": "model 'x' not found, try pulling it"})
    )
    with pytest.raises(AssistantUpstreamError) as excinfo:
        OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "x"}])
    assert "try pulling it" not in str(excinfo.value)


def test_missing_message_is_curated() -> None:
    client = failing_client(lambda request: httpx.Response(200, json={"done": True}))
    with pytest.raises(AssistantUpstreamError, match="no assistant message"):
        OllamaClient(assistant_config(), client=client).chat([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# The tool loop
# ---------------------------------------------------------------------------


def test_tool_loop_round_trip_is_grounded(app_context: Any, seeded: dict[str, Any]) -> None:
    """Model asks for get_inventory, gets real state, then answers."""
    session, script = _session(
        app_context,
        [
            tool_call_response("get_inventory"),
            prose_response("The library has drives and slots as listed above."),
        ],
    )
    turn = session.ask("what is in the library right now?")

    assert turn.tool_calls == ("get_inventory",)
    assert turn.rounds == 2
    assert script.exhausted

    # The second request carried the real tool output back to the model.
    second_request = script.requests[1]
    tool_messages = [m for m in second_request["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    inventory = app_context.inventory_service.snapshot()
    assert payload["slotCount"] == len(inventory.slots)
    assert payload["driveCount"] == len(inventory.drives)
    # Tool schemas were advertised on every call.
    assert {tool["function"]["name"] for tool in second_request["tools"]} == session.registry.names


def test_tool_observer_sees_each_call(app_context: Any, seeded: dict[str, Any]) -> None:
    session, _ = _session(
        app_context,
        [
            tool_call_response("get_volume_group", {"name": VOLUME_GROUP}),
            prose_response("done"),
        ],
    )
    seen: list[tuple[str, dict[str, Any]]] = []
    session.ask("tell me about the pool", on_tool=lambda name, args: seen.append((name, args)))
    assert seen == [("get_volume_group", {"name": VOLUME_GROUP})]


def test_string_encoded_arguments_are_accepted(app_context: Any, seeded: dict[str, Any]) -> None:
    """Small models often emit arguments as a JSON string rather than an object."""
    client, script = scripted_client(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_volume_group",
                                "arguments": json.dumps({"name": VOLUME_GROUP}),
                            }
                        }
                    ],
                }
            },
            prose_response("ok"),
        ]
    )
    config = assistant_config()
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
    )
    session.ask("pool?")
    payload = json.loads(
        [m for m in script.requests[1]["messages"] if m["role"] == "tool"][0]["content"]
    )
    assert payload["found"] is True
    assert payload["name"] == VOLUME_GROUP


def test_unknown_tool_is_reported_to_the_model_not_raised(app_context: Any) -> None:
    session, script = _session(
        app_context,
        [tool_call_response("format_tape", {"barcode": "PH000001"}), prose_response("I cannot.")],
    )
    turn = session.ask("format PH000001")
    assert turn.reply == "I cannot."
    payload = json.loads(
        [m for m in script.requests[1]["messages"] if m["role"] == "tool"][0]["content"]
    )
    assert "no tool named" in payload["error"]
    assert "read-only" in payload["error"]


def test_loop_is_bounded(app_context: Any) -> None:
    config = assistant_config(max_rounds=3)
    client, _ = scripted_client([tool_call_response("get_inventory") for _ in range(3)])
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
    )
    with pytest.raises(AssistantLoopLimitError, match="past 3 rounds"):
        session.ask("loop forever")


def test_reset_keeps_only_the_system_prompt(app_context: Any) -> None:
    session, _ = _session(app_context, [prose_response("hi")])
    session.ask("hello")
    assert len(session.messages) > 1
    session.reset()
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "system"


# ---------------------------------------------------------------------------
# Individual tools against seeded state
# ---------------------------------------------------------------------------


def test_get_inventory_reports_slots_drives_and_mount_state(app_context: Any) -> None:
    registry = build_registry()
    result = registry.call("get_inventory", _context(app_context), {})
    inventory = app_context.inventory_service.snapshot()
    assert result["slotCount"] == len(inventory.slots)
    assert result["driveCount"] == len(inventory.drives)
    assert result["changerState"] == inventory.changer_state.value
    drive = result["drives"][0]
    assert set(drive) == {"drive", "loaded", "barcode", "driveState", "mountState"}
    assert drive["loaded"] == (drive["barcode"] is not None)


def test_list_volume_groups_aggregates_capacity(app_context: Any, seeded: dict[str, Any]) -> None:
    result = build_registry().call("list_volume_groups", _context(app_context), {})
    group = next(item for item in result["volumeGroups"] if item["name"] == VOLUME_GROUP)
    assert group["tapeCount"] == 2
    assert sorted(group["barcodes"]) == sorted(BARCODES)
    assert group["capacityBytes"] == 24_000_000_000
    assert group["usedBytes"] == 6_000_000_000
    assert group["freeBytes"] == 18_000_000_000
    assert group["usedPercent"] == 25.0


def test_get_volume_group_lists_tapes(app_context: Any, seeded: dict[str, Any]) -> None:
    result = build_registry().call(
        "get_volume_group", _context(app_context), {"name": VOLUME_GROUP}
    )
    assert result["found"] is True
    assert [tape["barcode"] for tape in result["tapes"]] == sorted(BARCODES)
    assert all(tape["formatted"] for tape in result["tapes"])


def test_get_volume_group_missing_lists_known_names(
    app_context: Any, seeded: dict[str, Any]
) -> None:
    result = build_registry().call("get_volume_group", _context(app_context), {"name": "nope"})
    assert result["found"] is False
    assert VOLUME_GROUP in result["knownVolumeGroups"]


def test_list_jobs_and_get_job(app_context: Any, seeded: dict[str, Any]) -> None:
    registry = build_registry()
    context = _context(app_context)

    failed = registry.call("list_jobs", context, {"state": "failed"})
    assert failed["filterState"] == "failed"
    assert [job["id"] for job in failed["jobs"]] == [seeded["failed_job_id"]]

    detail = registry.call("get_job", context, {"job_id": seeded["failed_job_id"]})
    assert detail["found"] is True
    assert detail["type"] == "restore"
    assert detail["error"] == "drive 0 reported a write error"
    assert detail["metadata"]["path"]

    missing = registry.call("get_job", context, {"job_id": "does-not-exist"})
    assert missing["found"] is False


def test_catalog_search_resolves_file_to_tape(app_context: Any, seeded: dict[str, Any]) -> None:
    result = build_registry().call("catalog_search", _context(app_context), {"pattern": "wedding"})
    assert result["matchCount"] == 1
    found = result["files"][0]
    assert found["path"] == ARCHIVED_PATH
    assert found["tapes"] == [BARCODES[0]]
    assert found["volumeGroup"] == VOLUME_GROUP
    assert found["instances"][0]["state"] == "archived"
    assert found["instances"][0]["checksumVerified"] is True


def test_catalog_search_supports_globs(app_context: Any, seeded: dict[str, Any]) -> None:
    result = build_registry().call(
        "catalog_search", _context(app_context), {"pattern": "/photos/*/*.raw"}
    )
    assert result["matchCount"] == 2


def test_catalog_search_requires_a_pattern(app_context: Any) -> None:
    result = build_registry().call("catalog_search", _context(app_context), {})
    assert "error" in result


def test_config_summary_reports_gates_and_redacts_credentials(app_context: Any) -> None:
    result = build_registry().call("get_config_summary", _context(app_context), {})
    gates = result["safetyGates"]["realHardwareGate"]
    assert gates["realOperationsPermitted"] is False
    assert "OPENBLADE_REAL_HARDWARE_ENABLED=true" in gates["requires"]
    assert result["simulator"] is True
    assert result["driveCount"] == len(app_context.inventory_service.snapshot().drives)

    # The Scalar password was supplied to build_context and must not survive.
    assert result["scalarCredentialSet"] is True
    flattened = json.dumps(result)
    assert "hunter2" not in flattened
    assert "scalar.internal" not in flattened
    assert app_context.config.db_url not in flattened


@pytest.mark.parametrize(
    ("db_url", "expected_absent"),
    [
        ("postgresql://admin:s3cret@db.internal:5432/openblade", "s3cret"),
        ("mysql+pymysql://root:toor@10.0.0.5/openblade", "toor"),
    ],
)
def test_config_summary_redacts_dsn_passwords(
    app_context: Any, db_url: str, expected_absent: str
) -> None:
    config = assistant_config()
    context = build_context(
        config=config,
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url=db_url,
    )
    result = build_registry().call("get_config_summary", context, {})
    assert expected_absent not in json.dumps(result)
    assert result["database"].endswith("<redacted>")


def test_search_docs_finds_the_safety_gates_section(app_context: Any) -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    result = build_registry().call(
        # Verbatim from docs/safety.md — the growing corpus (campaign runbook,
        # wiki) displaced it from the top results for generic queries.
        "search_docs",
        _context(app_context, docs),
        {"query": "binds a barcode to a one-time SafetyToken"},
    )
    assert result["matchCount"] > 0
    docs_hit = [section for section in result["sections"] if section["doc"] == "safety.md"]
    assert docs_hit, [section["doc"] for section in result["sections"]]
    assert any("safetytoken" in section["excerpt"].lower() for section in docs_hit)


def test_search_docs_walks_nested_directories(app_context: Any, tmp_path: Path) -> None:
    """The wiki is written by another workstream; search must find whatever lands."""
    nested = tmp_path / "wiki" / "guides"
    nested.mkdir(parents=True)
    (nested / "pools.md").write_text(
        "# Creating a pool\n\nA volume group groups tapes for one purpose.\n",
        encoding="utf-8",
    )
    result = build_registry().call(
        "search_docs", _context(app_context, tmp_path), {"query": "creating a pool"}
    )
    assert result["sections"][0]["doc"] == "wiki/guides/pools.md"
    assert result["sections"][0]["heading"] == "Creating a pool"


def test_search_docs_with_no_docs_tree_is_empty_not_an_error(
    app_context: Any, tmp_path: Path
) -> None:
    result = build_registry().call(
        "search_docs", _context(app_context, tmp_path / "absent"), {"query": "anything"}
    )
    assert result["matchCount"] == 0
    assert result["sections"] == []


# ---------------------------------------------------------------------------
# Regressions found by adversarial review
# ---------------------------------------------------------------------------


def test_malformed_url_is_curated_not_a_traceback() -> None:
    """httpx.InvalidURL is NOT an httpx.HTTPError, so it used to escape raw."""
    config = assistant_config(base_url="localhost:11434")  # no scheme
    with pytest.raises(AssistantUpstreamError, match="needs a scheme"):
        OllamaClient(config, client=httpx.Client()).chat([{"role": "user", "content": "x"}])


def test_failed_turn_leaves_the_transcript_clean(app_context: Any) -> None:
    """A turn that dies mid-round must not leave dangling tool_calls in history.

    Otherwise the operator's next question is appended onto a malformed
    conversation, with only /reset to fix it.
    """
    config = assistant_config(max_rounds=2)
    client, _ = scripted_client([tool_call_response("get_inventory") for _ in range(2)])
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
    )
    before = list(session.messages)
    with pytest.raises(AssistantLoopLimitError):
        session.ask("loop")
    assert session.messages == before
    assert [message["role"] for message in session.messages] == ["system"]


def test_tool_calls_are_capped_per_round(app_context: Any) -> None:
    """Rounds are bounded; the work inside one round must be too."""
    flood = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_inventory", "arguments": {}}} for _ in range(200)
            ],
        }
    }
    client, script = scripted_client([flood, prose_response("done")])
    config = assistant_config()
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
    )
    turn = session.ask("flood me")
    assert len(turn.tool_calls) == MAX_CALLS_PER_ROUND
    tool_messages = [m for m in script.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == MAX_CALLS_PER_ROUND


def test_build_context_wires_refresh_to_the_session(app_context: Any) -> None:
    """``refresh`` must be the session's ``expire_all``, not a no-op."""
    calls: list[int] = []
    app_context.catalog.session.expire_all = lambda: calls.append(1)  # type: ignore[method-assign]
    context = _context(app_context)
    context.refresh()
    assert calls == [1]


def test_build_context_refresh_is_a_no_op_without_a_session() -> None:
    """A plain object (or a future non-SQLAlchemy catalog) must not break."""

    class Plain:
        def list_volume_groups(self) -> list[Any]:
            return []

    context = build_context(
        config=assistant_config(),
        catalog=Plain(),
        inventory_service=Plain(),
        backend="mock",
        real_hardware_enabled=False,
        db_url="sqlite:///x.db",
    )
    context.refresh()  # must not raise


def test_ask_refreshes_before_reading(app_context: Any) -> None:
    """Each turn drops cached rows first.

    The CLI holds one long-lived Session built with ``expire_on_commit=False``; in a
    REPL its identity map can pin rows another process has since changed, and the
    prompt tells the model to trust what it reads.
    """
    calls: list[int] = []
    config = assistant_config()
    client, _ = scripted_client([prose_response("hi")])
    context = replace(_context(app_context), refresh=lambda: calls.append(1))
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=context,
        config=config,
    )
    session.ask("anything")
    assert calls == [1]


def test_search_docs_ignores_hashes_inside_fenced_code(app_context: Any, tmp_path: Path) -> None:
    """A `#` in a bash block is a comment, not a heading.

    Unfenced, docs/sharding.md alone shredded one procedure into four fragments
    attributed to headings that do not exist.
    """
    (tmp_path / "guide.md").write_text(
        "# Real heading\n\nBody text about scheduling.\n\n"
        "```bash\n# Acquire 3 drives simultaneously\nopenblade jobs\n```\n\nMore body.\n",
        encoding="utf-8",
    )
    result = build_registry().call(
        "search_docs", _context(app_context, tmp_path), {"query": "scheduling drives"}
    )
    headings = [section["heading"] for section in result["sections"]]
    assert headings == ["Real heading"]
    assert "Acquire 3 drives simultaneously" in result["sections"][0]["excerpt"]


def test_search_docs_does_not_leak_the_absolute_docs_path(app_context: Any, tmp_path: Path) -> None:
    """With a cloud endpoint, /home/<operator>/... would leave the machine."""
    (tmp_path / "a.md").write_text("# T\n\nbody\n", encoding="utf-8")
    result = build_registry().call(
        "search_docs", _context(app_context, tmp_path), {"query": "body"}
    )
    assert result["docsRoot"] == "docs/"
    assert str(tmp_path) not in json.dumps(result)


def test_catalog_search_is_bounded_in_sql(app_context: Any, seeded: dict[str, Any]) -> None:
    """The model picks the pattern, so an unbounded full-catalog scan is a DoS.

    The search must reach SQL, not load every FileRecord and filter in Python.
    """
    calls: list[dict[str, Any]] = []
    real = app_context.catalog.list_catalog_files

    def spy(limit: int = 50, offset: int = 0, search: str | None = None) -> Any:
        calls.append({"limit": limit, "offset": offset, "search": search})
        return real(limit=limit, offset=offset, search=search)

    app_context.catalog.list_catalog_files = spy  # type: ignore[method-assign]
    result = build_registry().call(
        "catalog_search", _context(app_context), {"pattern": "/photos/*/*.raw"}
    )
    assert calls, "catalog_search must narrow in SQL"
    assert calls[0]["limit"] <= 500
    assert calls[0]["search"] == "/photos/", "the glob's literal prefix filters in SQL"
    assert result["matchCount"] == 2
    assert result["scanTruncated"] is False


def test_tool_context_repr_hides_the_api_key(app_context: Any) -> None:
    """A traceback with locals must not print the Ollama key or a DSN."""
    config = assistant_config(api_key="sk-super-secret")
    context = build_context(
        config=config,
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url="postgresql://admin:s3cret@db.internal/openblade",
        scalar_password="hunter2",
    )
    rendered = repr(context)
    assert "sk-super-secret" not in rendered
    assert "s3cret" not in rendered
    assert "hunter2" not in rendered


# ---------------------------------------------------------------------------
# Tier-1 setup actions: confirm-gated execution
# ---------------------------------------------------------------------------


class Confirmer:
    """A scripted operator. Records every action it was asked about."""

    def __init__(self, answers: list[bool] | bool = True) -> None:
        self._answers = answers if isinstance(answers, list) else None
        self._default = answers if isinstance(answers, bool) else True
        self.asked: list[PendingAction] = []

    def __call__(self, action: PendingAction) -> bool:
        self.asked.append(action)
        if self._answers is None:
            return self._default
        if not self._answers:
            return False
        return self._answers.pop(0)

    @property
    def previews(self) -> list[str]:
        return [action.preview for action in self.asked]


def _setup_session(
    app_context: Any,
    responses: list[dict[str, Any]],
    confirm: Any,
) -> tuple[AssistantSession, ScriptedOllama]:
    """A REPL-shaped session: read tools plus the confirm-gated tier-1 tools."""
    client, script = scripted_client(responses)
    config = assistant_config()
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
        setup_registry=build_setup_registry(),
        setup=setup_facade(app_context.catalog),
        confirm=confirm,
    )
    return session, script


def _tool_messages(session: AssistantSession) -> list[dict[str, Any]]:
    return [message for message in session.messages if message.get("role") == "tool"]


def test_tier_one_tool_is_offered_only_when_a_confirmation_is_possible(app_context: Any) -> None:
    session, _ = _setup_session(app_context, [prose_response("hi")], Confirmer())
    assert session.setup_enabled is True
    names = {schema["function"]["name"] for schema in session._schemas()}
    assert {"create_volume_group", "add_tapes_to_volume_group"} <= names


def test_nothing_runs_without_an_explicit_yes(app_context: Any) -> None:
    """THE GATE. Mutation: execute on arrival instead of asking -> this fails.

    The model asks for a volume group; the operator says no; the catalog must be
    untouched and the model must be told, in a result it can act on.
    """
    confirm = Confirmer(False)
    session, script = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Understood, I won't create it."),
        ],
        confirm,
    )
    session.ask("make me a pool called photos")

    assert app_context.catalog.get_volume_group("photos") is None
    assert confirm.asked, "the operator must be asked"
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload == {
        "executed": False,
        "status": "declined_by_operator",
        "action": "create_volume_group",
        "preview": "Create volume group 'photos' (your first pool).",
        "reason": "the operator answered no",
        "repeatedProposal": False,
        "guidance": payload["guidance"],
    }
    assert "Do not propose this same action again" in payload["guidance"]
    # And the model saw it: the decline is in the transcript sent upstream.
    assert "declined_by_operator" in json.dumps(script.requests[-1])


def test_a_yes_creates_the_volume_group(app_context: Any) -> None:
    confirm = Confirmer(True)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Done — 'photos' exists and has no tapes yet."),
        ],
        confirm,
    )
    turn = session.ask("make me a pool called photos")

    group = app_context.catalog.get_volume_group("photos")
    assert group is not None
    assert turn.executed_actions == ("create_volume_group",)
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["executed"] is True
    assert payload["result"]["created"] is True
    assert payload["result"]["name"] == "photos"


def test_the_whole_pool_setup_flow(app_context: Any, seeded: dict[str, Any]) -> None:
    """Create a pool, then add two known tapes to it — both confirmed, in one turn."""
    app_context.catalog.add_cartridge("SP000001")
    app_context.catalog.add_cartridge("SP000002")
    confirm = Confirmer(True)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "scratch"}),
            tool_call_response(
                "add_tapes_to_volume_group",
                {"name": "scratch", "barcodes": ["SP000001", "sp000002"]},
            ),
            prose_response("Done — 'scratch' now has 2 tapes."),
        ],
        confirm,
    )
    turn = session.ask("set up a scratch pool with SP000001 and SP000002")

    group = app_context.catalog.get_volume_group("scratch")
    assert group is not None
    assert sorted(group.barcodes) == ["SP000001", "SP000002"]
    assert turn.executed_actions == ("create_volume_group", "add_tapes_to_volume_group")
    # The preview names the objects, because that sentence is what the operator
    # actually answers on.
    assert confirm.previews == [
        "Create volume group 'scratch' (you have 1 already).",
        "Add tape(s) SP000001, SP000002 to volume group 'scratch'.",
    ]
    # The result is fed back, so the model can confirm in prose and keep going.
    result = json.loads(_tool_messages(session)[1]["content"])["result"]
    assert result["added"] == ["SP000001", "SP000002"]
    assert result["tapeCount"] == 2
    assert turn.reply == "Done — 'scratch' now has 2 tapes."


def test_an_identical_declined_action_is_not_put_to_the_operator_twice(app_context: Any) -> None:
    """The re-proposal cap: one refusal is an answer, two is nagging."""
    confirm = Confirmer(False)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("All right — what would you like to call it instead?"),
        ],
        confirm,
    )
    session.ask("make me a pool called photos")

    assert len(confirm.asked) == 1, "the operator must not be asked the same thing twice"
    second = json.loads(_tool_messages(session)[1]["content"])
    assert second["status"] == "declined_by_operator"
    assert second["repeatedProposal"] is True
    assert app_context.catalog.get_volume_group("photos") is None


def test_a_different_action_is_still_put_to_the_operator(app_context: Any) -> None:
    """The cap is per action, not a session-wide gag order."""
    confirm = Confirmer([False, True])
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            tool_call_response("create_volume_group", {"name": "pictures"}),
            prose_response("Created 'pictures'."),
        ],
        confirm,
    )
    session.ask("make me a pool")
    assert len(confirm.asked) == 2
    assert app_context.catalog.get_volume_group("pictures") is not None
    assert app_context.catalog.get_volume_group("photos") is None


def test_one_shot_mode_is_not_offered_the_setup_tools(app_context: Any) -> None:
    """No confirmation callback, no tier 1 — not even the schema."""
    session, script = _session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Here is the command to run."),
        ],
    )
    assert session.setup_enabled is False
    session.ask("make me a pool called photos")

    offered = {schema["function"]["name"] for schema in script.requests[0].get("tools", [])}
    assert "create_volume_group" not in offered
    assert offered == set(READ_ONLY_TOOL_NAMES)
    # And if the model calls it anyway, it is simply not a tool.
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert "There is no tool named 'create_volume_group'" in payload["error"]
    assert app_context.catalog.get_volume_group("photos") is None


def test_one_shot_prompt_points_at_the_repl(app_context: Any) -> None:
    session, _ = _session(app_context, [prose_response("ok")])
    assert "only in the interactive REPL" in session.messages[0]["content"]


def test_a_half_wired_session_is_read_only_not_unconfirmed(app_context: Any) -> None:
    """Fail-closed: a registry without a way to ask must not execute anything."""
    client, _script = scripted_client([prose_response("ok")])
    config = assistant_config()
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
        setup_registry=build_setup_registry(),
        setup=setup_facade(app_context.catalog),
        confirm=None,
    )
    assert session.setup_enabled is False
    assert {schema["function"]["name"] for schema in session._schemas()} == set(
        READ_ONLY_TOOL_NAMES
    )


# ---------------------------------------------------------------------------
# Ambiguity refuses, confirmed or not
# ---------------------------------------------------------------------------


def test_an_unknown_barcode_is_refused_with_candidates(
    app_context: Any, seeded: dict[str, Any]
) -> None:
    """The operator is never even asked: nothing can name what it would act on."""
    confirm = Confirmer(True)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response(
                "add_tapes_to_volume_group",
                {"name": VOLUME_GROUP, "barcodes": ["PH009999"]},
            ),
            prose_response("I could not find that tape."),
        ],
        confirm,
    )
    session.ask("add PH009999 to the photo pool")

    assert confirm.asked == []
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["executed"] is False
    assert payload["status"] == "refused"
    assert payload["code"] == "unknown_barcode"
    assert payload["confirmedByOperator"] is False
    # The tapes that actually share the typo'd barcode's prefix come first, then
    # tapes in no pool at all — a short list the operator can settle in one glance.
    assert payload["candidates"][: len(BARCODES)] == list(BARCODES)
    assert 0 < len(payload["candidates"]) <= 5
    assert "do not pick one for them" in payload["guidance"]


def test_a_tape_already_in_another_pool_is_refused(
    app_context: Any, seeded: dict[str, Any]
) -> None:
    app_context.catalog.create_volume_group("second")
    confirm = Confirmer(True)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response(
                "add_tapes_to_volume_group",
                {"name": "second", "barcodes": [BARCODES[0]]},
            ),
            prose_response("That tape is spoken for."),
        ],
        confirm,
    )
    session.ask(f"add {BARCODES[0]} to second")

    assert confirm.asked == []
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["code"] == "tape_in_other_volume_group"
    assert VOLUME_GROUP in payload["error"]
    group = app_context.catalog.get_volume_group("second")
    assert group is not None and group.barcodes == []


def test_an_existing_volume_group_name_is_refused(app_context: Any, seeded: dict[str, Any]) -> None:
    confirm = Confirmer(True)
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": VOLUME_GROUP}),
            prose_response("You already have that one."),
        ],
        confirm,
    )
    session.ask(f"create {VOLUME_GROUP}")
    assert confirm.asked == []
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["code"] == "volume_group_exists"


def test_confirmation_is_not_a_licence_to_guess(app_context: Any, seeded: dict[str, Any]) -> None:
    """A yes carried to the write path still refuses an unresolvable target.

    Reached by executing a PendingAction directly — the shape of a confirmation that
    was minted when the catalog looked different. The facade re-validates inside the
    write path, so the yes buys nothing.
    """
    facade = setup_facade(app_context.catalog)
    stale = PendingAction(
        tool="add_tapes_to_volume_group",
        arguments={"name": VOLUME_GROUP, "barcodes": ["PH009999"]},
        preview="Add tape(s) PH009999 to volume group 'photo-archive'.",
    )
    with pytest.raises(SetupRefusedError) as excinfo:
        build_setup_registry().perform(stale, facade)
    assert excinfo.value.code == "unknown_barcode"
    group = app_context.catalog.get_volume_group(VOLUME_GROUP)
    assert group is not None and sorted(group.barcodes) == sorted(BARCODES)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"name": "", "barcodes": ["PH000001"]}, "missing_name"),
        ({"name": "x" * 100, "barcodes": ["PH000001"]}, "invalid_name"),
        ({"name": "photos\nrm -rf", "barcodes": ["PH000001"]}, "invalid_name"),
        ({"name": "photos", "barcodes": []}, "missing_barcodes"),
        ({"name": "photos", "barcodes": ["../etc/passwd"]}, "invalid_barcode"),
        (
            {"name": "photos", "barcodes": [f"B{index:07d}" for index in range(30)]},
            "too_many_tapes",
        ),
    ],
)
def test_malformed_arguments_are_refused_not_normalised_away(
    app_context: Any, arguments: dict[str, Any], code: str
) -> None:
    facade = setup_facade(app_context.catalog)
    with pytest.raises(SetupRefusedError) as excinfo:
        build_setup_registry().plan("add_tapes_to_volume_group", facade, arguments)
    assert excinfo.value.code == code


def test_adding_a_tape_that_is_already_in_the_pool_is_a_no_op(
    app_context: Any, seeded: dict[str, Any]
) -> None:
    """Not ambiguity: the operator asked for a state that already holds."""
    facade = setup_facade(app_context.catalog)
    registry = build_setup_registry()
    action = registry.plan(
        "add_tapes_to_volume_group",
        facade,
        {"name": VOLUME_GROUP, "barcodes": [BARCODES[0]]},
    )
    assert "already in it" in action.preview
    result = registry.perform(action, facade)
    assert result["added"] == []
    assert result["alreadyPresent"] == [BARCODES[0]]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_every_executed_action_logs_one_structured_line(
    app_context: Any, caplog: pytest.LogCaptureFixture
) -> None:
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Created."),
        ],
        Confirmer(True),
    )
    with caplog.at_level(logging.INFO, logger="openblade.assistant.setup"):
        session.ask("create photos")

    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 1
    assert "actor=assistant" in lines[0]
    assert "tool=create_volume_group" in lines[0]
    assert "outcome=executed" in lines[0]
    assert '"name": "photos"' in lines[0]
    assert '"created": true' in lines[0]


def test_a_decline_is_logged_too(app_context: Any, caplog: pytest.LogCaptureFixture) -> None:
    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Fine."),
        ],
        Confirmer(False),
    )
    with caplog.at_level(logging.INFO, logger="openblade.assistant.setup"):
        session.ask("create photos")
    assert "outcome=declined" in caplog.records[0].getMessage()


def test_the_audit_line_cannot_be_forged_with_a_newline(
    app_context: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Log forging: a model-supplied name must not be able to add a second line."""
    action = PendingAction(
        tool="create_volume_group",
        arguments={"name": "photos\nactor=root outcome=executed"},
        preview="…",
    )
    with caplog.at_level(logging.INFO, logger="openblade.assistant.setup"):
        log_action(action, outcome="declined")
    message = caplog.records[0].getMessage()
    assert "\n" not in message
    assert "\\nactor=root" in message


# ---------------------------------------------------------------------------
# The REPL's confirmation prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", True),
        ("Y", True),
        ("yes", True),
        ("", False),
        ("n", False),
        ("ok", False),
        ("yep", False),
    ],
)
def test_repl_confirmation_requires_an_explicit_yes(
    monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool
) -> None:
    from openblade.cli import assist as assist_cli

    monkeypatch.setattr("builtins.input", lambda *args: answer)
    action = PendingAction(tool="create_volume_group", arguments={"name": "x"}, preview="Create x.")
    assert assist_cli._confirm_action(action) is expected


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_repl_confirmation_treats_an_interrupt_as_no(
    monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    from openblade.cli import assist as assist_cli

    def boom(*args: Any) -> str:
        raise interrupt()

    monkeypatch.setattr("builtins.input", boom)
    action = PendingAction(tool="create_volume_group", arguments={"name": "x"}, preview="Create x.")
    assert assist_cli._confirm_action(action) is False


def test_one_shot_cli_builds_a_session_without_a_confirm_callback(
    monkeypatch: pytest.MonkeyPatch, app_context: Any
) -> None:
    """The CLI, not the session, is what decides that one-shot has no tier 1."""
    from openblade.cli import assist as assist_cli

    captured: dict[str, Any] = {}

    def fake_create_session(context: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENBLADE_OLLAMA_URL", "http://ollama.test:11434")
    monkeypatch.setattr(assist_cli, "create_session", fake_create_session)
    monkeypatch.setattr("openblade.cli.main._get_context", lambda: app_context)

    assist_cli._build_session(interactive=False)
    assert captured["confirm"] is None
    assist_cli._build_session(interactive=True)
    assert captured["confirm"] is assist_cli._confirm_action


# ---------------------------------------------------------------------------
# Failure paths (adversarial-review regressions)
# ---------------------------------------------------------------------------


class BoomCatalog:
    """A catalog whose nth volume-group write explodes with a DSN in the text."""

    LEAK = "unable to open database file '/data/secret/openblade.db'"

    def __init__(self, *, fail_on: int = 1, fail_reads: bool = False) -> None:
        self._fail_on = fail_on
        self._fail_reads = fail_reads
        self.calls = 0
        self.added: list[str] = []
        self.group = type("G", (), {"id": "g1", "name": "pool", "barcodes": [], "cartridges": []})()

    def list_volume_groups(self) -> list[Any]:
        if self._fail_reads:
            raise RuntimeError(self.LEAK)
        return [self.group]

    def get_volume_group(self, name: str) -> Any:
        if self._fail_reads:
            raise RuntimeError(self.LEAK)
        return self.group if name == "pool" else None

    def list_cartridges(self) -> list[Any]:
        return [
            type("C", (), {"barcode": barcode, "volume_group_id": None})()
            for barcode in ("A0000001", "B0000002", "C0000003")
        ]

    def get_cartridge(self, barcode: str) -> Any:
        return type("C", (), {"barcode": barcode, "volume_group_id": None})()

    def create_volume_group(self, name: str) -> Any:
        raise RuntimeError(self.LEAK)

    def add_barcode_to_volume_group(self, group_id: str, barcode: str) -> Any:
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError(self.LEAK)
        self.added.append(barcode)
        return None


def _boom_session(app_context: Any, catalog: Any, responses: list[dict[str, Any]], confirm: Any):
    client, script = scripted_client(responses)
    config = assistant_config()
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
        setup_registry=build_setup_registry(),
        setup=setup_facade(catalog),
        confirm=confirm,
    )
    return session, script


def test_a_failure_while_checking_never_reaches_the_model_or_kills_the_repl(
    app_context: Any,
) -> None:
    """``plan`` reads the live catalog. A database failure there used to escape the
    loop entirely: not an AssistantError, so the transcript was not rewound and the
    REPL died with a traceback carrying the DSN."""
    confirm = Confirmer(True)
    session, _ = _boom_session(
        app_context,
        BoomCatalog(fail_reads=True),
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("I could not check that."),
        ],
        confirm,
    )
    session.ask("create photos")
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["status"] == "unavailable"
    assert payload["executed"] is False
    assert BoomCatalog.LEAK not in json.dumps(payload)
    assert "RuntimeError" in payload["error"]
    assert confirm.asked == [], "the operator must not be asked about a broken action"


def test_a_broken_confirmation_prompt_is_a_no(app_context: Any) -> None:
    """Fail closed: if we cannot establish that the operator agreed, they did not."""

    def broken(action: PendingAction) -> bool:
        raise ValueError("terminal exploded")

    session, _ = _setup_session(
        app_context,
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            prose_response("Nothing done."),
        ],
        broken,
    )
    session.ask("create photos")
    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["status"] == "declined_by_operator"
    assert app_context.catalog.get_volume_group("photos") is None


def test_a_confirmed_write_that_fails_reports_what_actually_landed(app_context: Any) -> None:
    """The repository commits per cartridge, so a mid-loop failure leaves rows
    behind. Reporting "nothing happened" there would be a lie in the audit trail."""
    catalog = BoomCatalog(fail_on=2)
    session, _ = _boom_session(
        app_context,
        catalog,
        [
            tool_call_response(
                "add_tapes_to_volume_group",
                {"name": "pool", "barcodes": ["A0000001", "B0000002", "C0000003"]},
            ),
            prose_response("Partly done."),
        ],
        Confirmer(True),
    )
    session.ask("add three tapes")

    payload = json.loads(_tool_messages(session)[0]["content"])
    assert payload["status"] == "partially_applied"
    assert payload["applied"] == ["A0000001"] == catalog.added
    assert BoomCatalog.LEAK not in json.dumps(payload)


def test_a_partial_write_is_named_in_the_audit_log(
    app_context: Any, caplog: pytest.LogCaptureFixture
) -> None:
    session, _ = _boom_session(
        app_context,
        BoomCatalog(fail_on=2),
        [
            tool_call_response(
                "add_tapes_to_volume_group", {"name": "pool", "barcodes": ["A0000001", "B0000002"]}
            ),
            prose_response("Partly done."),
        ],
        Confirmer(True),
    )
    with caplog.at_level(logging.INFO, logger="openblade.assistant.setup"):
        session.ask("add two tapes")
    line = caplog.records[0].getMessage()
    assert "outcome=partial" in line
    assert "A0000001" in line
    assert BoomCatalog.LEAK not in line


@pytest.mark.parametrize("stage", ["plan", "execute"])
def test_a_facade_violation_is_not_handed_to_the_model_as_data(
    app_context: Any, stage: str
) -> None:
    """A tool reaching outside the facade is the loudest signal this design has;
    neither broad ``except`` may turn it into a polite "could not be completed".

    Both stages are exercised: the plan path and the write path have separate
    handlers, and an earlier version of this test only tripped the first — so the
    write path's re-raise was uncovered and a mutation run proved it.
    """
    from openblade.assistant.errors import SetupFacadeViolationError

    class Rogue:
        """Behaves until the chosen stage, then reaches outside the facade."""

        def plan_new_volume_group(self, **kwargs: Any) -> dict[str, Any]:
            if stage == "plan":
                raise SetupFacadeViolationError("a tool reached outside the facade")
            return {"name": kwargs.get("name"), "existingVolumeGroups": []}

        def new_volume_group(self, **kwargs: Any) -> dict[str, Any]:
            raise SetupFacadeViolationError("a tool reached outside the facade")

    session, _ = _setup_session(app_context, [prose_response("x")], Confirmer(True))
    session.setup = Rogue()  # type: ignore[assignment]
    with pytest.raises(SetupFacadeViolationError):
        session._run_setup_tool(ToolCall(name="create_volume_group", arguments={"name": "photos"}))


def test_a_confirmed_write_survives_a_failed_turn(app_context: Any) -> None:
    """Operator says yes, the write lands, then the model loops past max_rounds.

    The write happened; the transcript and the CLI must both still say so, or the
    assistant proposes it again next turn and asks the operator to confirm
    something already done.
    """
    client, _script = scripted_client(
        [
            tool_call_response("create_volume_group", {"name": "photos"}),
            tool_call_response("get_inventory", {}),
        ]
    )
    config = assistant_config(max_rounds=2)
    session = AssistantSession(
        client=OllamaClient(config, client=client),
        registry=build_registry(),
        context=_context(app_context),
        config=config,
        setup_registry=build_setup_registry(),
        setup=setup_facade(app_context.catalog),
        confirm=Confirmer(True),
    )
    with pytest.raises(AssistantLoopLimitError):
        session.ask("create photos and then look around")

    assert app_context.catalog.get_volume_group("photos") is not None
    assert session.executed_this_turn == ("create_volume_group",)
    kept = json.dumps(session.messages)
    assert '\\"executed\\": true' in kept or '"executed": true' in kept


def test_a_group_named_zero_is_not_mistaken_for_a_missing_name(app_context: Any) -> None:
    facade = setup_facade(app_context.catalog)
    action = build_setup_registry().plan("create_volume_group", facade, {"name": "0"})
    assert action.arguments == {"name": "0"}


@pytest.mark.parametrize(
    "name",
    [
        "purge_volume_group",
        "destroy_pool",
        "drop_volume_group",
        "truncate_catalog",
        "clear_catalog",
        "prune_file_records",
        "reset_library",
        "export_tape_to_mailslot",
        "import_cartridge",
        "unmount_ltfs",
        "rename_volume_group",
        "revoke_api_token",
        "deactivate_user",
        "grant_admin",
    ],
)
def test_the_denylist_covers_the_verbs_a_reviewer_reached_for(name: str) -> None:
    """Every one of these was accepted by the first eleven-verb denylist."""
    from openblade.assistant.setup_tools import reject_destructive_name

    with pytest.raises(SetupRegistryViolationError):
        reject_destructive_name(name)
