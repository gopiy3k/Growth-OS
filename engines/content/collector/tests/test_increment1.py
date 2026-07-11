"""Unit tests for Increment 1 (no browser, no Grok quota).

Validates Amendment 1 (deterministic id + idempotency), Amendment 2 (externalized
prompt registry), Amendment 3 (mandatory provenance), and §10 exactly-once storage.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.identity import (  # noqa: E402
    RecordKey,
    build_provenance,
    compute_collection_id,
    compute_prompt_hash,
    provenance_is_complete,
)
from core.resume_state import PromptStatus, ResumeState  # noqa: E402
from prompt_registry.loader import PromptRegistry  # noqa: E402
from storage.evidence_store import EvidenceStore  # noqa: E402

REGISTRY = (
    Path(__file__).resolve().parents[1] / "docs" / "collector" / "prompts" / "registry.json"
)


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="collector_test_"))


def test_collection_id_deterministic():
    a = compute_collection_id("PROMPT-TREND-SCAN", "1.0.0", "daily-trend-scan", "2026-07-11")
    b = compute_collection_id("PROMPT-TREND-SCAN", "1.0.0", "daily-trend-scan", "2026-07-11")
    c = compute_collection_id("PROMPT-TREND-SCAN", "1.0.0", "daily-trend-scan", "2026-07-12")
    assert a == b, "same inputs -> same id (Amendment 1)"
    assert a != c, "different date -> different id"
    assert len(a) == 64, "sha256 hex"


def test_prompt_hash_stable():
    h1 = compute_prompt_hash("hello world")
    h2 = compute_prompt_hash("hello world")
    assert h1 == h2
    assert compute_prompt_hash("hello world") != compute_prompt_hash("hello worle")


def test_prompt_registry_externalized():
    reg = PromptRegistry(REGISTRY)
    pd = reg.get("PROMPT-TREND-SCAN", "1.0.0")
    assert pd.template  # prompt text lives in registry, not code
    assert pd.prompt_hash == reg.registry_hash_for("PROMPT-TREND-SCAN", "1.0.0")
    # collector asserts rendered hash matches registry (tamper check)
    assert pd.render_hash() == pd.prompt_hash


def test_provenance_complete():
    prov = build_provenance("cid", "PROMPT-TREND-SCAN", "1.0.0", "conv123")
    assert provenance_is_complete(prov), "all 10 mandatory fields present"
    assert prov["source"] == "grok"
    assert prov["endpoint"] == "https://x.com/i/grok"
    assert prov["runtime_version"] == "ADR-027"
    # missing field -> incomplete
    incomplete = dict(prov)
    del incomplete["conversation_id"]
    assert not provenance_is_complete(incomplete)


def test_resume_markers_idempotent():
    tmp = _tmp()
    state = ResumeState("coll-abc", state_dir=tmp)
    assert not state.is_completed("PROMPT-TREND-SCAN", "1.0.0")
    state.mark("PROMPT-TREND-SCAN", "1.0.0", PromptStatus.COMPLETED)
    assert state.is_completed("PROMPT-TREND-SCAN", "1.0.0")
    # simulate restart: new ResumeState on same collection_id
    state2 = ResumeState("coll-abc", state_dir=tmp)
    assert state2.is_completed("PROMPT-TREND-SCAN", "1.0.0"), "restart sees completed"


def test_exactly_once_raw_write():
    tmp = _tmp()
    store = EvidenceStore(base_dir=tmp)
    key = RecordKey("coll-xyz", "PROMPT-TREND-SCAN", "1.0.0")
    rec1 = {"record_key": key.to_dict(), "raw_response": "FIRST"}
    rec2 = {"record_key": key.to_dict(), "raw_response": "SECOND"}
    p1 = store.write_raw(key, "2026-07-11", rec1)
    p2 = store.write_raw(key, "2026-07-11", rec2)
    assert p1 == p2, "same key -> same path (exactly-once)"
    with p1.open(encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["raw_response"] == "FIRST", "second write did not overwrite"
    assert store.exists(key, "2026-07-11")


def test_normalized_idempotent_and_od_intake():
    tmp = _tmp()
    store = EvidenceStore(base_dir=tmp)
    key = RecordKey("coll-xyz", "PROMPT-TREND-SCAN", "1.0.0")
    rec = {"record_key": key.to_dict(), "items": [{"index": 1, "text": "x"}]}
    store.write_normalized(key, "2026-07-11", rec)
    store.write_normalized(key, "2026-07-11", rec)  # duplicate attempt
    norm_file = tmp / "normalized" / "2026-07-11.jsonl"
    lines = [l for l in norm_file.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 1, "normalized record not duplicated"
    store.write_od_intake("2026-07-11", rec)
    od_file = tmp / "od_intake" / "2026-07-11.jsonl"
    assert od_file.exists()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
