"""Public surface of the Browser Adapter layer (Increment 2).

Import from here to get the interface + the concrete CDP implementation,
plus the typed exceptions/handles the orchestrator depends on. Raw CDP
details live in `cdp_session`/`cdp_adapter` and are NOT re-exported.
"""

from browser.adapter import (
    AttachError,
    AuthError,
    BrowserAdapter,
    BrowserAdapterError,
    CompletionTimeout,
    ExtractionError,
    SubmitError,
    TabHandle,
    TargetInfo,
)
from browser.cdp_adapter import CdpBrowserAdapter

__all__ = [
    "BrowserAdapter",
    "CdpBrowserAdapter",
    "BrowserAdapterError",
    "AttachError",
    "AuthError",
    "SubmitError",
    "CompletionTimeout",
    "ExtractionError",
    "TabHandle",
    "TargetInfo",
]
