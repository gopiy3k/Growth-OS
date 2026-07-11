"""In-memory collection result types for the Grok Trend Intelligence Collector.

Increment 3 scope: the orchestrator produces `CollectionResult` (in-memory
only — no storage/normalization/OD emission yet; those are Increment 4).

This module contains NO browser, NO I/O, NO policy. Pure data + construction
helpers so it is unit-testable without spending Grok quota.

Design refs:
  - COLLECTOR-DESIGN-001 §8 (raw evidence schema), §0.1 (Amendments 1–3).
  - PO Increment 3 amendments: conversation_id is OPTIONAL (carried in
    `browser_metadata`); provenance completeness relaxes conversation_id to
    nullable; `browser_session_id` remains nullable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from core.identity import (
    ENDPOINT,
    COLLECTOR_VERSION,
    RUNTIME_VERSION,
    SOURCE,
    RecordKey,
    build_provenance,
    provenance_is_complete,
    utc_iso,
)


# Amended (PO Inc3 #2): explicit execution status simplifies scheduler/resume.
class CollectionStatus(str, Enum):
    SUCCESS = "success"        # all prompts collected (or all skipped)
    FAILED = "failed"          # interaction/transport/auth failure, stop-and-report
    SKIPPED = "skipped"        # nothing to do (every prompt already completed)
    SUSPENDED = "suspended"    # quota exhausted / resumable — not an error


@dataclass(frozen=True)
class PromptRef:
    """A reference to one prompt in the registry (Amendment 2: externalized)."""

    prompt_id: str
    version: str
    variables: dict = field(default_factory=dict)

    def key(self) -> tuple[str, str]:
        return (self.prompt_id, self.version)


@dataclass
class RawEvidenceRecord:
    """An in-memory, design-§8-shaped raw evidence record.

    Built BEFORE any normalization (which is Increment 4). Carries full
    mandatory provenance (Amendment 3) plus the structural fields from §8.
    `browser_metadata` (PO Inc3 #1) holds runtime-derived facts the adapter
    can optionally supply — conversation_id lives here so the orchestrator
    never assumes a runtime can derive it.
    """

    provenance: dict
    record_key: dict
    prompt: dict
    raw_response: str
    extraction: dict
    browser_metadata: dict = field(default_factory=dict)
    timestamps: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        """Provenance must be complete per Amendment 3 (conversation_id +
        browser_session_id may be null)."""
        return provenance_is_complete(self.provenance)

    def to_dict(self) -> dict:
        return {
            "schema_version": "1.0",
            "provenance": self.provenance,
            "record_key": self.record_key,
            "prompt": self.prompt,
            "raw_response": self.raw_response,
            "browser_metadata": self.browser_metadata,
            "timestamps": self.timestamps,
            "extraction": self.extraction,
        }

    @classmethod
    def build(
        cls,
        *,
        collection_id: str,
        prompt_id: str,
        prompt_version: str,
        prompt_text: str,
        prompt_hash: str,
        variables: dict,
        raw_response: str,
        collected_at: Optional[str] = None,
        browser_metadata: Optional[dict] = None,
        submitted_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        extraction_method: str = "dom_message_block",
        truncated: bool = False,
    ) -> "RawEvidenceRecord":
        provenance = build_provenance(
            collection_id=collection_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            conversation_id=(browser_metadata or {}).get("conversation_id"),
            collected_at=collected_at,
        )
        return cls(
            provenance=provenance,
            record_key=RecordKey(collection_id, prompt_id, prompt_version).to_dict(),
            prompt={
                "prompt_id": prompt_id,
                "version": prompt_version,
                "prompt_text": prompt_text,
                "prompt_hash": prompt_hash,
                "variables": variables,
            },
            raw_response=raw_response,
            browser_metadata=browser_metadata or {},
            timestamps={
                "submitted_at": submitted_at or utc_iso(),
                "completed_at": completed_at or utc_iso(),
                "extracted_at": utc_iso(),
            },
            extraction={
                "method": extraction_method,
                "boundary_detected": True,
                "truncated": truncated,
                "char_count": len(raw_response),
            },
        )


@dataclass
class CollectionResult:
    """The complete in-memory result of one collection run (Amendment 1
    deterministic collection_id; PO Inc3 #2 explicit status)."""

    collection_id: str
    status: CollectionStatus
    records: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: utc_iso())
    finished_at: Optional[str] = None
    prompts_total: int = 0
    prompts_completed: int = 0
    prompts_skipped: int = 0
    error: Optional[str] = None

    def add_record(self, rec: RawEvidenceRecord) -> None:
        assert rec.is_valid(), "cannot add a record with incomplete provenance"
        self.records.append(rec.to_dict())

    def finish(self, status: Optional[CollectionStatus] = None) -> None:
        self.finished_at = utc_iso()
        if status is not None:
            self.status = status

    def to_dict(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "prompts_total": self.prompts_total,
            "prompts_completed": self.prompts_completed,
            "prompts_skipped": self.prompts_skipped,
            "records": self.records,
            "error": self.error,
            "collector_version": COLLECTOR_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "source": SOURCE,
            "endpoint": ENDPOINT,
        }


__all__ = [
    "CollectionStatus",
    "PromptRef",
    "RawEvidenceRecord",
    "CollectionResult",
]
