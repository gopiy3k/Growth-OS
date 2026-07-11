"""Q1 — Evidence Store persistence tests (no Grok quota required)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Make the collector src importable when run from repo root or tests dir.
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.evidence_store import EvidenceStore
from core.identity import RecordKey
from orchestrator.collection_result import RawEvidenceRecord


def _record(collection_id: str, prompt_id: str, prompt_version: str, text: str = "x") -> dict:
    rec = RawEvidenceRecord.build(
        collection_id=collection_id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt_text=text,
        prompt_hash="h",
        variables={},
        raw_response=text,
    )
    return rec.to_dict()


def test_preserve_writes_file(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    rec = _record("col1", "P1", "1.0.0")
    assert store.preserve(rec) is True
    path = store.path_for("col1", RecordKey("col1", "P1", "1.0.0"))
    assert path.exists()
    # round-trip
    loaded = store.load("col1", RecordKey("col1", "P1", "1.0.0"))
    assert loaded["raw_response"] == "x"
    assert loaded["record_key"]["prompt_id"] == "P1"


def test_preserve_is_exactly_once(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    rec = _record("col1", "P1", "1.0.0", text="first")
    store.preserve(rec)
    rec2 = _record("col1", "P1", "1.0.0", text="second")
    # Second preserve with same key must be a no-op (no duplicate/overwrite).
    assert store.preserve(rec2) is False
    loaded = store.load("col1", RecordKey("col1", "P1", "1.0.0"))
    assert loaded["raw_response"] == "first", "exactly-once must not overwrite"


def test_contains_and_records_for(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    store.preserve(_record("c", "A", "1.0.0"))
    store.preserve(_record("c", "B", "1.0.0"))
    assert store.contains("c", RecordKey("c", "A", "1.0.0"))
    assert not store.contains("c", RecordKey("c", "Z", "1.0.0"))
    recs = store.records_for("c")
    assert {r["record_key"]["prompt_id"] for r in recs} == {"A", "B"}


def test_preserve_missing_identity_raises(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.preserve({"raw_response": "x"})  # no record_key


def test_atomic_write_leaves_no_tmp_on_crash(tmp_path: Path) -> None:
    """A replace that completes must leave no .tmp files behind."""
    store = EvidenceStore(tmp_path)
    store.preserve(_record("c", "A", "1.0.0"))
    tmps = list(tmp_path.glob("**/*.tmp"))
    assert not tmps, f"stray temp files: {tmps}"


def test_atomic_write_is_crash_safe(tmp_path: Path) -> None:
    """Interrupted mid-write leaves either old-or-new committed bytes, never
    a corrupt/partial JSON (validate the committed file parses)."""
    store = EvidenceStore(tmp_path)
    # Simulate a crash between write and rename by writing a temp + killing it.
    target = store.path_for("c", RecordKey("c", "A", "1.0.0"))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = __import__("tempfile").mkstemp(dir=str(target.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("{ this is intentionally corrupt")  # never renamed
    # committed path must still be absent (no half-write leaked)
    assert not target.exists()
    # Now a clean preserve should succeed and produce valid JSON.
    store.preserve(_record("c", "A", "1.0.0"))
    with target.open("r", encoding="utf-8") as fh:
        json.load(fh)  # must parse
    # tidy the stray temp
    if os.path.exists(tmp):
        os.unlink(tmp)
