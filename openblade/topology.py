"""Runtime topology verification for OpenBlade.

OpenBlade has no message broker or periodic scheduler; jobs run in-process. The
production-operability analog of "every queue has a consumer / scheduler
registered" is: the in-process job worker + services are wired, the required
endpoints actually respond, both backends are present, and the emulator fleet is
configured. This proves the expected topology is *functioning* (endpoints probed,
not merely introspected) — not just that a process is up.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

BLOCKING = "blocking"
WARNING = "warning"

# Required externally-observable surfaces. Presence = the endpoint answers with
# anything other than 404/5xx (401/403 count as "present + auth working").
REQUIRED_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/version"),
    ("POST", "/aml/users/login"),
    ("GET", "/aml/physicalLibrary/elements"),
    ("GET", "/aml/system/emulator/latency/metrics/prometheus"),
    ("GET", "/archive/"),
    ("GET", "/restore/"),
    ("GET", "/jobs/"),
    ("GET", "/iblade/states"),
]

# The in-process "consumers" that must be wired into the AppContext.
REQUIRED_CONTEXT_MEMBERS = [
    "library", "ltfs", "catalog", "queue", "worker",
    "inventory_service", "format_service", "archive_service", "restore_service",
]

# A probe takes (method, path) and returns an HTTP status code.
Probe = Callable[[str, str], int]


@dataclass(frozen=True)
class TopologyFinding:
    severity: str
    code: str
    message: str


def verify_topology(*, probe: Probe, context: object, emulator_urls: Iterable[str]) -> list[TopologyFinding]:
    findings: list[TopologyFinding] = []

    for method, path in REQUIRED_ENDPOINTS:
        try:
            status = probe(method, path)
        except Exception as exc:  # noqa: BLE001 - a raising probe is itself a failure
            findings.append(TopologyFinding(BLOCKING, "endpoint_error",
                f"{method} {path} raised during probe: {exc}"))
            continue
        if status == 404:
            findings.append(TopologyFinding(BLOCKING, "missing_endpoint",
                f"required endpoint {method} {path} is not registered (404)"))
        elif status >= 500:
            findings.append(TopologyFinding(BLOCKING, "endpoint_error",
                f"required endpoint {method} {path} returned {status}"))

    for member in REQUIRED_CONTEXT_MEMBERS:
        if getattr(context, member, None) is None:
            findings.append(TopologyFinding(BLOCKING, "unwired_component",
                f"AppContext.{member} is not wired — its work has no consumer"))

    if not [u for u in emulator_urls if u]:
        findings.append(TopologyFinding(WARNING, "no_emulator_fleet",
            "no OPENBLADE_EMULATOR_URLS configured — fleet features unavailable"))

    return findings


def is_healthy_topology(findings: list[TopologyFinding]) -> bool:
    return not any(f.severity == BLOCKING for f in findings)
