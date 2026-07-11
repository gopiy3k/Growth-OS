"""Increment 3 — Collection Orchestrator package (public interface).

Exposes the orchestrator and its result/configuration types. The orchestrator
consumes ONLY: BrowserAdapter (+ exceptions), PromptRegistry, Identity, ResumeState.
No storage / normalization / OD / scheduler logic here (Increment 4+).

PO Increment 3 amendments honored:
  #1 conversation_id optional (browser_metadata)  -> see collection_result
  #2 explicit CollectionStatus enum               -> see collection_result
  #3 policy separated into CollectorConfig         -> see config
  #4 endpoint read from config (default grok)     -> see config
"""

from __future__ import annotations

from .collection_result import (
    CollectionResult,
    CollectionStatus,
    PromptRef,
    RawEvidenceRecord,
)
from .config import CollectorConfig
from .collector import GrokCollector

__all__ = [
    "GrokCollector",
    "CollectionResult",
    "CollectionStatus",
    "PromptRef",
    "RawEvidenceRecord",
    "CollectorConfig",
]
