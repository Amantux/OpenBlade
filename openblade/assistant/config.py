"""Configuration for the OpenBlade AI assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "You are the OpenBlade Operations Assistant, embedded in a simulator-first "
    "Quantum i3 tape-archive controller. Help operators with library management, "
    "archive/restore and sharding workflows, NAS configuration, diagnostics, and "
    "security posture. You have READ-ONLY tools to inspect live system state — use "
    "them to ground every answer in concrete values rather than guessing.\n\n"
    "Safety rules (never violate):\n"
    "- Never instruct anyone to bypass OpenBlade's safety gates: real-hardware "
    "enablement (OPENBLADE_BACKEND=real + OPENBLADE_REAL_HARDWARE_ENABLED=true), "
    "tape format confirmation (barcode + one-time safety token), or the "
    "unload-while-mounted/dirty protection.\n"
    "- You cannot execute state-changing operations. For destructive actions "
    "(format, erase, unload, real-hardware moves) explain the safe procedure and "
    "the confirmations required, and direct the operator to perform them.\n"
    "- For security questions, be specific about what is and isn't exposed, and "
    "flag risky configuration (default passwords, disabled auth, real hardware on).\n\n"
    "Be concise, precise, and operator-focused."
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AssistantConfig:
    """Resolved assistant settings.

    Reads OPENBLADE_ASSISTANT_* first, then falls back to the conventional
    OPENAI_* variables so a standard OpenAI or local-LLM environment works as-is.
    """

    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    temperature: float
    max_tool_iterations: int
    system_prompt: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def base_host(self) -> str:
        """Host portion of base_url, safe to surface without leaking a key."""
        without_scheme = self.base_url.split("://", 1)[-1]
        return without_scheme.split("/", 1)[0]


def load_assistant_config() -> AssistantConfig:
    base_url = (
        _env("OPENBLADE_ASSISTANT_BASE_URL")
        or _env("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    api_key = _env("OPENBLADE_ASSISTANT_API_KEY") or _env("OPENAI_API_KEY")
    model = _env("OPENBLADE_ASSISTANT_MODEL") or _env("OPENAI_MODEL") or "gpt-4o-mini"
    configured = bool(base_url and model)
    # Off by default: only auto-enable when the operator has explicitly pointed
    # OpenBlade at an endpoint (custom base URL / model / API key), so we never
    # silently call api.openai.com with the fallback defaults.
    explicit_setup = any(
        _env(name)
        for name in (
            "OPENBLADE_ASSISTANT_BASE_URL",
            "OPENBLADE_ASSISTANT_MODEL",
            "OPENBLADE_ASSISTANT_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "OPENAI_API_KEY",
        )
    )

    def _float(name: str, default: float) -> float:
        try:
            return float(_env(name) or default)
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        try:
            return int(_env(name) or default)
        except ValueError:
            return default

    return AssistantConfig(
        enabled=configured and _env_bool("OPENBLADE_ASSISTANT_ENABLED", default=explicit_setup),
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=_float("OPENBLADE_ASSISTANT_TIMEOUT", 60.0),
        temperature=_float("OPENBLADE_ASSISTANT_TEMPERATURE", 0.2),
        max_tool_iterations=max(1, _int("OPENBLADE_ASSISTANT_MAX_TOOL_ITERATIONS", 4)),
        system_prompt=_env("OPENBLADE_ASSISTANT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT,
    )
