"""Configuration for the operator assistant, loaded from the environment.

The assistant is off unless ``OPENBLADE_OLLAMA_URL`` is set. "Off" is a first-class
state with a curated explanation, not an error the operator has to decode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "llama3.2"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_ROUNDS = 6

DISABLED_MESSAGE = (
    "The OpenBlade assistant is not configured.\n"
    "\n"
    "Set OPENBLADE_OLLAMA_URL to an Ollama endpoint to enable it:\n"
    "  local   export OPENBLADE_OLLAMA_URL=http://localhost:11434\n"
    "  cloud   export OPENBLADE_OLLAMA_URL=https://ollama.com\n"
    "          export OPENBLADE_OLLAMA_API_KEY=<your key>\n"
    "\n"
    f"Optional: OPENBLADE_OLLAMA_MODEL (default {DEFAULT_MODEL})."
)


@dataclass(frozen=True)
class AssistantConfig:
    """Resolved assistant settings.

    ``base_url is None`` means the feature is disabled.
    """

    base_url: str | None = None
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    docs_dir: Path | None = None

    @property
    def enabled(self) -> bool:
        return self.base_url is not None


def _clean(name: str) -> str | None:
    """Read ``name``; treat an empty/whitespace value as unset."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _positive_float(name: str, default: float) -> float:
    raw = _clean(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(name: str, default: int) -> int:
    raw = _clean(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def default_docs_dir() -> Path:
    """Repository ``docs/`` tree.

    Resolved from this file's location so it works from a checkout, a worktree, or
    an editable install. ``search_docs`` walks whatever is actually there, so a
    wiki landing later under ``docs/wiki/`` is picked up with no code change.
    """
    return Path(__file__).resolve().parents[2] / "docs"


def load_assistant_config() -> AssistantConfig:
    base_url = _clean("OPENBLADE_OLLAMA_URL")
    docs_override = _clean("OPENBLADE_DOCS_DIR")
    return AssistantConfig(
        base_url=base_url.rstrip("/") if base_url else None,
        model=_clean("OPENBLADE_OLLAMA_MODEL") or DEFAULT_MODEL,
        api_key=_clean("OPENBLADE_OLLAMA_API_KEY"),
        timeout_seconds=_positive_float("OPENBLADE_OLLAMA_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        max_rounds=_positive_int("OPENBLADE_ASSISTANT_MAX_ROUNDS", DEFAULT_MAX_ROUNDS),
        docs_dir=Path(docs_override) if docs_override else None,
    )
