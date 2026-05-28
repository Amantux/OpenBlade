"""Real-hardware command compatibility checks for the i3 smoke command set."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from tests.i3.command_matrix import run_smoke_command_matrix

pytestmark = [pytest.mark.i3, pytest.mark.real_i3]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def test_real_library_supports_smoke_command_matrix(
    i3_client: httpx.Client,
    i3_credentials: tuple[str, str],
    real_i3_guard: None,
) -> None:
    include_motion = _env_flag("I3_COMMAND_MATRIX_INCLUDE_MOTION", default=False)
    include_control_plane = _env_flag("I3_COMMAND_MATRIX_INCLUDE_CONTROL_PLANE", default=False)
    report_path = Path(os.environ.get("I3_COMMAND_MATRIX_REPORT", "/tmp/i3-command-matrix.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)

    username, password = i3_credentials
    report = run_smoke_command_matrix(
        i3_client,
        username=username,
        password=password,
        include_motion=include_motion,
        include_control_plane=include_control_plane,
    )

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    failed = [item for item in report["results"] if not bool(item["matched"])]
    assert not failed, (
        "Smoke command compatibility failed:\n"
        + "\n".join(
            f"- {item['method']} {item['path']} -> {item['status_code']} "
            f"(expected one of {item['expected_statuses']})"
            for item in failed
        )
    )
