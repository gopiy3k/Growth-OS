"""Increment 2 — end-to-end live smoke validation against the frozen Browser
Runtime (ADR-027). Drives the REAL CdpBrowserAdapter (no fakes).

This is a validation harness only, not a collector run. It proves the adapter
contract against the live Chrome+CDP runtime:
  - attach succeeds
  - new tab created
  - existing user tabs untouched (assert the known user tab id survives)
  - navigation to https://x.com/i/grok succeeds
  - prompt submission succeeds
  - completion detection succeeds
  - raw response extraction succeeds
  - automation tab closes (and only it)

Run: python tests/e2e_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from browser import CdpBrowserAdapter, TargetInfo  # noqa: E402

CDP_URL = "http://127.0.0.1:9333"
Grok = "https://x.com/i/grok"
# Known persistent user tab (from prior runtime verification).
KNOWN_USER_TAB = "54F5F3236B58EF13AC23ADAD415FCF38"
SMOKE_PROMPT = "Reply with exactly: BROWSER_ADAPTER_E2E_OK"


async def main() -> int:
    adapter = CdpBrowserAdapter(cdp_url=CDP_URL)
    print("[1] attach ...")
    await adapter.attach()
    print("    attach OK")

    before = await adapter.enumerate_targets()
    print(f"[2] targets before: {len(before)} (user tab present={any(t.target_id==KNOWN_USER_TAB for t in before)})")
    assert any(t.target_id == KNOWN_USER_TAB for t in before), "known user tab missing before run"

    print("[3] new automation tab ...")
    tab = await adapter.new_tab()
    print(f"    created {tab.target_id}")

    print(f"[4] navigate -> {Grok} ...")
    await adapter.navigate(tab, Grok)

    print("[5] verify auth ...")
    await adapter.verify_auth(tab)
    print("    auth OK (twid/auth_token present)")

    print(f"[6] submit prompt: {SMOKE_PROMPT!r}")
    await adapter.submit_prompt(tab, SMOKE_PROMPT)

    print("[7] wait for completion ...")
    await adapter.wait_for_completion(tab, timeout_s=120, poll_s=1.5)
    print("    completion detected")

    print("[8] extract raw response ...")
    resp = await adapter.extract_response(tab)
    print(f"    extracted ({len(resp)} chars): {resp!r}")
    assert "BROWSER_ADAPTER_E2E_OK" in resp, "expected marker not found in response"

    print("[9] close ONLY automation tab ...")
    await adapter.close_tab(tab)
    after = await adapter.enumerate_targets()
    assert not any(t.target_id == tab.target_id for t in after), "automation tab still present"
    assert any(t.target_id == KNOWN_USER_TAB for t in after), "USER TAB WAS CLOSED — VIOLATION"
    print("    automation tab closed; user tab intact")

    await adapter.detach()
    print("\nE2E SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
