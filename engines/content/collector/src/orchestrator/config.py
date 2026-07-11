"""Collector configuration / policy separation (PO Increment 3 amendment #3, #4).

The orchestrator consumes configuration rather than embedding policy. Anything
that could reasonably change per deployment — endpoint, timeouts, retry limits,
quota ceilings — lives here, NOT in GrokCollector. This keeps the orchestrator
stable across runtime/endpoint/policy evolution.

Amendment #4: the canonical endpoint is frozen by ADR-027 TODAY
(https://x.com/i/grok) but is read from config so a future endpoint change is a
config edit, not a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Frozen by ADR-027 §6.1 — the ONLY authorized Grok endpoint. Kept as the
# default so the collector never silently uses grok.com.
DEFAULT_ENDPOINT = "https://x.com/i/grok"
DEFAULT_COMPLETION_TIMEOUT = 120.0
# Transport retry limit is a policy input; the adapter owns its own internal
# transport-retry loop, so this is advisory for orchestrator-level concerns.
DEFAULT_TRANSPORT_RETRY_LIMIT = 3


@dataclass
class CollectorConfig:
    """Deployment policy for one collection run. No logic — data only."""

    endpoint: str = DEFAULT_ENDPOINT
    completion_timeout: float = DEFAULT_COMPLETION_TIMEOUT
    transport_retry_limit: int = DEFAULT_TRANSPORT_RETRY_LIMIT
    quota_limit: Optional[int] = None
    # Where resume markers live; defaults into the collector data tree.
    state_dir: Optional[Path] = None
    # Override conversation_id when the runtime supplies it out-of-band.
    conversation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.completion_timeout <= 0:
            raise ValueError("completion_timeout must be > 0")
        if self.transport_retry_limit < 0:
            raise ValueError("transport_retry_limit must be >= 0")


__all__ = ["CollectorConfig", "DEFAULT_ENDPOINT"]
