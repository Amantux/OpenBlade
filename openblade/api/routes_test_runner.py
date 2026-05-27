"""routes_test_runner.py — OpenBlade Test Runner API.

Provides endpoints to run tests/i3/ against the emulator or a real Quantum i3,
streaming live output via Server-Sent Events.

Safety gate: running against a real i3 requires I3_REAL_HARDWARE_ENABLED=true.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/test-runner", tags=["test-runner"])

# In-memory run registry — sufficient for single-instance dev use
_RUNS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class TestRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target: Literal["emulator", "real"] = "emulator"
    timing_profile: Literal["instant", "realistic", "hardware"] = "instant"
    i3_aml_url: str = "http://localhost:8000"
    i3_aml_user: str = "admin"
    i3_aml_password: str = "password"
    modules: list[str] | None = None  # None = all modules


class TestRunResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    started_at: str
    target: str
    timing_profile: str


class TestRunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    started_at: str
    finished_at: str | None
    target: str
    timing_profile: str
    total_tests: int
    passed: int
    failed: int
    errors: int
    skipped: int
    exit_code: int | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_pattern(modules: list[str] | None) -> str:
    """Convert a list of module short names to a pytest -k expression."""
    if not modules:
        return ""
    # Modules look like "test_01_auth" or "auth" — normalise
    patterns = []
    for m in modules:
        if not m.startswith("test_"):
            patterns.append(f"test_{m}")
        else:
            patterns.append(m)
    return " or ".join(patterns)


def _build_pytest_command(run_id: str, req: TestRunRequest) -> list[str]:
    """Build the pytest argv list. Never uses shell=True."""
    cmd = [
        "python3",
        "-m",
        "pytest",
        "tests/i3/",
        "-m",
        "i3",
        "-v",
        "--tb=short",
        "--no-header",
        f"--json-report",
        f"--json-report-file=/tmp/i3-run-{run_id}.json",
    ]
    pattern = _module_pattern(req.modules)
    if pattern:
        cmd += ["-k", pattern]
    return cmd


def _parse_json_report(run_id: str) -> dict:
    """Read the pytest-json-report output file if it exists."""
    path = Path(f"/tmp/i3-run-{run_id}.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=TestRunResponse)
async def start_test_run(req: TestRunRequest) -> TestRunResponse:
    """Start a test run. Returns run_id. Output streams via /stream/{run_id}."""
    if req.target == "real" and os.environ.get("I3_REAL_HARDWARE_ENABLED", "false").lower() != "true":
        raise HTTPException(
            status_code=403,
            detail=(
                "Real i3 target requires I3_REAL_HARDWARE_ENABLED=true. "
                "Set this environment variable on the OpenBlade API container to proceed."
            ),
        )

    run_id = str(uuid.uuid4())[:8]
    _RUNS[run_id] = {
        "run_id": run_id,
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "target": req.target,
        "timing_profile": req.timing_profile,
        "total_tests": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "exit_code": None,
        "output_lines": [],
        "_req": req,
    }

    # Fire and forget — caller streams output
    asyncio.create_task(_run_tests(run_id, req))

    return TestRunResponse(
        run_id=run_id,
        status="queued",
        started_at=_RUNS[run_id]["started_at"],
        target=req.target,
        timing_profile=req.timing_profile,
    )


@router.get("/status/{run_id}", response_model=TestRunStatus)
async def get_run_status(run_id: str) -> TestRunStatus:
    """Poll the status of a test run."""
    run = _RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return TestRunStatus(
        run_id=run["run_id"],
        status=run["status"],
        started_at=run["started_at"],
        finished_at=run["finished_at"],
        target=run["target"],
        timing_profile=run["timing_profile"],
        total_tests=run["total_tests"],
        passed=run["passed"],
        failed=run["failed"],
        errors=run["errors"],
        skipped=run["skipped"],
        exit_code=run["exit_code"],
    )


@router.get("/stream/{run_id}")
async def stream_run_output(run_id: str) -> StreamingResponse:
    """Stream test output as Server-Sent Events."""
    if run_id not in _RUNS:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs")
async def list_runs() -> list[dict]:
    """List recent test runs (last 20)."""
    runs = list(_RUNS.values())[-20:]
    return [
        {
            "run_id": r["run_id"],
            "status": r["status"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "target": r["target"],
            "timing_profile": r["timing_profile"],
            "passed": r["passed"],
            "failed": r["failed"],
            "total_tests": r["total_tests"],
        }
        for r in runs
    ]


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_tests(run_id: str, req: TestRunRequest) -> None:
    """Run pytest in a subprocess, capturing output line by line."""
    run = _RUNS[run_id]
    run["status"] = "running"

    env = dict(os.environ)
    env["I3_TEST_MODE"] = req.target
    env["I3_AML_URL"] = req.i3_aml_url
    env["I3_AML_USER"] = req.i3_aml_user
    env["I3_AML_PASSWORD"] = req.i3_aml_password
    env["I3_TIMING_PROFILE"] = req.timing_profile

    if req.target == "real":
        env["I3_REAL_HARDWARE_ENABLED"] = "true"

    cmd = _build_pytest_command(run_id, req)
    log = logger.bind(run_id=run_id, cmd=cmd[0])
    log.info("starting_test_run", target=req.target, profile=req.timing_profile)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd="/root/OpenBlade",
        )
        assert proc.stdout is not None

        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            run["output_lines"].append(line)
            _update_counts_from_line(run, line)

        await proc.wait()
        run["exit_code"] = proc.returncode

    except Exception as exc:
        log.error("test_run_error", error=str(exc))
        run["output_lines"].append(f"ERROR: {exc}")
        run["exit_code"] = -1
        run["status"] = "failed"
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        return

    # Parse JSON report for final counts
    report = _parse_json_report(run_id)
    summary = report.get("summary", {})
    if summary:
        run["passed"] = summary.get("passed", run["passed"])
        run["failed"] = summary.get("failed", run["failed"])
        run["errors"] = summary.get("error", run["errors"])
        run["skipped"] = summary.get("skipped", run["skipped"])
        run["total_tests"] = summary.get("total", run["total_tests"])

    run["status"] = "completed" if run["exit_code"] == 0 else "failed"
    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    log.info("test_run_complete", exit_code=run["exit_code"], passed=run["passed"], failed=run["failed"])


def _update_counts_from_line(run: dict, line: str) -> None:
    """Parse pytest verbose output lines for incremental pass/fail counts."""
    if " PASSED" in line:
        run["passed"] += 1
        run["total_tests"] += 1
    elif " FAILED" in line:
        run["failed"] += 1
        run["total_tests"] += 1
    elif " ERROR" in line and "::" in line:
        run["errors"] += 1
        run["total_tests"] += 1
    elif " SKIPPED" in line:
        run["skipped"] += 1
        run["total_tests"] += 1


async def _sse_generator(run_id: str) -> AsyncGenerator[bytes, None]:
    """Yield SSE events as lines are added to the run output."""
    run = _RUNS[run_id]
    sent_idx = 0

    while True:
        lines = run["output_lines"]
        while sent_idx < len(lines):
            line = lines[sent_idx]
            payload = json.dumps({"line": line, "idx": sent_idx})
            yield f"data: {payload}\n\n".encode()
            sent_idx += 1

        if run["status"] in ("completed", "failed") and sent_idx >= len(run["output_lines"]):
            # Send final status event
            status_payload = json.dumps({
                "event": "done",
                "status": run["status"],
                "passed": run["passed"],
                "failed": run["failed"],
                "total": run["total_tests"],
                "exit_code": run["exit_code"],
            })
            yield f"data: {status_payload}\n\n".encode()
            break

        await asyncio.sleep(0.2)
