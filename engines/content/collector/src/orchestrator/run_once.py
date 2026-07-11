"""External scheduler entrypoint for the Grok Trend Intelligence Collector.

Increment 4, phase Q6. Design refs: COLLECTOR-DESIGN-001 §11 (scheduling),
IMPLEMENTATION-ROADMAP-RC1 §5 (Q6).

This is a CLEAN EXTERNAL ENTRYPOINT ONLY. It contains NO scheduling logic
(no cadence parsing, no locking, no queue, no retry/retry-policy, no concurrency
control). It does exactly one thing per invocation:

    1. Read a collection spec (prompt registry + which prompt ids/versions,
       a collection label, and policy overrides) from CLI args / env.
    2. Construct the browser adapter (BrowserAdapter ABC — CdpBrowserAdapter
       in production, never raw CDP directly), the CollectorConfig, and the
       GrokCollector.
    3. Run exactly ONE collection (asyncio.run(col.run_collection())).
    4. Emit the structured CollectionResult and exit 0 for SUCCESS/SKIPPED/
       SUSPENDED (all resumable/expected), and non-zero ONLY for FAILED.

Scheduling (cadence, locking, heartbeat, retries) is the caller's job (the
profile's scheduling system per design §11). This module deliberately refuses to
own any of it, preserving the collector subtree boundary and ADR-027.

Run:
    python -m orchestrator.run_once \
        --registry path/to/registry.json \
        --prompts PROMPT-TREND-SCAN@1.2.0 \
        --label daily-trend-scan \
        --cdp-url http://127.0.0.1:9333 \
        [--state-dir DIR] [--store-dir DIR] [--intake-dir DIR] \
        [--quota N] [--endpoint URL] [--conversation-id ID] [--json] [--date DATE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from browser.cdp_adapter import CdpBrowserAdapter
from orchestrator import CollectorConfig, GrokCollector, PromptRef
from orchestrator.collection_result import CollectionStatus
from prompt_registry.loader import PromptRegistry

# Exit codes: 0 = ran to a non-error terminal state (success/skipped/suspended
# are all expected/rvariable); non-zero = FAILED (stop-and-report).
EXIT_FAILED = 2


def _parse_prompt_refs(spec: str, registry: PromptRegistry) -> list[PromptRef]:
    """Parse "ID@VERSION[,ID@VERSION...]" into PromptRefs, validating each
    exists in the registry (fail fast before opening a browser tab)."""
    refs: list[PromptRef] = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "@" not in item:
            raise ValueError(f"prompt spec must be ID@VERSION, got: {item!r}")
        pid, ver = item.split("@", 1)
        pid, ver = pid.strip(), ver.strip()
        # Validate the registry knows this id@version (renders + hashes).
        # registry.get raises KeyError on miss; normalize to ValueError so the
        # entrypoint reports a precondition error (not a collector FAILED run).
        try:
            registry.get(pid, ver)
        except KeyError as e:
            raise ValueError(f"prompt not in registry: {pid}@{ver}") from e
        refs.append(PromptRef(pid, ver, {}))
    if not refs:
        raise ValueError("no prompt refs parsed from --prompts")
    return refs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_once",
        description="Run ONE Grok Trend Intelligence collection (scheduler entrypoint).",
    )
    p.add_argument("--registry", type=Path, default=None,
                   help="Path to prompt registry JSON (defaults to the bundled registry).")
    p.add_argument("--prompts", required=True,
                   help="Comma-separated PROMPT_ID@VERSION entries to collect.")
    p.add_argument("--label", required=True,
                   help="Stable collection label (feeds the deterministic collection_id).")
    p.add_argument("--cdp-url", default="http://127.0.0.1:9333",
                   help="Browser CDP endpoint (production adapter only).")
    p.add_argument("--endpoint", default=None,
                   help="Grok endpoint override (default: ADR-027 frozen x.com/i/grok).")
    p.add_argument("--state-dir", type=Path, default=None,
                   help="Resume-state directory (default: collector data tree).")
    p.add_argument("--store-dir", type=Path, default=None,
                   help="Raw/normalized evidence directory (default: collector data tree).")
    p.add_argument("--intake-dir", type=Path, default=None,
                   help="OD intake drop-zone directory (default: collector data tree).")
    p.add_argument("--quota", type=int, default=None,
                   help="Per-run quota ceiling (prompts collected). None = unbounded.")
    p.add_argument("--conversation-id", default=None,
                   help="Override conversation_id when the runtime supplies it out-of-band.")
    p.add_argument("--date", default=None,
                   help="Collection date (YYYY-MM-DD) for deterministic collection_id.")
    p.add_argument("--json", action="store_true",
                   help="Emit the full CollectionResult as JSON on stdout.")
    return p


async def _run(args: argparse.Namespace) -> CollectionStatus:
    registry = PromptRegistry(args.registry)
    refs = _parse_prompt_refs(args.prompts, registry)
    config = CollectorConfig(
        endpoint=args.endpoint or CollectorConfig.endpoint,  # ADR-027 default
        state_dir=args.state_dir,
        store_dir=args.store_dir,
        intake_dir=args.intake_dir,
        quota_limit=args.quota,
        conversation_id=args.conversation_id,
    )
    # Production browser abstraction ONLY — never raw CDP.
    adapter = CdpBrowserAdapter(cdp_url=args.cdp_url)
    collector = GrokCollector(adapter, registry, config, refs, args.label, args.date)
    return await collector.run_collection()


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (ValueError, FileNotFoundError) as e:
        # Config/registry/precondition errors are operational, not a collector
        # FAILED — report and exit non-zero so the scheduler can alert.
        print(f"run_once: precondition error: {e}", file=sys.stderr)
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001 — surface any unexpected failure to caller
        print(f"run_once: unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_FAILED

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            f"collection_id={result.collection_id} status={result.status.value} "
            f"completed={result.prompts_completed} skipped={result.prompts_skipped} "
            f"normalized={result.normalized_persisted} od_emitted={result.od_emitted}"
        )
        if result.error:
            print(f"error: {result.error}", file=sys.stderr)

    # 0 for any non-FAILED terminal state (success/skipped/suspended are all
    # resumable/expected). Non-zero only on FAILED.
    return 0 if result.status != CollectionStatus.FAILED else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
