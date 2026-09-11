"""Assistant behaviour: config, provider, tools, and the bounded loop.

Zero network: every test drives the real client through ``httpx.MockTransport``.
The read-only safety guarantees are tested separately in
``tests/safety/test_assistant_read_only.py``.
"""

from __future__ import annotations

import json
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
)
from openblade.assistant.provider import OllamaClient
from openblade.assistant.session import MAX_CALLS_PER_ROUND, AssistantSession
from openblade.assistant.tools import build_context, build_registry
from tests.assistant_support import (
    ARCHIVED_PATH,
    BARCODES,
    VOLUME_GROUP,
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
        library=app_context.library,
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
    inventory = app_context.library.inventory()
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
    inventory = app_context.library.inventory()
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
    assert result["driveCount"] == len(app_context.library.inventory().drives)

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
        library=app_context.library,
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
        "search_docs", _context(app_context, docs), {"query": "safety gates format token"}
    )
    assert result["matchCount"] > 0
    docs_hit = [section for section in result["sections"] if section["doc"] == "safety.md"]
    assert docs_hit, [section["doc"] for section in result["sections"]]
    assert any("safety token" in section["excerpt"].lower() for section in docs_hit)


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
        library=Plain(),
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
        library=app_context.library,
        backend="mock",
        real_hardware_enabled=False,
        db_url="postgresql://admin:s3cret@db.internal/openblade",
        scalar_password="hunter2",
    )
    rendered = repr(context)
    assert "sk-super-secret" not in rendered
    assert "s3cret" not in rendered
    assert "hunter2" not in rendered
