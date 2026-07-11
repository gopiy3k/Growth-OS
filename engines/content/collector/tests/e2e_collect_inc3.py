"""Increment 3 — live end-to-end collection validation (GATED).

Drives the REAL CdpBrowserAdapter (no fakes) through the GrokCollector
lifecycle. Requires a live, authenticated Local Chrome CDP runtime per
ADR-027 (http://127.0.0.1:9333). Spends a small amount of Grok quota (one
prompt). NOT run by the unit suite.

Strengthened cleanup verification (PO Inc3 #5): after the collection, assert
  - the automation tab was destroyed (absent from enumerate_targets),
  - only the original known user tab remains,
  - no orphan automation targets linger (tab count returns to baseline).

Run manually:
  PYTHONPATH=src CDP_URL=http://127.0.0.1:9333 \
    python engines/content/collector/tests/e2e_collect_inc3.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import CdpBrowserAdapter  # noqa: E402
from prompt_registry.loader import PromptRegistry  # noqa: E402
from orchestrator import CollectorConfig, CollectionStatus, GrokCollector, PromptRef  # noqa: E402

CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9333")
# Known persistent user tab (from runtime verification, ADR-027 §13).
KNOWN_USER_TAB = "54F5F3236B58EF13AC23ADAD415FCF38"


async def main() -> int:
    adapter = CdpBrowserAdapter(cdp_url=CDP_URL)
    registry = PromptRegistry()  # seeds from docs/collector/prompts/registry.json

    # Baseline: how many targets exist before we touch anything.
    await adapter.attach()
    baseline = await adapter.enumerate_targets()
    await adapter.detach()
    baseline_ids = {t.target_id for t in baseline}
    assert KNOWN_USER_TAB in baseline_ids, "known user tab missing before run"
    print(f"[baseline] targets={len(baseline_ids)} (user tab present)")

    cfg = CollectorConfig()
    refs = [PromptRef("PROMPT-TREND-SCAN", "1.2.0", {"topic": "AI tooling"})]
    collector = GrokCollector(adapter, registry, cfg, refs, collection_label="inc3-live-e2e")

    print(f"[run] collection_id={collector.collection_id[:12]}…")
    result = await collector.run_collection()

    assert result.status == CollectionStatus.SUCCESS, f"unexpected status: {result.status}"
    assert result.prompts_completed == 1, "expected 1 prompt completed"
    rec = result.records[0]
    assert rec["raw_response"], "raw response is empty"
    assert rec["provenance"]["conversation_id"], "conversation_id not captured"
    assert rec["provenance"]["endpoint"] == cfg.endpoint
    print(f"[ok] collected {len(rec['raw_response'])} chars; conversation_id={rec['provenance']['conversation_id']}")

    # ---- strengthened cleanup verification (PO Inc3 #5) ----
    # Collector already detached; re-attach read-only to inspect targets.
    probe = CdpBrowserAdapter(cdp_url=CDP_URL)
    await probe.attach()
    after = await probe.enumerate_targets()
    await probe.detach()
    after_ids = {t.target_id for t in after}

    # 1. automation tab destroyed (not present)
    assert collector.automation_tab_id not in after_ids, "automation tab still present after run"
    # 2. only the original user tab remains (no orphan automation targets)
    orphan_automation = after_ids - baseline_ids
    assert not orphan_automation, f"orphan automation targets leaked: {orphan_automation}"
    # 3. user tab intact
    assert KNOWN_USER_TAB in after_ids, "USER TAB WAS CLOSED — invariant breach"
    print(f"[cleanup] targets={len(after_ids)}; user tab intact; no orphans; automation tab destroyed")

    print("\nE2E COLLECT INC3: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
