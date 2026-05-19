from __future__ import annotations

"""Safe subprocess runner for hardware integrations."""

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

REDACT_PATTERNS: list[str] = []


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def raise_on_error(self) -> None:
        if not self.success:
            raise CommandError(self.args, self.returncode, self.stderr)


class CommandError(Exception):
    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args_list = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Command {shlex.join(args)!r} failed (rc={returncode}): {stderr}")


class SafeRunner:
    """Run external commands safely."""

    def __init__(self, dry_run: bool = False, default_timeout: int = 30) -> None:
        self.dry_run = dry_run
        self.default_timeout = default_timeout

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        """Run a command and reject string arguments to avoid injection bugs."""
        if isinstance(args, str):
            raise TypeError("args must be a list, not a string")

        effective_timeout = timeout or self.default_timeout
        log_args = list(args)
        if redact_args:
            for index in redact_args:
                if 0 <= index < len(log_args):
                    log_args[index] = "***"

        logger.info("run: %s", shlex.join(log_args))

        if self.dry_run:
            logger.info("dry_run=True, skipping execution")
            return CommandResult(
                args=list(args),
                returncode=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.0,
            )

        start = time.monotonic()
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            shell=False,
            check=False,
        )
        elapsed = time.monotonic() - start

        logger.debug(
            "rc=%d elapsed=%.2fs stdout=%r", result.returncode, elapsed, result.stdout[:200]
        )
        if result.stderr:
            logger.debug("stderr=%r", result.stderr[:200])

        return CommandResult(
            args=list(args),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_seconds=elapsed,
        )
