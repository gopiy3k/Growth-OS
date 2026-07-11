"""CDP implementation of `BrowserAdapter` (ADR-027 Local Chrome via CDP).

This is the ONLY place raw CDP appears. It uses the technique verified in
ADR-027 §12 (smoke test) and §13 (multi-interaction validation):
  - set textarea value via the React prototype setter + dispatch input event;
  - locate the send button by aria-label and getBoundingClientRect (coords are
    DYNAMIC — resolved per submission, never cached);
  - submit with a REAL mouse click (Input.dispatchMouseEvent), not Enter/
    synthetic click (those failed in validation).

Transport failures (CDP -32601 / timeouts) are retryable; interaction failures
(auth lost, submit fails, extract empty) follow stop-and-report per the PO's
transport-vs-interaction split. This adapter raises the appropriate
BrowserAdapterError subclass for interaction failures; transport glitches are
retried with a bounded backoff.

The collector never sees CDP identifiers except through TabHandle/TargetInfo.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from browser.adapter import (
    AttachError,
    AuthError,
    BrowserAdapter,
    CompletionTimeout,
    ExtractionError,
    SubmitError,
    TabHandle,
    TargetInfo,
)
from browser.cdp_session import CdpError, CdpSession

GROK_URL = "https://x.com/i/grok"
_RETRYABLE = (CdpError, asyncio.TimeoutError, OSError)
_MAX_TRANSPORT_ATTEMPTS = 3


def _require_ok(cond: bool, msg: str, exc_cls=SubmitError):
    if not cond:
        raise exc_cls(msg)


async def _with_transport_retry(coro_factory, *, what: str):
    """Retry a transport-level CDP call. Interaction-level failures (raised as
    BrowserAdapterError subclasses) are NOT retried — they are real."""
    last: Optional[BaseException] = None
    for attempt in range(1, _MAX_TRANSPORT_ATTEMPTS + 1):
        try:
            return await coro_factory()
        except _RETRYABLE as e:  # transient transport glitch
            last = e
            if attempt < _MAX_TRANSPORT_ATTEMPTS:
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
    raise AttachError(f"transport failed after retries ({what}): {last}")


class CdpBrowserAdapter(BrowserAdapter):
    def __init__(self, cdp_url: str = "http://127.0.0.1:9333"):
        self._cdp_url = cdp_url.rstrip("/")
        self._browser: Optional[CdpSession] = None
        self._page_sessions: dict[str, str] = {}  # target_id -> session_id

    # ---- lifecycle ----

    async def attach(self) -> None:
        async def _do():
            import json
            import urllib.request

            with urllib.request.urlopen(f"{self._cdp_url}/json/version", timeout=5) as resp:
                ws_uri = json.loads(resp.read().decode("utf-8"))["webSocketDebuggerUrl"]
            self._browser = CdpSession(ws_uri)
            await self._browser.connect()

        try:
            await _with_transport_retry(_do, what="attach")
        except (AttachError, CdpError) as e:
            raise AttachError(f"could not attach to {self._cdp_url}: {e}") from None

    async def enumerate_targets(self) -> list[TargetInfo]:
        res = await self._browser.send("Target.getTargets")
        return [
            TargetInfo(
                target_id=t.get("targetId", ""),
                type=t.get("type", ""),
                url=t.get("url", ""),
                title=t.get("title", ""),
            )
            for t in res.get("targetInfos", [])
        ]

    async def _page(self, tab: TabHandle) -> str:
        """Return the CDP sessionId for the automation tab (creating it on first
        use). All page-scoped commands are routed with this sessionId over the
        single browser WebSocket."""
        if tab.target_id in self._page_sessions:
            return self._page_sessions[tab.target_id]
        res = await self._browser.send(
            "Target.attachToTarget",
            {"targetId": tab.target_id, "flatten": True},
        )
        session_id = res["sessionId"]
        self._page_sessions[tab.target_id] = session_id
        return session_id

    def _bsend(self, tab: TabHandle, method: str, params=None, **kw):
        """Convenience: page-scoped send routed to the tab's session. Must be
        awaited inside a coroutine (the adapter methods are already async)."""
        sid = self._page_sessions[tab.target_id]
        return self._browser.send(method, params, session_id=sid, **kw)

    async def new_tab(self) -> TabHandle:
        res = await self._browser.send("Target.createTarget", {"url": "about:blank"})
        return TabHandle(target_id=res["targetId"])

    async def _wait_for_composer(self, tab: TabHandle, *, timeout_s: float = 15.0) -> None:
        """Poll until Grok's composer textarea is present (page hydration is
        async; a fixed sleep races). Verified on the live runtime: the textarea
        appears only after the SPA hydrates (3-5s observed)."""
        sid = await self._page(tab)
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            r = await self._browser.send(
                "Runtime.evaluate",
                {
                    "expression": "!!document.querySelector('textarea')",
                    "returnByValue": True,
                },
                session_id=sid,
            )
            if r.get("result", {}).get("value") is True:
                return
            await asyncio.sleep(0.5)
        raise SubmitError("composer textarea never appeared (page did not hydrate)")

    async def navigate(self, tab: TabHandle, url: str) -> None:
        sid = await self._page(tab)
        await self._browser.send("Page.enable", {}, session_id=sid)
        await self._browser.send("Page.navigate", {"url": url}, session_id=sid)
        await self._wait_for_composer(tab)

    async def verify_auth(self, tab: TabHandle) -> None:
        await self._wait_for_composer(tab)
        sid = await self._page(tab)
        cookies = await self._browser.send("Network.getCookies", {}, session_id=sid)
        names = {c.get("name") for c in cookies.get("cookies", [])}
        _require_ok(
            {"twid", "auth_token"} & names,
            "Grok not authenticated: X auth cookies (twid/auth_token) missing",
            AuthError,
        )
        has_composer = await self._browser.send(
            "Runtime.evaluate",
            {"expression": "!!document.querySelector('textarea')", "returnByValue": True},
            session_id=sid,
        )
        _require_ok(
            has_composer.get("result", {}).get("value") is True,
            "composer textarea not found on Grok page",
            AuthError,
        )

    async def submit_prompt(self, tab: TabHandle, text: str) -> None:
        sid = await self._page(tab)
        set_expr = (
            "(function(){"
            "  var ta = document.querySelector('textarea');"
            "  if(!ta) return 'NO_TEXTAREA';"
            "  var proto = Object.getPrototypeOf(ta);"
            "  var setter = Object.getOwnPropertyDescriptor(proto,'value').set;"
            "  setter.call(ta, " + repr(text) + ");"
            "  ta.dispatchEvent(new Event('input',{bubbles:true}));"
            "  return 'SET';"
            "})()"
        )
        r = await self._browser.send(
            "Runtime.evaluate",
            {"expression": set_expr, "returnByValue": True},
            session_id=sid,
        )
        val = r.get("result", {}).get("value")
        _require_ok(val == "SET", f"textarea set failed: {val}", SubmitError)

        loc = await self._browser.send(
            "Runtime.evaluate",
            {
                "expression": (
                    "(function(){"
                    "  var btn = document.querySelector('[aria-label=\"Grok something\"]');"
                    "  if(!btn) return null;"
                    "  var r = btn.getBoundingClientRect();"
                    "  return {x: r.left + r.width/2, y: r.top + r.height/2};"
                    "})()"
                ),
                "returnByValue": True,
            },
            session_id=sid,
        )
        coords = loc.get("result", {}).get("value")
        _require_ok(coords and "x" in coords, "send button not found", SubmitError)

        x, y = coords["x"], coords["y"]
        await self._browser.send(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=sid,
        )
        await self._browser.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=sid,
        )

    async def wait_for_completion(
        self, tab: TabHandle, *, timeout_s: float = 120.0, poll_s: float = 1.5
    ) -> None:
        sid = await self._page(tab)
        deadline = asyncio.get_event_loop().time() + timeout_s
        stable_count = 0
        while asyncio.get_event_loop().time() < deadline:
            done = await self._browser.send(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(function(){"
                        "  var ta = document.querySelector('textarea');"
                        "  var textareaEmpty = !ta || ta.value.trim() === '';"
                        "  var body = document.body ? document.body.innerText : '';"
                        "  var thinking = /Thinking about your request/i.test(body);"
                        "  var spin = !!document.querySelector('[class*=animate-spin],[role=progressbar]');"
                        "  return {textareaEmpty: textareaEmpty, thinking: thinking, spin: spin};"
                        "})()"
                    ),
                    "returnByValue": True,
                },
                session_id=sid,
            )
            v = done.get("result", {}).get("value", {})
            if v.get("textareaEmpty") and not v.get("thinking") and not v.get("spin"):
                stable_count += 1
                if stable_count >= 2:
                    return
            else:
                stable_count = 0
            await asyncio.sleep(poll_s)
        raise CompletionTimeout(f"response not complete after {timeout_s}s")

    async def extract_response(self, tab: TabHandle) -> str:
        sid = await self._page(tab)
        res = await self._browser.send(
            "Runtime.evaluate",
            {
                "expression": (
                    "(function(){"
                    "  var blocks = document.querySelectorAll('[data-testid=conversation] article, article');"
                    "  if(!blocks || blocks.length === 0){"
                    "    var body = document.body ? document.body.innerText : '';"
                    "    return {method:'fallback', text: body};"
                    "  }"
                    "  var last = blocks[blocks.length-1];"
                    "  return {method:'dom', text: last.innerText};"
                    "})()"
                ),
                "returnByValue": True,
            },
            session_id=sid,
        )
        payload = res.get("result", {}).get("value", {})
        text = (payload.get("text") or "").strip()
        _require_ok(len(text) > 0, "extracted response is empty", ExtractionError)
        return text

    async def close_tab(self, tab: TabHandle) -> None:
        sid = self._page_sessions.pop(tab.target_id, None)
        if sid is not None:
            try:
                await self._browser.send("Target.detachFromTarget", {"sessionId": sid})
            except CdpError:
                pass
        await self._browser.send("Target.closeTarget", {"targetId": tab.target_id})
        # Best-effort confirmation: the target should drop from getTargets
        # promptly; tolerate a brief teardown race.
        for _ in range(10):
            try:
                targets = await self._browser.send("Target.getTargets")
                if not any(t.get("targetId") == tab.target_id for t in targets.get("targetInfos", [])):
                    return
            except CdpError:
                return
            await asyncio.sleep(0.2)

    async def detach(self) -> None:
        for sid in self._page_sessions.values():
            try:
                await self._browser.send("Target.detachFromTarget", {"sessionId": sid})
            except CdpError:
                pass
        self._page_sessions.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
