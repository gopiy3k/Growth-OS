"""Increment 3 — unit tests for GrokCollector (no Grok quota).

Uses a FakeBrowserAdapter that subclasses the frozen BrowserAdapter ABC, so it
is a verified subtype (import-time check that the orchestrator's dependency
contract is intact). The fake returns canned responses and tracks tab state so
we can assert the new-automation-tab invariant, lifecycle order, idempotency,
resume, provenance completeness, and failure handling.

Run: python engines/content/collector/tests/test_collector.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

# Make the collector src importable when run from repo root or tests dir.
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser.adapter import (  # noqa: E402
    AuthError,
    BrowserAdapter,
    BrowserAdapterError,
    CompletionTimeout,
    ExtractionError,
    SubmitError,
    TabHandle,
    TargetInfo,
)
from core.identity import compute_collection_id  # noqa: E402
from core.resume_state import PromptStatus, ResumeState  # noqa: E402
from prompt_registry.loader import PromptRegistry  # noqa: E402
from orchestrator import (  # noqa: E402
    CollectorConfig,
    CollectionResult,
    CollectionStatus,
    GrokCollector,
    PromptRef,
    RawEvidenceRecord,
)
from core.evidence_store import EvidenceStore  # noqa: E402
from orchestrator.collector import _parse_conversation_id  # noqa: E402

# Known persistent user tab (from runtime verification, ADR-027).
KNOWN_USER_TAB = "54F5F3236B58EF13AC23ADAD415FCF38"
USER_URL = "https://x.com/i/grok?conversation=USER-SEED"

# A fake prompt registry entry used for resolution.
FAKE_PROMPT_ID = "PROMPT-TREND-SCAN"
FAKE_PROMPT_VER = "1.2.0"

# A minimal registry document in memory via a stub PromptRegistry is overkill;
# we monkeypatch a tiny fake registry object with .get() returning a PromptDef.
from prompt_registry.loader import PromptDef  # noqa: E402


class _FakePromptDef(PromptDef):
    pass


def _make_registry() -> PromptRegistry:
    reg = PromptRegistry.__new__(PromptRegistry)
    reg._data = {
        "prompts": [
            {
                "prompt_id": FAKE_PROMPT_ID,
                "version": FAKE_PROMPT_VER,
                "description": "fake",
                "template": "Survey {topic}.",
                "variables": ["topic"],
            }
        ]
    }
    return reg


class FakeBrowserAdapter(BrowserAdapter):
    """Subclass of the frozen ABC — must implement every abstract method."""

    def __init__(self, *, fail_on="", conv_id="FAKE123"):
        self.attached = False
        self.closed_tabs: list[str] = []
        self.detached = False
        self.submits: list[str] = []
        self._tab_counter = 0
        self.fail_on = fail_on  # 'auth'|'submit'|'wait'|'extract'|''
        self.conv_id = conv_id
        self.used_endpoint: Optional[str] = None

    async def attach(self) -> None:
        self.attached = True

    async def enumerate_targets(self) -> list[TargetInfo]:
        # User tab always present and untouched; automation tab added on new_tab.
        items = [TargetInfo(target_id=KNOWN_USER_TAB, type="page", url=USER_URL, title="X")]
        last = getattr(self, "_last_tab_id", None)
        if self._tab_counter > 0 and last:
            url = f"https://x.com/i/grok?conversation={self.conv_id}"
            items.append(TargetInfo(target_id=last, type="page", url=url, title="Grok"))
        return items

    async def new_tab(self) -> TabHandle:
        self._tab_counter += 1
        self._last_tab_id = f"AUTO-{self._tab_counter}"
        return TabHandle(target_id=self._last_tab_id)

    async def navigate(self, tab: TabHandle, url: str) -> None:
        self.used_endpoint = url
        if self.fail_on == "auth":
            raise AuthError("fake auth lost")

    async def verify_auth(self, tab: TabHandle) -> None:
        if self.fail_on == "auth":
            raise AuthError("fake auth lost")

    async def submit_prompt(self, tab: TabHandle, text: str) -> None:
        if self.fail_on == "submit":
            raise SubmitError("fake submit fail")
        self.submits.append(text)

    async def wait_for_completion(self, tab: TabHandle, *, timeout_s=120.0, poll_s=1.5) -> None:
        if self.fail_on == "wait":
            raise CompletionTimeout("fake timeout")

    async def extract_response(self, tab: TabHandle) -> str:
        if self.fail_on == "extract":
            raise ExtractionError("fake empty")
        return "FAKE GROK RESPONSE about AI tooling."

    async def close_tab(self, tab: TabHandle) -> None:
        self.closed_tabs.append(tab.target_id)

    async def detach(self) -> None:
        self.detached = True


def _collector(adapter: FakeBrowserAdapter, *, label="inc3-collection", refs=None, state_dir=None):
    import tempfile
    cfg = CollectorConfig(state_dir=state_dir if state_dir is not None else Path(tempfile.mkdtemp()))
    refs = refs or [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER, {"topic": "AI"})]
    return GrokCollector(adapter, _make_registry(), cfg, refs, collection_label=label)


# ----------------------------- tests -----------------------------

def test_parse_conversation_id():
    assert _parse_conversation_id("https://x.com/i/grok?conversation=ABC") == "ABC"
    assert _parse_conversation_id("https://x.com/i/grok") is None
    assert _parse_conversation_id("not a url") is None


def test_deterministic_collection_id():
    import tempfile
    cfg = CollectorConfig(state_dir=Path(tempfile.mkdtemp()))
    r1 = GrokCollector(FakeBrowserAdapter(), _make_registry(), cfg,
                       [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER)], "label-A")
    r2 = GrokCollector(FakeBrowserAdapter(), _make_registry(), cfg,
                       [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER)], "label-A")
    assert r1.collection_id == r2.collection_id
    # different label -> different id
    r3 = GrokCollector(FakeBrowserAdapter(), _make_registry(), cfg,
                       [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER)], "label-B")
    assert r3.collection_id != r1.collection_id


def test_happy_path_success_and_invariants():
    adapter = FakeBrowserAdapter()
    col = _collector(adapter)
    res: CollectionResult = asyncio.run(col.run_collection())
    assert res.status == CollectionStatus.SUCCESS
    assert res.prompts_total == 1
    assert res.prompts_completed == 1
    assert res.prompts_skipped == 0
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec["provenance"]["conversation_id"] == "FAKE123"
    assert rec["browser_metadata"]["conversation_id"] == "FAKE123"
    assert rec["raw_response"] == "FAKE GROK RESPONSE about AI tooling."
    # invariants
    assert adapter.used_endpoint == "https://x.com/i/grok"
    assert adapter.closed_tabs == [adapter._last_tab_id]
    assert adapter.detached is True
    # user tab never closed
    assert KNOWN_USER_TAB not in adapter.closed_tabs


def test_provenance_complete_on_every_record():
    from core.identity import provenance_is_complete
    adapter = FakeBrowserAdapter()
    res = asyncio.run(_collector(adapter).run_collection())
    for rec in res.records:
        assert provenance_is_complete(rec["provenance"])


def test_idempotent_resume_skips_completed():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = CollectorConfig(state_dir=Path(td))
        ref = PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER, {"topic": "AI"})
        # first run
        a = FakeBrowserAdapter()
        res1 = asyncio.run(GrokCollector(a, _make_registry(), cfg, [ref], "resume-A").run_collection())
        assert res1.status == CollectionStatus.SUCCESS
        assert a.submits == ["Survey AI."]
        # resume: prompt already completed -> skipped, no submit
        b = FakeBrowserAdapter()
        res2 = asyncio.run(GrokCollector(b, _make_registry(), cfg, [ref], "resume-A").run_collection())
        assert res2.status == CollectionStatus.SKIPPED
        assert res2.prompts_skipped == 1
        assert b.submits == []


def test_conversation_id_optional_when_absent():
    from core.identity import provenance_is_complete
    adapter = FakeBrowserAdapter(conv_id="")
    res = asyncio.run(_collector(adapter).run_collection())
    rec = res.records[0]
    assert rec["provenance"]["conversation_id"] is None
    assert rec["browser_metadata"].get("conversation_id") is None
    assert provenance_is_complete(rec["provenance"])


def test_auth_failure_returns_failed_and_cleans_up():
    adapter = FakeBrowserAdapter(fail_on="auth")
    res = asyncio.run(_collector(adapter).run_collection())
    assert res.status == CollectionStatus.FAILED
    assert res.error is not None and "AuthError" in res.error
    # cleanup still happened
    assert adapter.detached is True
    assert len(adapter.closed_tabs) == 1  # automation tab closed in finally


def test_submit_failure_returns_failed():
    adapter = FakeBrowserAdapter(fail_on="submit")
    res = asyncio.run(_collector(adapter).run_collection())
    assert res.status == CollectionStatus.FAILED
    assert "SubmitError" in res.error
    assert adapter.detached is True


def test_wait_timeout_returns_failed():
    adapter = FakeBrowserAdapter(fail_on="wait")
    res = asyncio.run(_collector(adapter).run_collection())
    assert res.status == CollectionStatus.FAILED
    assert "CompletionTimeout" in res.error
    assert adapter.detached is True


def test_extract_failure_returns_failed():
    adapter = FakeBrowserAdapter(fail_on="extract")
    res = asyncio.run(_collector(adapter).run_collection())
    assert res.status == CollectionStatus.FAILED
    assert "ExtractionError" in res.error
    assert adapter.detached is True


def test_config_endpoint_used_not_hardcoded():
    adapter = FakeBrowserAdapter()
    cfg = CollectorConfig(endpoint="https://x.com/i/grok")
    col = GrokCollector(adapter, _make_registry(), cfg,
                        [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER)], "ep-test")
    asyncio.run(col.run_collection())
    assert adapter.used_endpoint == cfg.endpoint


def test_no_policy_hardcoded_in_config_defaults():
    cfg = CollectorConfig()
    assert cfg.endpoint == "https://x.com/i/grok"
    assert cfg.completion_timeout == 120.0
    assert cfg.transport_retry_limit == 3


# ----------------------------- Q1 tests (evidence persistence) -----------------------------

def test_q1_run_persists_records_and_resume_rehydrates():
    """A finished run writes durable raw evidence; a second run with the same
    collection_id (resume) reloads it without recollecting or duplicating."""
    import tempfile

    work = Path(tempfile.mkdtemp())
    state_dir = work / "state"
    store_dir = work / "store"
    cfg = CollectorConfig(state_dir=state_dir, store_dir=store_dir)
    refs = [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER, {"topic": "AI"})]
    label = "q1-persist"

    adapter1 = FakeBrowserAdapter()
    col1 = GrokCollector(adapter1, _make_registry(), cfg, refs, label)
    res1 = asyncio.run(col1.run_collection())
    assert res1.status == CollectionStatus.SUCCESS
    assert res1.prompts_completed == 1

    # Exactly one durable record on disk.
    store = EvidenceStore(store_dir)
    recs = store.records_for(col1.collection_id)
    assert len(recs) == 1
    assert recs[0]["raw_response"].startswith("FAKE GROK RESPONSE")

    # Resume: new adapter (would "recollect" if not skipped), same state+store.
    adapter2 = FakeBrowserAdapter()
    col2 = GrokCollector(adapter2, _make_registry(), cfg, refs, label)
    res2 = asyncio.run(col2.run_collection())
    assert res2.status == CollectionStatus.SKIPPED
    assert res2.prompts_skipped == 1
    assert res2.records_persisted == 1
    # In-memory result still carries the full rehydrated evidence.
    assert len(res2.records) == 1
    assert res2.records[0]["raw_response"].startswith("FAKE GROK RESPONSE")
    # No duplicate file written.
    assert len(store.records_for(col2.collection_id)) == 1
    # The fake adapter never submitted on the resume run (idempotent skip).
    assert adapter2.submits == []


def test_q1_records_persisted_count_on_fresh_run():
    import tempfile

    work = Path(tempfile.mkdtemp())
    cfg = CollectorConfig(
        state_dir=Path(tempfile.mkdtemp()),
        store_dir=work / "store",
    )
    adapter = FakeBrowserAdapter()
    col = GrokCollector(adapter, _make_registry(), cfg,
                        [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER, {"topic": "AI"})], "q1-count")
    res = asyncio.run(col.run_collection())
    assert res.records_persisted == 0  # persisted count tracks resume-rehydrated
    assert len(EvidenceStore(work / "store").records_for(col.collection_id)) == 1


if __name__ == "__main__":
    # minimal test runner (no pytest dependency drift)
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
