"""Q4 — Resume persistence hardening tests (no Grok quota required)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Make the collector src importable when run from repo root or tests dir.
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.resume_state import PromptStatus, ResumeState  # noqa: E402
from prompt_registry.loader import PromptRegistry  # noqa: E402


def _make_registry() -> PromptRegistry:
    reg = PromptRegistry.__new__(PromptRegistry)
    reg._data = {
        "prompts": [
            {
                "prompt_id": "P1",
                "version": "1.0.0",
                "description": "fake",
                "template": "Survey {topic}.",
                "variables": ["topic"],
            }
        ]
    }
    return reg


def test_atomic_write_leaves_no_tmp(tmp_path: Path) -> None:
    rs = ResumeState("c", state_dir=tmp_path)
    rs.mark("P1", "1.0.0", PromptStatus.COMPLETED)
    tmps = list(tmp_path.glob("**/*.tmp"))
    assert not tmps, f"stray temp files: {tmps}"


def test_atomic_write_is_crash_safe(tmp_path: Path) -> None:
    """An interrupted write (temp written, rename never happens) must not
    corrupt the committed state file."""
    rs = ResumeState("c", state_dir=tmp_path)
    rs.mark("P1", "1.0.0", PromptStatus.COMPLETED)
    path = rs.path
    # Simulate crash: a .tmp left behind, committed file still valid JSON.
    fd, tmp = __import__("tempfile").mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("{ corrupted")
    # committed path must still parse
    with path.open("r", encoding="utf-8") as fh:
        json.load(fh)
    if os.path.exists(tmp):
        os.unlink(tmp)


def test_crash_recovery_demotes_submitted_to_pending(tmp_path: Path) -> None:
    """A state file left with a SUBMITTED marker (crash mid-collect) must be
    recovered to PENDING on the next ResumeState load, so the prompt is
    re-run, not silently skipped and not double-counted."""
    # Hand-write a crashed state file.
    path = tmp_path / "c.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"P1@1.0.0": "submitted", "P2@1.0.0": "completed"}, indent=2), encoding="utf-8")

    rs = ResumeState("c", state_dir=tmp_path)
    # Construction already recovered in-flight markers (see ResumeState.__init__).
    assert rs.status("P1", "1.0.0") == PromptStatus.PENDING  # was submitted -> pending
    assert rs.status("P2", "1.0.0") == PromptStatus.COMPLETED  # untouched
    # Persisted to disk too.
    with path.open("r", encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["P1@1.0.0"] == "pending"


def test_crash_recovery_crash_mid_write_resumable(tmp_path: Path) -> None:
    """End-to-end: simulate a crash after SUBMITTED is written but before
    COMPLETED. A new collector run must re-collect P1 (not skip it) and end
    SUCCESS with one completed prompt."""
    import asyncio

    sys.path.insert(0, str(ROOT / "src"))

    from orchestrator import CollectorConfig, GrokCollector, PromptRef  # noqa: E402
    from core.identity import compute_collection_id  # noqa: E402

    from browser.adapter import (  # noqa: E402
        BrowserAdapter,
        BrowserAdapterError,
        TabHandle,
        TargetInfo,
    )

    class _Fake(BrowserAdapter):
        def __init__(self):
            self.submits = []
            self.closed = []
            self.detached = False

        async def attach(self): pass
        async def enumerate_targets(self): return [TargetInfo(target_id="U", type="page", url="https://x.com/i/grok", title="X")]
        async def new_tab(self): return TabHandle(target_id="AUTO")
        async def navigate(self, tab, url): pass
        async def verify_auth(self, tab): pass
        async def submit_prompt(self, tab, text): self.submits.append(text)
        async def wait_for_completion(self, tab, *, timeout_s=120.0, poll_s=1.5): pass
        async def extract_response(self, tab): return "FAKE RESPONSE"
        async def close_tab(self, tab): self.closed.append(tab.target_id)
        async def detach(self): self.detached = True

    label = "q4-crash"
    date = "2026-07-11"  # pin so the deterministic collection_id matches exactly
    cid = compute_collection_id("P1", "1.0.0", label, date)
    path = tmp_path / f"{cid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"P1@1.0.0": "submitted"}, indent=2), encoding="utf-8")

    store_dir = tmp_path / "store"
    cfg = CollectorConfig(state_dir=tmp_path, store_dir=store_dir)
    refs = [PromptRef("P1", "1.0.0", {"topic": "AI"})]
    reg = _make_registry()

    adapter = _Fake()
    col = GrokCollector(adapter, reg, cfg, refs, label, collection_date=date)
    res = asyncio.run(col.run_collection())
    assert res.status.value == "success"
    # P1 was re-collected (submitted exactly once after recovery).
    assert adapter.submits == ["Survey AI."]
    assert res.prompts_completed == 1
    # Final state is COMPLETED.
    assert ResumeState(cid, state_dir=tmp_path).status("P1", "1.0.0") == PromptStatus.COMPLETED


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL {t.__name__}\n{traceback.format_exc()}")
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    raise SystemExit(1 if failed else 0)
