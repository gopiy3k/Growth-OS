"""Shared test helpers for the collector test-suite.

Importable as ``tests._helpers`` (the tests directory is on sys.path via each
test module's ROOT bootstrap). Kept dependency-light so every collector test
can build a FakeBrowserAdapter + in-memory registry without a real browser.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Ensure src is importable when this helper is imported first.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
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
from orchestrator import CollectorConfig, GrokCollector, PromptRef  # noqa: E402
from prompt_registry.loader import PromptDef, PromptRegistry  # noqa: E402

# Known persistent user tab (from runtime verification, ADR-027).
KNOWN_USER_TAB = "54F5F3236B58EF13AC23ADAD415FCF38"
USER_URL = "https://x.com/i/grok?conversation=USER-SEED"

FAKE_PROMPT_ID = "PROMPT-TREND-SCAN"
FAKE_PROMPT_VER = "1.0.0"


class FakeBrowserAdapter(BrowserAdapter):
    """Subclass of the frozen ABC — must implement every abstract method.

    Returns canned responses and tracks tab state so tests can assert the
    new-automation-tab invariant, lifecycle order, resume, provenance, and
    failure handling without spending Grok quota or opening a browser.
    """

    def __init__(self, *, fail_on: str = "", conv_id: str = "FAKE123") -> None:
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


def _make_registry(ids=("PROMPT-TREND-SCAN",)) -> PromptRegistry:
    """In-memory registry; default holds the bundled prompt id at version 1.0.0."""
    reg = PromptRegistry.__new__(PromptRegistry)
    reg._data = {
        "prompts": [
            {
                "prompt_id": pid,
                "version": "1.0.0",
                "description": "fake",
                "template": "Survey {topic}." if pid != FAKE_PROMPT_ID else (
                    "Survey the last 24 hours of discussions on Grok about AI "
                    "tooling. List distinct emerging trends with evidence."
                ),
                "variables": [] if pid != FAKE_PROMPT_ID else ["topic"],
            }
            for pid in ids
        ]
    }
    return reg


def _collector(adapter: FakeBrowserAdapter, *, label="inc3-collection", refs=None, state_dir=None):
    import tempfile

    cfg = CollectorConfig(state_dir=state_dir if state_dir is not None else Path(tempfile.mkdtemp()))
    refs = refs or [PromptRef(FAKE_PROMPT_ID, FAKE_PROMPT_VER, {"topic": "AI"})]
    return GrokCollector(adapter, _make_registry(), cfg, refs, collection_label=label)
