"""Increment 2 unit tests — BrowserAdapter contract + CDP adapter logic.

No live browser. A FakeCdpSession records sent commands and returns scripted
results, so we validate the adapter's *behavioral contract* (the orchestrator's
only dependency) and that raw CDP is correctly translated:

  - attach resolves ws uri from /json/version and connects
  - new_tab issues Target.createTarget and returns an opaque TabHandle
  - submit_prompt sets the textarea via React setter, locates the button,
    and issues a real mouse click (not Enter)
  - wait_for_completion polls Runtime.evaluate and debounces
  - extract_response returns the last assistant block / body text
  - close_tab closes ONLY the automation target, detaches its session
  - user targets are never closed
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
from browser.cdp_adapter import CdpBrowserAdapter  # noqa: E402
from browser import cdp_session  # noqa: E402


class FakeCdpSession:
    """Scriptable CDP transport standing in for CdpSession over a websocket."""

    def __init__(self, uri):
        self.uri = uri
        self.connected = False
        self.sent = []  # list of (method, params, session_id)
        self._handler = None  # callable(method, params, session_id) -> result dict

    async def connect(self):
        self.connected = True

    async def send(self, method, params=None, *, timeout=30.0, session_id=None):
        self.sent.append((method, params or {}, session_id))
        if self._handler is None:
            return {}
        return self._handler(method, params or {}, session_id)

    def drain_events(self):
        return []

    async def close(self):
        self.connected = False


def install_fake(fake: FakeCdpSession):
    """Monkeypatch CdpSession so the adapter uses our fake transport.
    Patch in both modules because cdp_adapter binds the name at import time."""

    def _factory(uri, *, loop=None):
        return fake

    cdp_session.CdpSession = _factory  # type: ignore
    import browser.cdp_adapter as ca

    ca.CdpSession = _factory  # type: ignore


USER_TAB_ID = "USER_TAB_123"


def base_handler():
    """Default scripted CDP responses for a successful Grok flow."""

    def handler(method, params, session_id):
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"targetId": USER_TAB_ID, "type": "page", "url": "https://x.com/i/grok", "title": "Grok / X"},
                ]
            }
        if method == "Target.createTarget":
            return {"targetId": "AUTO_TAB_1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "SES_1"}
        if method == "Page.enable":
            return {}
        if method == "Page.navigate":
            return {}
        if method == "Network.getCookies":
            return {"cookies": [{"name": "twid"}, {"name": "auth_token"}]}
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression", "")
            # React value-setter call (submit step)
            if "getOwnPropertyDescriptor(proto,'value').set" in expr:
                return {"result": {"value": "SET"}}
            if "getBoundingClientRect" in expr:
                return {"result": {"value": {"x": 100.0, "y": 200.0}}}
            if "animate-spin" in expr:  # completion poll
                return {
                    "result": {
                        "value": {
                            "textareaEmpty": True,
                            "thinking": False,
                            "spin": False,
                        }
                    }
                }
            if "article" in expr:  # extraction
                return {
                    "result": {
                        "value": {
                            "method": "dom",
                            "text": "1. Trend A\n2. Trend B",
                        }
                    }
                }
            return {"result": {"value": True}}
        if method in ("Input.dispatchMouseEvent", "Target.closeTarget", "Target.detachFromTarget"):
            return {}
        return {}

    return handler


async def make_adapter():
    fake = FakeCdpSession("ws://fake")
    install_fake(fake)
    fake._handler = base_handler()
    # attach() reads /json/version via urllib — stub it via a monkeypatched helper
    import urllib.request

    def fake_urlopen(req, timeout=5):
        class _R:
            def read(self):
                return b'{"webSocketDebuggerUrl":"ws://fake"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        a = CdpBrowserAdapter(cdp_url="http://127.0.0.1:9333")
        await a.attach()
    finally:
        urllib.request.urlopen = orig
    a._browser = fake  # ensure fake is used
    return a, fake


def test_interface_is_abstract():
    # cannot instantiate the ABC directly
    try:
        BrowserAdapter()
        assert False, "ABC should be uninstantiable"
    except TypeError:
        pass


def test_tabhandle_opaque():
    th = TabHandle(target_id="X")
    assert th.target_id == "X"
    # orchestrator only passes handles back; it never reads internals directly
    assert isinstance(th, TabHandle)


async def test_attach_and_enumerate():
    a, fake = await make_adapter()
    assert fake.connected
    targets = await a.enumerate_targets()
    assert any(t.target_id == USER_TAB_ID and t.type == "page" for t in targets)


async def test_new_tab_returns_handle_and_calls_createTarget():
    a, fake = await make_adapter()
    th = await a.new_tab()
    assert isinstance(th, TabHandle)
    assert ("Target.createTarget", {"url": "about:blank"}, None) in [
        (m, p, s) for (m, p, s) in fake.sent
    ]


async def test_submit_uses_real_click_not_enter():
    a, fake = await make_adapter()
    th = await a.new_tab()
    await a.submit_prompt(th, "Hello Grok")
    methods = [m for (m, p, s) in fake.sent]
    # React setter evaluated
    assert "Runtime.evaluate" in methods
    # send button located
    loc_exprs = [p.get("expression", "") for (m, p, s) in fake.sent if m == "Runtime.evaluate"]
    assert any("getBoundingClientRect" in e for e in loc_exprs)
    # REAL mouse click issued (not a synthetic .click() DOM call, not Enter key)
    mouse = [p for (m, p, s) in fake.sent if m == "Input.dispatchMouseEvent"]
    assert len(mouse) >= 2  # pressed + released
    assert mouse[0]["type"] == "mousePressed" and mouse[0]["button"] == "left"
    assert mouse[1]["type"] == "mouseReleased"


async def test_wait_for_completion_polls():
    a, fake = await make_adapter()
    th = await a.new_tab()
    await a.wait_for_completion(th, timeout_s=5, poll_s=0.01)
    poll_exprs = [
        p.get("expression", "")
        for (m, p, s) in fake.sent
        if m == "Runtime.evaluate" and "animate-spin" in p.get("expression", "")
    ]
    assert len(poll_exprs) >= 2  # debounced (stable_count >= 2)


async def test_completion_timeout():
    a, fake = await make_adapter()
    # handler always reports "thinking" -> never completes
    fake._handler = lambda method, params, sid: (
        {"result": {"value": {"textareaEmpty": False, "thinking": True, "spin": True}}}
        if method == "Runtime.evaluate" and "animate-spin" in (params or {}).get("expression", "")
        else base_handler()(method, params, sid)
    )
    th = await a.new_tab()
    try:
        await a.wait_for_completion(th, timeout_s=0.2, poll_s=0.01)
        assert False, "should have timed out"
    except CompletionTimeout:
        pass


async def test_extract_response():
    a, fake = await make_adapter()
    th = await a.new_tab()
    text = await a.extract_response(th)
    assert "Trend A" in text


async def test_auth_error_when_cookies_missing():
    a, fake = await make_adapter()
    fake._handler = lambda method, params, sid: (
        {"cookies": [{"name": "guest"}]}
        if method == "Network.getCookies"
        else base_handler()(method, params, sid)
    )
    th = await a.new_tab()
    try:
        await a.verify_auth(th)
        assert False, "should raise AuthError"
    except AuthError:
        pass


async def test_close_tab_only_closes_automation_target():
    a, fake = await make_adapter()
    th = await a.new_tab()
    await a.close_tab(th)
    close_cmds = [p.get("targetId") for (m, p, s) in fake.sent if m == "Target.closeTarget"]
    assert close_cmds == ["AUTO_TAB_1"], "closed target must be the automation tab only"
    # user tab never appears in a close command
    assert USER_TAB_ID not in close_cmds


async def test_detach_releases():
    a, fake = await make_adapter()
    th = await a.new_tab()
    await a.close_tab(th)
    await a.detach()
    assert fake.connected is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        if asyncio.iscoroutinefunction(t):
            asyncio.run(t())
        else:
            t()
        print(f"PASS {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
