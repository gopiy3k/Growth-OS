"""Unit tests for the Opportunity Discovery collector-intake pipeline.

These tests run against the REAL RC3 collector intake fixture
(engines/content/collector/data/opportunity-intake/2026-07-11.jsonl) — the canonical
upstream input per the OD mission. They verify the reporter (collector_signal) and the
driver (run_opportunity_discovery) preserve evidence fidelity, dedup, and the frozen
eos_queue enqueue contract — with NO modification to frozen modules.

The frozen eos_queue / editorial_memory are mocked so tests need no Supabase.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]  # engines/content
_LIB = _ROOT / "lib"
_SHARED = _ROOT.parent / "_shared"
for _p in (str(_LIB), str(_SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --- mock the FROZEN eos_queue contract (no Supabase in tests) ----------------------------
_CAPTURE = {"enqueued": [], "known": set()}


def _mock_eos_queue():
    mod = types.ModuleType("eos_queue")

    def enqueue(engine, stage, payload, *, max_attempts=3):
        _CAPTURE["enqueued"].append((engine, stage, payload))
        return f"job-{len(_CAPTURE['enqueued'])}"

    def is_source_known(url):
        return url in _CAPTURE["known"]

    mod.enqueue = enqueue
    mod.is_source_known = is_source_known
    return mod


def _mock_editorial_memory():
    mod = types.ModuleType("editorial_memory")
    mod.start_cycle = lambda *a, **k: "cycle-test"
    mod.record_candidates = lambda cid, topics: None
    return mod


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    _CAPTURE["enqueued"].clear()
    _CAPTURE["known"].clear()
    sys.modules["eos_queue"] = _mock_eos_queue()
    sys.modules["editorial_memory"] = _mock_editorial_memory()
    # Reload the OD modules so they pick up the mocks.
    for name in ("collector_signal", "run_opportunity_discovery"):
        sys.modules.pop(name, None)
    import collector_signal as cs  # noqa: F401
    import run_opportunity_discovery as rod  # noqa: F401
    yield
    sys.modules.pop("eos_queue", None)
    sys.modules.pop("editorial_memory", None)


REAL_INTAKE = _ROOT / "collector" / "data" / "opportunity-intake"


def test_reporter_reads_real_rc3_fixture():
    import collector_signal as cs
    items = cs.collector_intake_items(REAL_INTAKE)
    assert len(items) == 11, f"expected 11 RC3 records, got {len(items)}"
    for it in items:
        assert it["url"], "every item needs a stable source_url for dedup"
        assert it["raw_evidence_ref"], "raw_evidence_ref must be carried for audit"
        assert it["record_key"], "record_key must be carried"
        assert it["content"], "items must carry extractable content"


def test_reporter_skips_malformed_records():
    import collector_signal as cs
    # A record missing record_key/provenance/raw_ref is not enqueueable.
    bad = {"sections": [{"heading": "x", "body": "y"}]}
    assert cs._to_source_item(bad) is None


def test_driver_enqueues_to_frozen_score_stage():
    import run_opportunity_discovery as rod
    report = rod.discover_once(REAL_INTAKE)
    assert report["raw"] == 11
    assert report["enqueued"] == 11
    assert report["dropped_dup"] == 0
    # All enqueued via the frozen contract.
    for engine, stage, _payload in _CAPTURE["enqueued"]:
        assert engine == "content"
        assert stage == "score"


def test_driver_preserves_evidence_fidelity():
    import run_opportunity_discovery as rod
    rod.discover_once(REAL_INTAKE)
    payload = _CAPTURE["enqueued"][0][2]
    # Evidence fidelity: auditable back to the raw collector output.
    assert payload["raw_evidence_ref"]
    assert payload["record_key"]
    assert payload["collector_version"] == "1.0.0"
    assert payload["endpoint"] == "https://x.com/i/grok"
    assert payload["source_kind"] == "collector_intake"
    # No opportunity gate is applied (design §3).
    assert payload["opportunity_score"] is None
    assert payload["content_type"] is None


def test_driver_is_idempotent_via_dedup():
    import run_opportunity_discovery as rod
    rod.discover_once(REAL_INTAKE)
    # Mark all as known (simulating prior successful runs).
    for _engine, _stage, payload in _CAPTURE["enqueued"]:
        _CAPTURE["known"].add(payload["source_url"])
    report2 = rod.discover_once(REAL_INTAKE)
    assert report2["enqueued"] == 0
    assert report2["dropped_dup"] == 11


def test_driver_empty_intake_is_safe():
    import run_opportunity_discovery as rod
    empty = _ROOT / "collector" / "data" / "_nonexistent_intake"
    report = rod.discover_once(empty)
    assert report["raw"] == 0
    assert report["enqueued"] == 0
