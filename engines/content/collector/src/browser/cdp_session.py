"""Low-level CDP WebSocket session (IMPLEMENTATION DETAIL — not part of the
public adapter interface).

Correlates request/response by numeric id and buffers CDP events. The collector
never touches this directly; only `cdp_adapter.CdpBrowserAdapter` uses it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional


class CdpError(Exception):
    """A CDP command returned an error result."""


class CdpSession:
    def __init__(self, uri: str, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.uri = uri
        self._ws = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._events: list[dict] = []
        self._listener: Optional[asyncio.Task] = None
        self._loop = loop

    async def connect(self) -> None:
        import websockets  # local import keeps the dependency tight to this detail

        self._ws = await websockets.connect(self.uri)
        self._listener = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            data = json.loads(message)
            msg_id = data.get("id")
            if msg_id is not None and msg_id in self._pending:
                fut = self._pending.pop(msg_id)
                if not fut.done():
                    fut.set_result(data)
            else:
                # CDP event (method/params) — buffer for optional consumers.
                self._events.append(data)

    async def send(
        self, method: str, params: Optional[dict] = None, *, timeout: float = 30.0,
        session_id: Optional[str] = None,
    ) -> dict:
        if self._ws is None:
            raise CdpError("session not connected")
        msg_id = self._next_id
        self._next_id += 1
        msg: dict = {"id": msg_id, "method": method, "params": params or {}}
        if session_id is not None:
            msg["sessionId"] = session_id
        loop = self._loop or asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(json.dumps(msg))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise CdpError(f"timeout waiting for {method} (id={msg_id})") from None
        if "error" in resp:
            raise CdpError(f"{method} failed: {resp['error']}")
        return resp.get("result", {})

    def drain_events(self) -> list[dict]:
        evs = self._events
        self._events = []
        return evs

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
