"""OpenBlade AI assistant: an OpenAI-compatible helper agent.

Connects to any OpenAI-compatible chat-completions endpoint (OpenAI, Ollama,
vLLM, LM Studio, LocalAI, ...) and answers operator questions about library
management, archive/restore workflows, NAS configuration, diagnostics, and
security posture — grounded in live OpenBlade state via read-only tools.

Advisory by default: the assistant never executes state-changing operations and
never advises bypassing OpenBlade's safety gates.
"""

from openblade.assistant.config import AssistantConfig, load_assistant_config
from openblade.assistant.service import AssistantReply, AssistantService

__all__ = [
    "AssistantConfig",
    "AssistantReply",
    "AssistantService",
    "load_assistant_config",
]
