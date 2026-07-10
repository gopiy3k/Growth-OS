"""Public Browser Adapter interface (Increment 2).

The collector orchestrator depends ONLY on `BrowserAdapter` and the exceptions
defined here. Raw CDP is an implementation detail of `CdpBrowserAdapter`; future
runtimes (Playwright, Browser Use, etc.) implement the same interface and swap in
without touching orchestration.

This layer contains NO collector logic: no prompts, no normalization, no storage,
no scheduling, no OD/EB/Publishing. It is a thin, clean abstraction over the
frozen Browser Runtime (ADR-024).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class BrowserAdapterError(Exception):
    """Base error for adapter-layer failures."""


class AttachError(BrowserAdapterError):
    """Could not connect to / attach the browser runtime."""


class AuthError(BrowserAdapterError):
    """Grok endpoint not authenticated (X cookies missing)."""


class SubmitError(BrowserAdapterError):
    """Prompt submission failed (interaction failure, not a transport glitch)."""


class CompletionTimeout(BrowserAdapterError):
    """Response did not complete within the poll budget."""


class ExtractionError(BrowserAdapterError):
    """Response present but could not be cleanly extracted."""


@dataclass(frozen=True)
class TabHandle:
    """Opaque handle to an automation tab. The collector holds this and passes
    it back; it never inspects target ids or raw CDP identifiers."""

    target_id: str


@dataclass(frozen=True)
class TargetInfo:
    """Enumerated browser target (read-only view)."""

    target_id: str
    type: str
    url: str
    title: str


class BrowserAdapter(ABC):
    """Clean abstraction over the Browser Runtime. Collector orchestrator
    depends only on this interface."""

    @abstractmethod
    async def attach(self) -> None:
        """Attach to the existing CDP endpoint. Raises AttachError on failure."""

    @abstractmethod
    async def enumerate_targets(self) -> list[TargetInfo]:
        """Enumerate browser targets (read-only; used to assert user tabs
        remain untouched)."""

    @abstractmethod
    async def new_tab(self) -> TabHandle:
        """Create a NEW automation tab only. Never touches user tabs."""

    @abstractmethod
    async def navigate(self, tab: TabHandle, url: str) -> None:
        """Navigate the automation tab to `url`."""

    @abstractmethod
    async def verify_auth(self, tab: TabHandle) -> None:
        """Assert Grok is authenticated in this tab. Raises AuthError if not.
        No-ops on success."""

    @abstractmethod
    async def submit_prompt(self, tab: TabHandle, text: str) -> None:
        """Resolve the composer DOM dynamically and submit `text`. Raises
        SubmitError on interaction failure."""

    @abstractmethod
    async def wait_for_completion(
        self, tab: TabHandle, *, timeout_s: float = 120.0, poll_s: float = 1.5
    ) -> None:
        """Poll until the Grok response completes. Raises CompletionTimeout."""

    @abstractmethod
    async def extract_response(self, tab: TabHandle) -> str:
        """Extract the complete raw assistant response for the last prompt.
        Raises ExtractionError on failure."""

    @abstractmethod
    async def close_tab(self, tab: TabHandle) -> None:
        """Close ONLY the given automation tab. Never closes user tabs."""

    @abstractmethod
    async def detach(self) -> None:
        """Release the runtime connection (does not close the browser)."""


__all__ = [
    "BrowserAdapter",
    "BrowserAdapterError",
    "AttachError",
    "AuthError",
    "SubmitError",
    "CompletionTimeout",
    "ExtractionError",
    "TabHandle",
    "TargetInfo",
]
