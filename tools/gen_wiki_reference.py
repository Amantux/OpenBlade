"""Generate the OpenBlade wiki reference pages from live introspection.

Two artifacts are produced under ``docs/wiki/reference/``:

* ``cli.md``  -- every Typer command and sub-command, with its options,
  arguments, types and defaults, read out of the click command tree that
  Typer builds for :data:`openblade.cli.main.app`.
* ``api.md``  -- every FastAPI operation, read out of the generated OpenAPI
  schema for :data:`openblade.api.main.app` and grouped by tag.

Why introspection rather than a hand-written list: hand-maintained command
tables in this repo have already drifted from the code. These pages are
regenerated and diffed by ``tests/unit/test_wiki_reference_generated.py``, so a
new command or route fails a test until the page is regenerated.

Why the OpenAPI schema and not ``app.routes``: FastAPI >= 0.130 includes
routers lazily, so ``app.routes`` holds ``_IncludedRouter`` placeholders rather
than a flattened list of ``APIRoute`` objects. Walking ``app.routes`` therefore
silently misses almost every route. ``app.openapi()`` is the only complete view.

The Quantum AML / iBlade emulator surface (~1000 operations) is deliberately
NOT expanded here -- it is a separate wire contract with its own generated
catalog. We emit a per-tag operation count and link to that catalog instead.

Usage::

    python3 tools/gen_wiki_reference.py           # write the pages
    python3 tools/gen_wiki_reference.py --check   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Document THIS checkout, not whichever copy of `openblade` happens to be
# pip-installed. Running `python3 tools/gen_wiki_reference.py` puts `tools/` on
# sys.path[0], not the repo root, so an editable install elsewhere on the
# machine wins the import and the pages silently describe the wrong tree. This
# was observed: a worktree generated a page describing the main checkout.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REFERENCE_DIR = ROOT / "docs" / "wiki" / "reference"
CLI_PATH = REFERENCE_DIR / "cli.md"
API_PATH = REFERENCE_DIR / "api.md"

GENERATED_BANNER = (
    "<!-- GENERATED FILE -- do not edit by hand.\n"
    "     Regenerate with: python3 tools/gen_wiki_reference.py\n"
    "     Guarded by: tests/unit/test_wiki_reference_generated.py -->"
)

#: Tag prefixes belonging to the Quantum emulator wire surface. Operations under
#: these tags are counted, not expanded -- see the module docstring.
EMULATOR_TAG_PREFIXES = ("aml", "iblade", "rbac")

EMULATOR_CATALOG_LINK = "../../../openblade/emulator_contract/quantum_i3_endpoint_catalog.md"
EMULATOR_CONTRACT_LINK = "../../../openblade/emulator_contract/README.md"

#: Tags whose operations are native OpenBlade control-plane surface, in the
#: order we want them to appear. Anything not listed is appended alphabetically,
#: so a new tag shows up in the page rather than being dropped.
NATIVE_TAG_ORDER = (
    "health",
    "inventory",
    "cartridges",
    "tape-ops",
    "ltfs",
    "volume-groups",
    "archive",
    "restore",
    "jobs",
    "catalog",
    "virtual",
    "storage",
    "NAS Config",
    "upload-download",
    "libraries",
    "gateway",
    "proxy",
    "safety",
    "dashboard",
    "test-runner",
)

UNTAGGED = "(untagged)"


# ---------------------------------------------------------------------------
# CLI introspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamDoc:
    """One rendered CLI parameter."""

    kind: str
    invocation: str
    type_name: str
    required: bool
    default: str
    help_text: str


@dataclass(frozen=True)
class CommandDoc:
    """One rendered CLI command (a leaf, not a group)."""

    path: tuple[str, ...]
    help_text: str
    params: tuple[ParamDoc, ...]


def _subcommands(command: Any) -> dict[str, Any] | None:
    """Return a group's sub-commands, or None if ``command`` is a leaf.

    Deliberately duck-typed rather than ``isinstance(command, click.Group)``:
    Typer >= 0.16 vendors its own click fork under ``typer._click``, so a
    ``TyperGroup`` is NOT an instance of the public ``click.Group``. An
    isinstance check silently classifies the whole command tree as one leaf.
    """
    commands = getattr(command, "commands", None)
    return commands if isinstance(commands, dict) else None


def _help_of(command: Any) -> str:
    text = str(getattr(command, "help", None) or getattr(command, "short_help", "") or "")
    return " ".join(text.strip().split("\n\n")[0].split())


def _type_name(param: Any) -> str:
    param_type = param.type
    choices = getattr(param_type, "choices", None)
    if choices is not None:
        return "choice[" + "|".join(str(choice) for choice in choices) + "]"
    name = getattr(param_type, "name", None)
    return str(name or param_type).upper()


def _default_repr(param: Any) -> str:
    if param.required:
        return "-"
    default = param.default
    if default is None:
        return "none"
    if isinstance(default, bool):
        return "true" if default else "false"
    return f"`{default}`"


def _param_doc(param: Any) -> ParamDoc | None:
    kind = str(getattr(param, "param_type_name", ""))
    if kind == "option":
        if param.name == "help":
            return None
        invocation = ", ".join(f"`{opt}`" for opt in param.opts)
    elif kind == "argument":
        invocation = f"`{str(param.metavar or param.name or '').upper()}`"
    else:  # pragma: no cover - click has no third parameter kind today
        return None
    help_text = " ".join(str(getattr(param, "help", "") or "").split())
    return ParamDoc(
        kind=kind,
        invocation=invocation,
        type_name=_type_name(param),
        required=bool(param.required),
        default=_default_repr(param),
        help_text=help_text or "-",
    )


def _walk_commands(command: Any, path: tuple[str, ...]) -> list[CommandDoc]:
    children = _subcommands(command)
    if children is not None:
        collected: list[CommandDoc] = []
        for name in sorted(children):
            collected.extend(_walk_commands(children[name], (*path, name)))
        return collected
    params = tuple(doc for doc in (_param_doc(p) for p in command.params) if doc is not None)
    return [CommandDoc(path=path, help_text=_help_of(command), params=params)]


def collect_cli_commands() -> tuple[list[CommandDoc], dict[str, str]]:
    """Return every leaf command plus the help text of each command group."""
    import typer

    from openblade.cli.main import app as cli_app

    root = typer.main.get_command(cli_app)
    groups: dict[str, str] = {}
    children = _subcommands(root) or {}
    for name in sorted(children):
        if _subcommands(children[name]) is not None:
            groups[name] = _help_of(children[name])
    return _walk_commands(root, ()), groups


def render_cli_markdown() -> str:
    commands, groups = collect_cli_commands()
    lines: list[str] = [
        GENERATED_BANNER,
        "",
        "# CLI reference",
        "",
        "Every command exposed by the `openblade` Typer application, introspected",
        "from `openblade.cli.main:app`.",
        "",
        "`pip install -e .` puts the `openblade` console script on your PATH.",
        "Every command below also accepts `--help`.",
        "",
        "> ⚠️ **Every command here runs against the SIMULATOR, whatever",
        "> `OPENBLADE_BACKEND` is set to — except `openblade hardware connect-i3`",
        "> and `openblade hardware validate-ltfs`, which are the only two that read",
        "> the real configuration.** The rest build their config by hand in",
        "> `openblade/cli/main.py:_default_config()`, which leaves the backend at",
        "> its `mock` default and ignores `OPENBLADE_DB_URL`. `openblade inventory`",
        "> on a real-hardware host prints simulator data and exits 0. Drive real",
        "> hardware through the HTTP API.",
        "",
        f"**{len(commands)} commands**, in {len(groups)} sub-group(s) plus the top level.",
        "",
        "## Command groups",
        "",
        "| Group | Purpose |",
        "| --- | --- |",
    ]
    for name in sorted(groups):
        lines.append(f"| `openblade {name}` | {groups[name] or '-'} |")
    lines.extend(["", "## Commands", ""])
    for command in commands:
        invocation = " ".join(("openblade", *command.path))
        lines.append(f"### `{invocation}`")
        lines.append("")
        lines.append(command.help_text or "_No help text._")
        lines.append("")
        if not command.params:
            lines.extend(["Takes no arguments or options.", ""])
            continue
        lines.append("| Parameter | Kind | Type | Required | Default | Help |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for param in command.params:
            lines.append(
                f"| {param.invocation} | {param.kind} | `{param.type_name}` | "
                f"{'yes' if param.required else 'no'} | {param.default} | "
                f"{param.help_text} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# API introspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationDoc:
    """One rendered HTTP operation."""

    method: str
    path: str
    summary: str
    params: str
    request_body: str
    response_model: str


_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def _schema_name(node: Any) -> str:
    """Best-effort name for a schema node from the OpenAPI document."""
    if not isinstance(node, dict):
        return ""
    ref = node.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    for key in ("items", "allOf", "anyOf", "oneOf"):
        value = node.get(key)
        if isinstance(value, dict):
            name = _schema_name(value)
            if name:
                return f"{name}[]" if key == "items" else name
        if isinstance(value, list):
            for entry in value:
                name = _schema_name(entry)
                if name and name != "NoneType":
                    return name
    schema_type = node.get("type")
    return str(schema_type) if isinstance(schema_type, str) else ""


def _response_model(operation: dict[str, Any]) -> str:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return "-"
    for status in ("200", "201", "202", "204"):
        response = responses.get(status)
        if not isinstance(response, dict):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            return "(no body)"
        json_content = content.get("application/json")
        if isinstance(json_content, dict):
            name = _schema_name(json_content.get("schema"))
            if name:
                return f"`{name}`"
        return "`" + "`, `".join(sorted(content)) + "`"
    return "-"


def _request_body(operation: dict[str, Any]) -> str:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return "-"
    content = body.get("content")
    if not isinstance(content, dict):
        return "yes"
    json_content = content.get("application/json")
    if isinstance(json_content, dict):
        name = _schema_name(json_content.get("schema"))
        if name:
            return f"`{name}`"
    return "`" + "`, `".join(sorted(content)) + "`"


def _parameters(operation: dict[str, Any]) -> str:
    params = operation.get("parameters")
    if not isinstance(params, list) or not params:
        return "-"
    rendered: list[str] = []
    for param in params:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name", "?"))
        location = str(param.get("in", "?"))
        marker = "" if param.get("required") else "?"
        rendered.append(f"`{name}`{marker} ({location})")
    return ", ".join(rendered) or "-"


def collect_operations() -> dict[str, list[OperationDoc]]:
    """Return operations grouped by tag, read from the OpenAPI schema."""
    from openblade.api.main import app as api_app

    schema = api_app.openapi()
    paths = schema.get("paths", {})
    grouped: dict[str, list[OperationDoc]] = {}
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.upper() not in _METHOD_ORDER:
                continue
            if not isinstance(operation, dict):
                continue
            tags = operation.get("tags")
            tag = str(tags[0]) if isinstance(tags, list) and tags else UNTAGGED
            grouped.setdefault(tag, []).append(
                OperationDoc(
                    method=method.upper(),
                    path=str(path),
                    summary=" ".join(str(operation.get("summary", "")).split()) or "-",
                    params=_parameters(operation),
                    request_body=_request_body(operation),
                    response_model=_response_model(operation),
                )
            )
    for operations_list in grouped.values():
        operations_list.sort(key=lambda op: (op.path, _METHOD_ORDER[op.method]))
    return grouped


def _is_emulator_tag(tag: str) -> bool:
    return tag.lower().startswith(EMULATOR_TAG_PREFIXES)


def _ordered_native_tags(tags: list[str]) -> list[str]:
    known = [tag for tag in NATIVE_TAG_ORDER if tag in tags]
    extra = sorted(tag for tag in tags if tag not in NATIVE_TAG_ORDER)
    return known + extra


def render_api_markdown() -> str:
    grouped = collect_operations()
    assert_app_surface_is_complete(grouped)
    native_tags = _ordered_native_tags([t for t in grouped if not _is_emulator_tag(t)])
    emulator_tags = sorted(t for t in grouped if _is_emulator_tag(t))
    native_count = sum(len(grouped[t]) for t in native_tags)
    emulator_count = sum(len(grouped[t]) for t in emulator_tags)

    lines: list[str] = [
        GENERATED_BANNER,
        "",
        "# HTTP API reference",
        "",
        "Introspected from the OpenAPI schema of `openblade.api.main:app`.",
        "",
        f"**{native_count + emulator_count} operations** total: "
        f"{native_count} on the native OpenBlade control plane, "
        f"{emulator_count} on the Quantum AML / iBlade emulator surface.",
        "",
        "The application serves two surfaces from one ASGI app. Setting",
        "`OPENBLADE_SCALAR_API_ONLY=true` puts it in emulator-only mode, where the",
        "native surfaces below return 404.",
        "",
        "Interactive docs for a running instance are at `/docs` and `/redoc`.",
        "",
        "## Native OpenBlade control plane",
        "",
    ]
    for tag in native_tags:
        operations = grouped[tag]
        plural = "operation" if len(operations) == 1 else "operations"
        lines.append(f"### `{tag}` ({len(operations)} {plural})")
        lines.append("")
        lines.append("| Method | Path | Summary | Parameters | Request | Response |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for op in operations:
            lines.append(
                f"| `{op.method}` | `{op.path}` | {op.summary} | {op.params} | "
                f"{op.request_body} | {op.response_model} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Quantum AML / iBlade emulator surface",
            "",
            "These operations implement the Quantum Scalar i3/i6 Web Services wire",
            "contract. They are **not** documented here: the path set is the wire",
            "contract itself and is generated, reviewed and gated separately.",
            "",
            f"See the generated endpoint catalog at "
            f"[`quantum_i3_endpoint_catalog.md`]({EMULATOR_CATALOG_LINK}) and the "
            f"boundary contract at [`emulator_contract/README.md`]"
            f"({EMULATOR_CONTRACT_LINK}).",
            "",
            "| Tag | Operations |",
            "| --- | --- |",
        ]
    )
    for tag in emulator_tags:
        lines.append(f"| `{tag}` | {len(grouped[tag])} |")
    lines.append(f"| **total** | **{emulator_count}** |")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


#: Environment pinned before `openblade.api.main` is imported. The generated
#: pages must be a function of the CODE, not of the developer's shell.
#:
#: Two observed failures this prevents:
#:   * `OPENBLADE_SCALAR_API_ONLY=true` scopes the app down to the emulator-only
#:     surface, so api.md would be regenerated missing every native route.
#:   * `OPENBLADE_BACKEND=real` (which the bring-up guides tell operators to set)
#:     makes importing the app raise RealHardwareDisabledError, so the staleness
#:     test errors with a hardware message that looks nothing like a docs problem.
#:
#: The DB URL is redirected too: importing the app runs init_db() and seeds demo
#: rows, which would otherwise be written into the operator's real
#: ~/.openblade/openblade.db -- the same file the CLI reads.
_PINNED_ENV: dict[str, str | None] = {
    "OPENBLADE_BACKEND": "mock",
    "OPENBLADE_REAL_HARDWARE_ENABLED": None,
    "OPENBLADE_SCALAR_API_ONLY": None,
    "OPENBLADE_IBLADE_COMPAT_MODE": None,
    "OPENBLADE_ROBOTICS_TRANSPORT": None,
}


@contextlib.contextmanager
def pinned_introspection_env() -> Iterator[None]:
    """Pin the env that shapes the app surface, then restore it.

    Note the limit: if `openblade.api.main` was already imported by something
    else in this process, its routes are already fixed and this cannot help.
    :func:`assert_app_surface_is_complete` is the backstop for that case.
    """
    previous = {key: os.environ.get(key) for key in (*_PINNED_ENV, "OPENBLADE_DB_URL")}
    with tempfile.TemporaryDirectory(prefix="openblade-wiki-gen-") as tmp:
        try:
            for key, value in _PINNED_ENV.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            os.environ["OPENBLADE_DB_URL"] = f"sqlite:///{Path(tmp) / 'introspect.db'}"
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def assert_app_surface_is_complete(grouped: dict[str, list[OperationDoc]]) -> None:
    """Fail loudly if the app was scoped down before we could introspect it.

    Emulator-only mode yields a schema with no native control plane. Writing
    that out would look like "the API shrank" rather than "the generator ran in
    the wrong mode", so refuse instead of producing a plausible wrong page.
    """
    native = [tag for tag in grouped if not _is_emulator_tag(tag)]
    if not native:
        raise RuntimeError(
            "No native control-plane operations found. The app was probably "
            "imported in emulator-only mode (OPENBLADE_SCALAR_API_ONLY) before "
            "this generator could pin the environment."
        )


def assert_documenting_this_checkout() -> None:
    """Fail loudly if `openblade` resolved to a different checkout.

    See the sys.path note at the top of this module. A silent mis-resolution
    produces plausible-looking but wrong documentation, which is worse than an
    error.
    """
    import openblade

    package_root = Path(openblade.__file__).resolve().parent.parent
    if package_root != ROOT:
        raise RuntimeError(
            f"`openblade` imported from {package_root}, expected {ROOT}. "
            "The generated pages would describe the wrong checkout."
        )


def build_pages() -> dict[Path, str]:
    """Return the full generated content keyed by destination path."""
    with pinned_introspection_env():
        assert_documenting_this_checkout()
        return {CLI_PATH: render_cli_markdown(), API_PATH: render_api_markdown()}


def write_pages() -> list[Path]:
    written: list[Path] = []
    for path, content in build_pages().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def check_pages() -> list[Path]:
    """Return the paths whose on-disk content is stale (or missing)."""
    stale: list[Path] = []
    for path, content in build_pages().items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            stale.append(path)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if any generated page is stale.",
    )
    args = parser.parse_args(argv)
    if args.check:
        stale = check_pages()
        for path in stale:
            print(f"stale: {path.relative_to(ROOT)}")
        if stale:
            print("Regenerate with: python3 tools/gen_wiki_reference.py")
            return 1
        print("wiki reference pages are up to date")
        return 0
    for path in write_pages():
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
