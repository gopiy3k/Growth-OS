"""Q2 — Normalization tests (pure transform + collector emission)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Make the collector src importable.
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from core.normalizer import normalize, NORMALIZED_SCHEMA_VERSION  # noqa: E402


def _raw(text, pid="P1", ver="1.0.0", cid="c1", conv="FAKE123"):
    return {
        "schema_version": "1.0",
        "provenance": {
            "collection_id": cid,
            "prompt_id": pid,
            "prompt_version": ver,
            "conversation_id": conv,
            "collected_at": "2026-07-11T09:00:15Z",
        },
        "record_key": {"collection_id": cid, "prompt_id": pid, "prompt_version": ver},
        "raw_response": text,
    }


def test_normalize_preserves_provenance_verbatim():
    raw = _raw("hello world")
    out = normalize(raw)
    assert out["provenance"] == raw["provenance"]
    assert out["record_key"] == raw["record_key"]
    assert out["confidence"] is None  # §9: never asserted by collector


def test_normalize_does_not_mutate_raw():
    raw = _raw("bullet")
    snapshot = json.dumps(raw, sort_keys=True)
    normalize(raw)
    assert json.dumps(raw, sort_keys=True) == snapshot


def test_normalize_extracts_sections_and_bullets():
    text = (
        "# Market Trends\n"
        "Some preamble text.\n"
        "- Trend A about AI https://example.com/a\n"
        "- Trend B about crypto\n"
        "# Risks\n"
        "1. Risk one\n"
        "2. Risk two\n"
    )
    out = normalize(_raw(text))
    headings = [s["heading"] for s in out["sections"] if s.get("heading")]
    assert headings == ["Market Trends", "Risks"]
    # Both bullet and ordered items captured as flat items with links.
    texts = [i["text"] for i in out["items"]]
    assert any("Trend A" in t for t in texts)
    assert any("Risk one" in t for t in texts)
    links = [lnk for i in out["items"] for lnk in i["embedded_links"]]
    assert "https://example.com/a" in links


def test_normalize_unstructured_text_no_items():
    out = normalize(_raw("Just a paragraph.\nNothing list-like here."))
    assert out["items"] == []
    assert "unstructured" in out["notes"]


def test_normalize_raw_ref_present():
    out = normalize(_raw("x", pid="P1", ver="1.2.0", cid="c9"))
    assert out["raw_evidence_ref"].endswith("evidence/2026-07-11/c9/P1@1.2.0.json")


def test_normalize_rejects_missing_identity():
    with pytest.raises(ValueError):
        normalize({"raw_response": "x"})


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    from orchestrator import CollectorConfig, GrokCollector, PromptRef  # noqa: E402
    import asyncio  # noqa: E402
    import tempfile  # noqa: E402

    # Collector-level: normalization is emitted and re-derived on resume.
    from tests.test_collector import FakeBrowserAdapter, _make_registry  # type: ignore  # noqa: E402

    state_dir = Path(tempfile.mkdtemp())
    store_dir = Path(tempfile.mkdtemp())
    refs = [PromptRef("PROMPT-TREND-SCAN", "1.2.0", {"topic": "AI"})]
    cfg = CollectorConfig(state_dir=state_dir, store_dir=store_dir)
    a = FakeBrowserAdapter()
    col = GrokCollector(a, _make_registry(), cfg, refs, "q2-norm")
    res = asyncio.run(col.run_collection())
    assert res.status.value == "success"
    assert res.normalized_persisted == 1
    from core.evidence_store import EvidenceStore  # noqa: E402

    norm = EvidenceStore(store_dir).normalized_for(col.collection_id)
    assert len(norm) == 1
    assert norm[0]["schema_version"] == NORMALIZED_SCHEMA_VERSION
    print("Q2 collector emission OK:", len(norm), "normalized artifact(s)")
