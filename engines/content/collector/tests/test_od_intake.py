"""Q5 — OD intake emission tests (contract only; no OD dependency)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Make the collector src importable.
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from core.od_intake import OpportunityIntake  # noqa: E402


def _norm(cid="c1", pid="P1", ver="1.0.0", text="Trend A"):
    return {
        "schema_version": "1.0",
        "record_key": {"collection_id": cid, "prompt_id": pid, "prompt_version": ver},
        "raw_evidence_ref": f"evidence/2026-07-11/{cid}/{pid}@{ver}.json",
        "items": [{"index": 1, "text": text, "embedded_links": []}],
    }


def test_q5_emits_to_dated_jsonl(tmp_path: Path):
    intake = OpportunityIntake(tmp_path)
    n = intake.emit([_norm(pid="P1"), _norm(pid="P2")], date="2026-07-11")
    assert n == 2
    day_file = tmp_path / "2026-07-11.jsonl"
    assert day_file.exists()
    lines = [json.loads(l) for l in day_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0]["record_key"]["prompt_id"] == "P1"


def test_q5_idempotent_no_duplicate_on_re_emit(tmp_path: Path):
    intake = OpportunityIntake(tmp_path)
    intake.emit([_norm(pid="P1"), _norm(pid="P2")], date="2026-07-11")
    # Re-run with same records — should be a no-op (no duplicates).
    n2 = intake.emit([_norm(pid="P1"), _norm(pid="P2")], date="2026-07-11")
    assert n2 == 0
    day_file = tmp_path / "2026-07-11.jsonl"
    lines = [json.loads(l) for l in day_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_q5_passes_all_findings_not_curated(tmp_path: Path):
    # Collector must emit EVERY normalized record, never filter/rank.
    recs = [_norm(pid=f"P{i}") for i in range(5)]
    intake = OpportunityIntake(tmp_path)
    emitted = intake.emit(recs, date="2026-07-11")
    assert emitted == 5
    lines = [
        json.loads(l) for l in (tmp_path / "2026-07-11.jsonl").read_text().splitlines() if l.strip()
    ]
    assert len(lines) == 5


def test_q5_carries_raw_evidence_ref(tmp_path: Path):
    intake = OpportunityIntake(tmp_path)
    intake.emit([_norm(cid="cx", pid="PY", ver="2.0.0")], date="2026-07-11")
    line = json.loads((tmp_path / "2026-07-11.jsonl").read_text().splitlines()[0])
    assert line["raw_evidence_ref"].endswith("evidence/2026-07-11/cx/PY@2.0.0.json")


def test_q5_append_across_dates(tmp_path: Path):
    intake = OpportunityIntake(tmp_path)
    intake.emit([_norm(pid="P1")], date="2026-07-11")
    intake.emit([_norm(pid="P2")], date="2026-07-12")
    assert (tmp_path / "2026-07-11.jsonl").exists()
    assert (tmp_path / "2026-07-12.jsonl").exists()


def test_q5_no_od_import_or_internal_write(tmp_path: Path):
    # Structural guarantee: the intake module must not import OD/discovery libs.
    import inspect
    import core.od_intake as mod

    src = inspect.getsource(mod)
    assert "opportunity_discovery" not in src
    assert "from engines" not in src.replace(" ", "")
    # Emitting must only write into the intake dir, never above it.
    intake = OpportunityIntake(tmp_path)
    intake.emit([_norm()], date="2026-07-11")
    written = list(tmp_path.rglob("*.jsonl"))
    assert len(written) == 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    from orchestrator import CollectorConfig, GrokCollector, PromptRef  # noqa: E402
    import asyncio  # noqa: E402
    import tempfile  # noqa: E402
    from tests.test_collector import FakeBrowserAdapter, _make_registry  # type: ignore  # noqa: E402

    state_dir = Path(tempfile.mkdtemp())
    store_dir = Path(tempfile.mkdtemp())
    intake_dir = Path(tempfile.mkdtemp())
    refs = [PromptRef("PROMPT-TREND-SCAN", "1.2.0", {"topic": "AI"})]
    cfg = CollectorConfig(state_dir=state_dir, store_dir=store_dir, intake_dir=intake_dir)
    col = GrokCollector(FakeBrowserAdapter(), _make_registry(), cfg, refs, "q5-intake")
    res = asyncio.run(col.run_collection())
    assert res.od_emitted == 1
    lines = [l for l in (intake_dir / "2026-07-11.jsonl").read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    print("Q5 collector emission OK: od_emitted =", res.od_emitted)
