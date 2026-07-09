"""Content Engine — Selection mechanism driver (EOM §8 operational mechanism).

This is the I/O driver for Selection. It is NOT a Stage, Runtime, EOS component, Queue, or
Worker, and performs NO AI reasoning (ADR-026 §7). It:
  1. reads the Approved Pool (via pool_client),
  2. computes deterministic selection in pure selection.select(),
  3. enqueues `publish` for winners (idempotently — never re-publishes a source_url),
  4. writes an observability run (stage="select") recording why each draft was
     selected/skipped/expired.

The publish Stage still performs the real Buffer publication (ADR-025). Selection only queues.

Credentials: SUPABASE_* from the worker environment. Never printed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

_HERE = os.path.realpath(__file__)
_LIB = _HERE and os.path.dirname(_HERE)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(_LIB)), "_shared")
for _p in (_LIB, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eos_queue as ce_queue  # noqa: E402
import pool_client  # noqa: E402
import selection as sel  # noqa: E402

ENGINE = "content"
STAGE_SELECT = "select"


def _load_policy() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(_LIB)), "config", "policy.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    try:
        policy = _load_policy()
        now = datetime.now(timezone.utc)

        pool = pool_client.fetch_review_pool()
        published = pool_client.fetch_published_source_urls()
        for row in pool:
            row["published"] = row.get("source_url") in published
        week_count, week_cats = pool_client.published_this_week(now)

        decision = sel.select(pool, policy, now=now,
                              week_published_count=week_count,
                              week_category_counts=week_cats)
        selected = decision["selected"]

        # Observability: record why (ADR-026 observability requirement). Engine-owned write
        # into content_engine_runs via pool_client (does NOT touch the platform eos_queue API).
        pool_client.record_select_run(ENGINE, STAGE_SELECT, {
            "eligible_count": decision.get("eligible_count"),
            "selected_count": len(selected),
            "skipped": decision.get("skipped"),
            "starvation": decision.get("starvation"),
            "reason": decision.get("reason"),
            "selected_source_urls": [r.get("source_url") for r in selected],
            "policy_version": policy.get("policy_version"),
            "selection_algorithm_version": sel.SELECTION_ALGORITHM_VERSION,
        })

        # Enqueue publish for winners (idempotent: already-published excluded by pool_client).
        for r in selected:
            ce_queue.enqueue(ENGINE, "publish", {
                "source_url": r.get("source_url"),
                "draft_title": r.get("draft_title", ""),
                "draft_body": r.get("draft_body", ""),
                "tone": r.get("tone", ""),
                "category": r.get("category", ""),
                "selected_by": "selection",
                "review_job_id": r.get("job_id"),
            })
        print(f"[select] pool={len(pool)} eligible={decision.get('eligible_count')} "
              f"selected={len(selected)} reason={decision.get('reason')} "
              f"starvation={decision.get('starvation')}")
        return 0
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:500]
        print(f"[select] error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
