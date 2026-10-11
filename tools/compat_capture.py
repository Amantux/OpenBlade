"""Compatibility capture + sanitize pipeline for the ``compatibility/`` corpus.

Replays each case's ``request`` block against a live base URL (the OpenBlade
emulator or, on-site, a real Scalar i3) and writes a new case whose
``expected`` is filled from the observed response. Every written case passes
through :func:`sanitize` first so tokens, serials, hosts, timestamps and
passwords never reach the repository.

    capture --base-url URL --recipe <case.json|dir> --out <dir>
            [--source self-captured|captured] [--profile scalar-i3-341g]
            [--login user:pass] [--appliance-firmware VER --attest NAME]
    capture --base-url URL --recipe <case.json|dir> --diff

``source: captured`` is appliance-only and ENFORCED by the corpus harness, so
the tool refuses to write it without ``--appliance-firmware`` AND ``--attest``.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TOKEN = "<token>"
SERIAL = "<serial>"
HOST = "<host>"
TIMESTAMP = "<timestamp>"
REDACTED = "<redacted>"
# A request password that was NOT the --login credential (a deliberate bad-password case).
REDACTED_INVALID = "<redacted-invalid>"

SOURCES = ("self-captured", "captured")
_COOKIE_ATTRS = frozenset({"path", "domain", "expires", "max-age", "samesite"})


class CaptureRefusedError(Exception):
    """The requested capture would mislabel provenance; nothing was written."""


@dataclass(frozen=True)
class Rules:
    # Key rules match on the lower-cased key with "_"/"-" removed, by substring.
    token_keys: tuple[str, ...] = ("session", "token", "authorization", "apikey")
    serial_keys: tuple[str, ...] = ("serial",)
    password_keys: tuple[str, ...] = ("password", "passwd")
    cookie_keys: tuple[str, ...] = ("cookie", "setcookie")
    bearer: re.Pattern[str] = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
    url_host: re.Pattern[str] = re.compile(r"(?i)\b(https?|wss?)://[^/\s\"'?#]+")
    ipv4: re.Pattern[str] = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
    timestamp: re.Pattern[str] = re.compile(
        r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        r"|\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2} GMT"
    )


DEFAULT_RULES = Rules()


_XML_START_TAG = re.compile(r"<[A-Za-z_][\w.-]*\s[^<>]*>")
_XML_ATTR = re.compile(r"([A-Za-z_][\w.:-]*)\s*=\s*([\"'])(.*?)\2")
_XML_ELEMENT = re.compile(r"<([A-Za-z_][\w.-]*)((?:\s[^<>]*)?)>([^<]*)</\1>")


def _norm(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def _key_hit(key: str, needles: Iterable[str]) -> bool:
    k = _norm(key)
    return any(n in k for n in needles)


def _scrub_cookie(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return m.group(0) if name.strip().lower() in _COOKIE_ATTRS else f"{name}={TOKEN}"

    return re.sub(r"([^=;,\s]+)=([^;,]*)", repl, value)


def _secret_mark(name: str, rules: Rules) -> str | None:
    for needles, mark in (
        (rules.password_keys, REDACTED),
        (rules.token_keys, TOKEN),
        (rules.serial_keys, SERIAL),
    ):
        if _key_hit(name, needles):
            return mark
    return None


def _scrub_xml(value: str, rules: Rules) -> str:
    # XML bodies (Accept: application/xml) carry the same secrets as JSON keys,
    # both as element text and as attribute values (``sessionId="..."``).
    def attr_repl(m: re.Match[str]) -> str:
        mark = _secret_mark(m.group(1), rules)
        return m.group(0) if mark is None else f"{m.group(1)}={m.group(2)}{mark}{m.group(2)}"

    def tag_repl(m: re.Match[str]) -> str:
        return _XML_ATTR.sub(attr_repl, m.group(0))

    def elem_repl(m: re.Match[str]) -> str:
        tag, attrs, text = m.group(1), m.group(2), m.group(3)
        if not text:  # already a placeholder (or empty): keep idempotent
            return m.group(0)
        mark = _secret_mark(tag, rules)
        return m.group(0) if mark is None else f"<{tag}{attrs}>{mark}</{tag}>"

    # Element text first: once an attribute holds a placeholder the start tag
    # no longer matches _XML_ELEMENT (that is what keeps a re-run idempotent).
    value = _XML_ELEMENT.sub(elem_repl, value)
    return _XML_START_TAG.sub(tag_repl, value)


def _scrub_str(value: str, rules: Rules) -> str:
    value = _scrub_xml(value, rules)
    value = rules.bearer.sub(lambda m: f"{m.group(1)} {TOKEN}", value)
    value = rules.url_host.sub(lambda m: f"{m.group(1)}://{HOST}", value)
    value = rules.ipv4.sub(HOST, value)
    return rules.timestamp.sub(TIMESTAMP, value)


def _mask_leaves(node: Any, mark: str, keep: tuple[str, ...] = ()) -> Any:
    # A secret-class key masks EVERY scalar beneath it, however it is nested.
    if node is None or isinstance(node, bool):
        return node
    if isinstance(node, dict):
        return {k: _mask_leaves(v, mark, keep) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_leaves(item, mark, keep) for item in node]
    return node if node in keep else mark


def _walk(node: Any, rules: Rules) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if value is None or isinstance(value, bool):
                out[key] = value
            elif _key_hit(key, rules.password_keys):
                out[key] = _mask_leaves(value, REDACTED, (REDACTED, REDACTED_INVALID))
            elif _key_hit(key, rules.cookie_keys) and isinstance(value, str):
                out[key] = _scrub_str(_scrub_cookie(value), rules)
            elif _key_hit(key, rules.token_keys):
                out[key] = _mask_leaves(value, TOKEN)
            elif _key_hit(key, rules.serial_keys):
                out[key] = _mask_leaves(value, SERIAL)
            else:
                out[key] = _walk(value, rules)
        return out
    if isinstance(node, list):
        return [_walk(item, rules) for item in node]
    if isinstance(node, str):
        return _scrub_str(node, rules)
    return node


def sanitize(case: dict[str, Any], rules: Rules = DEFAULT_RULES) -> dict[str, Any]:
    """Pure: return a scrubbed deep copy of ``case``; the input is not mutated."""
    result: dict[str, Any] = _walk(copy.deepcopy(case), rules)
    return result


def dumps_case(case: dict[str, Any]) -> str:
    """Deterministic serialisation: same case -> byte-identical file."""
    return json.dumps(case, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def load_recipes(path: Path) -> list[dict[str, Any]]:
    if not path.is_dir():
        return [json.loads(path.read_text())]
    # A directory's own earlier outputs are not recipes: their passwords are redacted.
    cases = [json.loads(p.read_text()) for p in sorted(path.rglob("*.json"))]
    return [c for c in cases if c.get("source") not in SOURCES]


def _login(client: httpx.Client, login: str | None) -> None:
    if login is None:
        raise CaptureRefusedError("case needs auth but --login user:pass was not given")
    user, _, password = login.partition(":")
    resp = client.post("/aml/users/login", json={"name": user, "password": password})
    if resp.status_code != 200:
        raise CaptureRefusedError(f"login as {user!r} failed with status {resp.status_code}")


def replay(client: httpx.Client, case: dict[str, Any], login: str | None) -> httpx.Response:
    # Mirrors tests/compat/test_compatibility_corpus.py::_run so captures replay identically.
    if case.get("auth"):
        _login(client, login)
    req = case["request"]
    headers = dict(req.get("headers") or {})
    if "content_type" in req:
        headers["Content-Type"] = req["content_type"]
    kwargs: dict[str, Any] = {"headers": headers or None}
    if "body" in req:
        kwargs["content"] = req["body"]
    else:
        kwargs["json"] = req.get("json")
    for _ in range(int(req.get("repeat", 0))):
        client.request(req["method"], req["path"], **kwargs)
    return client.request(req["method"], req["path"], **kwargs)


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def build_case(
    recipe: dict[str, Any],
    resp: httpx.Response,
    *,
    source: str,
    profile: str | None = None,
    appliance_firmware: str | None = None,
    attest: str | None = None,
    login: str | None = None,
) -> dict[str, Any]:
    if source not in SOURCES:
        raise CaptureRefusedError(f"source must be one of {SOURCES}, not {source!r}")
    if source == "captured" and not (appliance_firmware and attest):
        raise CaptureRefusedError(
            "source=captured is appliance-only: --appliance-firmware and --attest are both required"
        )
    raw = _body(resp)
    clean = sanitize({"b": raw})["b"]
    expected: dict[str, Any] = {"status": resp.status_code}
    ctype = resp.headers.get("content-type")
    if ctype:
        expected["headers"] = {"content-type": ctype.split(";")[0].strip()}
    if isinstance(raw, dict):
        # Only values the sanitizer left untouched are enforceable.
        expected["json_contains"] = {k: v for k, v in clean.items() if raw.get(k) == v}
    observed_headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() != "date"}
    case: dict[str, Any] = {
        key: recipe[key]
        for key in ("id", "profile", "firmware", "auth", "manual_ref", "request")
        if key in recipe
    }
    case.update(
        source=source,
        expected=expected,
        observed={"headers": observed_headers, "body": clean},
    )
    good = login.partition(":")[2] if login else None
    payload = case.get("request", {}).get("json")
    if isinstance(payload, dict):
        # Keep "right vs wrong password" replayable after redaction.
        case["request"] = {
            **case["request"],
            "json": {
                k: REDACTED_INVALID
                if _key_hit(k, DEFAULT_RULES.password_keys) and isinstance(v, str) and v != good
                else v
                for k, v in payload.items()
            },
        }
    if profile:
        case["profile"] = profile
    if source == "captured":
        case.update(firmware=appliance_firmware, attested_by=attest)
    else:
        case["captured_from"] = "openblade-emulator"
        case["fidelity_notes"] = (
            "Self-captured from the OpenBlade emulator by tools/compat_capture.py: a regression "
            "guard of emulator behaviour, NOT evidence of appliance fidelity."
        )
    return sanitize(case)


def drift(case: dict[str, Any], resp: httpx.Response) -> list[str]:
    exp = case["expected"]
    problems: list[str] = []
    if resp.status_code != exp["status"]:
        problems.append(f"status {resp.status_code} != {exp['status']}")
    for name, needle in exp.get("headers", {}).items():
        value = resp.headers.get(name)
        if value is None or needle not in value:
            problems.append(f"header {name}={value!r} lacks {needle!r}")
    if "json_contains" in exp:
        body = _body(resp)
        if not isinstance(body, dict):
            problems.append(f"body is not a JSON object: {str(body)[:80]!r}")
        else:
            for key, value in exp["json_contains"].items():
                if body.get(key, KeyError) != value:
                    problems.append(f"{key}: expected {value!r}, got {body.get(key)!r}")
    return problems


ClientFactory = Callable[[], httpx.Client]


def run_diff(factory: ClientFactory, cases: list[dict[str, Any]], login: str | None) -> int:
    total = 0
    for case in cases:
        with factory() as client:
            problems = drift(case, replay(client, case, login))
        total += len(problems)
        for p in problems:
            print(f"DRIFT {case['id']}: {p}")
    print(f"[compat_capture] diff: {len(cases)} cases, {total} drift findings")
    return 1 if total else 0


def run_capture(factory: ClientFactory, cases: list[dict[str, Any]], out: Path, **opts: Any) -> int:
    login = opts.pop("login", None)
    built = []
    for recipe in cases:  # build everything first: a refusal writes nothing
        with factory() as client:
            built.append(build_case(recipe, replay(client, recipe, login), login=login, **opts))
    out.mkdir(parents=True, exist_ok=True)
    for case in built:
        (out / f"{case['id']}.json").write_text(dumps_case(case))
    print(f"[compat_capture] wrote {len(built)} cases to {out}")
    return 0


def main(argv: list[str] | None = None, factory: ClientFactory | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compat_capture", description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["capture"])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--recipe", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--source", choices=SOURCES, default="self-captured")
    ap.add_argument("--profile")
    ap.add_argument("--login")
    ap.add_argument("--appliance-firmware")
    ap.add_argument("--attest")
    args = ap.parse_args(argv)
    make = factory or (lambda: httpx.Client(base_url=args.base_url, timeout=30.0))
    cases = load_recipes(args.recipe)
    try:
        if args.diff:
            return run_diff(make, cases, args.login)
        if args.out is None:
            ap.error("--out is required unless --diff is given")
        return run_capture(
            make,
            cases,
            args.out,
            login=args.login,
            source=args.source,
            profile=args.profile,
            appliance_firmware=args.appliance_firmware,
            attest=args.attest,
        )
    except CaptureRefusedError as exc:
        print(f"[compat_capture] REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
