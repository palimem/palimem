from __future__ import annotations

import re

_INJECTION_BLOCK_RE = re.compile(
    r"<!--\s*ai-memory:begin\s+injection_id=([^\s>]+)\s*-->.*?<!--\s*ai-memory:end\s+injection_id=\1\s*-->",
    re.DOTALL,
)
_LEGACY_PREFETCH_BLOCK_RE = re.compile(
    r"<ai-memory-prefetch>.*?</ai-memory-prefetch>",
    re.DOTALL,
)

KNOWN_INJECTION_IDS = ("prefetch", "profile", "system_prompt")


def apply_context_fencing(text: str, known_injection_ids: list[str]) -> str:
    """Strip ai-memory injection blocks from capture input (spec §17.4)."""
    if not text:
        return text
    del known_injection_ids
    stripped = _INJECTION_BLOCK_RE.sub("", text)
    return _LEGACY_PREFETCH_BLOCK_RE.sub("", stripped)
