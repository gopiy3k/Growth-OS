"""Core identity primitives for the Grok Trend Intelligence Collector.

Implements Amendment 1 (deterministic, idempotent collection_id) and
Amendment 3 (mandatory provenance) from COLLECTOR-DESIGN-001 v1.

No browser, no I/O, no side effects. Pure functions only — unit-testable
without spending Grok quota.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

COLLECTOR_VERSION = "1.0.0"
RUNTIME_VERSION = "ADR-027"
SOURCE = "grok"
ENDPOINT = "https://x.com/i/grok"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: Optional[datetime] = None) -> str:
    return (dt or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date(dt: Optional[datetime] = None) -> str:
    return (dt or utc_now()).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class RecordKey:
    """Exactly-once evidence key (Amendment 1).

    A raw/normalized evidence record is uniquely identified by the tuple
    (collection_id, prompt_id, prompt_version). A second write with the same
    key is a no-op (or versioned append) — never a duplicate row.
    """

    collection_id: str
    prompt_id: str
    prompt_version: str

    def to_dict(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
        }

    def to_filename(self) -> str:
        return f"{self.prompt_id}@{self.prompt_version}.json"


def compute_collection_id(
    prompt_id: str,
    prompt_version: str,
    collection_label: str,
    collection_date: Optional[str] = None,
) -> str:
    """Deterministic collection_id (Amendment 1).

    Derived from defining inputs, NOT from process/clock randomness:
        sha256(prompt_id + ":" + prompt_version + ":" + date + ":" + label)

    Same inputs on the same UTC calendar day -> identical id. A collector
    restart produces the same id, so resume markers + duplicate detection
    guarantee exactly-once evidence preservation.

    Args:
        prompt_id: prompt identifier from the Prompt Registry.
        prompt_version: prompt version from the Prompt Registry.
        collection_label: identifies the scheduled collection (e.g.
            "daily-trend-scan").
        collection_date: UTC YYYY-MM-DD; defaults to today (UTC).
    """
    date = collection_date or utc_date()
    seed = f"{prompt_id}:{prompt_version}:{date}:{collection_label}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def compute_prompt_hash(rendered_prompt: str) -> str:
    """SHA-256 of the exact rendered prompt text (for reproducibility)."""
    return hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()


def build_provenance(
    collection_id: str,
    prompt_id: str,
    prompt_version: str,
    conversation_id: str,
    collected_at: Optional[str] = None,
    browser_session_id: Optional[str] = None,
) -> dict:
    """Mandatory provenance block (Amendment 3).

    All ten fields are required. Downstream systems MUST NOT process a record
    missing any of them.
    """
    return {
        "collection_id": collection_id,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "conversation_id": conversation_id,
        "browser_session_id": browser_session_id,
        "collected_at": collected_at or utc_iso(),
        "collector_version": COLLECTOR_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "source": SOURCE,
        "endpoint": ENDPOINT,
    }


def provenance_is_complete(prov: dict) -> bool:
    """Validate that all mandatory provenance fields are present.

    Amendment 3: all ten fields are required. `browser_session_id` may be null
    (when the Chrome browser-context id is not available), but the key MUST be
    present. All other fields must be non-null.
    """
    required = {
        "collection_id",
        "prompt_id",
        "prompt_version",
        "conversation_id",
        "browser_session_id",
        "collected_at",
        "collector_version",
        "runtime_version",
        "source",
        "endpoint",
    }
    if not required.issubset(prov.keys()):
        return False
    nullable = {"browser_session_id"}
    for k in required:
        if k in nullable:
            continue
        if prov.get(k) is None:
            return False
    return True


__all__ = [
    "COLLECTOR_VERSION",
    "RUNTIME_VERSION",
    "SOURCE",
    "ENDPOINT",
    "RecordKey",
    "compute_collection_id",
    "compute_prompt_hash",
    "build_provenance",
    "provenance_is_complete",
    "utc_now",
    "utc_iso",
    "utc_date",
]
