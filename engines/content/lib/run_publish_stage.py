"""Content Engine - 'publish' stage Worker driver (TERMINAL Stage).

Run by the EOS Worker. Performs the full EOS loop for ONE job:
  claim -> publish (real Buffer call) -> complete (persist declared Outcome) or fail (DLQ).

This is the TERMINAL stage of the Content Engine. Its successful `complete()` writes the
`content_engine_runs` record that represents the engine's declared Business Capability
Outcome: PUBLISHED content (a real Buffer update id on LinkedIn and/or X).

It reuses the EXACT same EOS execution spine as score/generate (engines/_shared/eos_queue.py)
- proof that one engine can host multiple independent stages with ZERO EOS changes. No queue,
no EOS, no Runtime redesign.

The Publish Stage calls the AI Runtime? No. Publishing is an external system action, not
reasoning. The reasoning (draft generation) already happened in the 'generate' stage. Here we
only act on the draft and verify the external result.

Credentials: SUPABASE_* + BUFFER_* from the worker environment. Never printed.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# EOS shared queue client lives at engines/_shared/ (single source of truth).
# run_publish_stage.py is at engines/content/lib/ -> up 3 levels to engines/, then _shared.
# Buffer client lives in the SAME lib dir as this driver.
_HERE = os.path.realpath(__file__)
_LIB = os.path.dirname(_HERE)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "_shared")
for _p in (_LIB, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eos_queue as ce_queue  # noqa: E402
import buffer_client  # noqa: E402

ENGINE = "content"
STAGE = "publish"


def build_post(payload: dict) -> str:
    """Compose the publishable post text from the generate-stage draft (engine policy)."""
    title = (payload.get("draft_title") or "").strip()
    body = (payload.get("draft_body") or "").strip()
    parts = [p for p in (title, body) if p]
    return "\n\n".join(parts)


def main() -> int:
    try:
        job = ce_queue.claim(ENGINE, STAGE)
    except Exception as e:  # noqa: BLE001
        print(f"[publish] claim error: {e}", file=sys.stderr)
        return 2

    if job is None:
        print("[publish] queue empty for stage=publish - nothing to do.")
        return 0

    job_id = job["id"]
    payload = job.get("payload_json") or {}
    source_url = payload.get("source_url")
    print(f"[publish] claimed job {job_id} source={source_url}")

    try:
        post_text = build_post(payload)
        # Real Buffer call. Raises on missing token / API error / no update id.
        buf = buffer_client.publish(post_text)
        outcome: dict[str, Any] = {
            "status": "published",
            "text": post_text,
            "updates": buf["updates"],  # real Buffer update ids (LinkedIn/X)
            "published_at": datetime.now(timezone.utc).isoformat(),
            # selection/explainability attribution (ADR-026 observability)
            "category": payload.get("category"),
            "selected_by": payload.get("selected_by"),
            "review_job_id": payload.get("review_job_id"),
        }
        # Verify publication happened (real update ids) BEFORE completing.
        ce_queue.complete(job_id, outcome, source_url=source_url)
        print(f"[publish] completed job {job_id} -> {outcome}")
        return 0
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:500]
        ce_queue.fail(job_id, err)
        print(f"[publish] failed job {job_id}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
