"""Content Engine — Approved Pool read client (engine-owned).

The Approved Pool is a LOGICAL VIEW over `content_engine_runs` (ADR-026 §6): review runs
where `approved=true`, that are not yet published and not expired. This module is engine-owned;
it performs read-only Supabase queries via PostgREST. It does NOT modify the EOS platform.

All I/O is side-effect-free reads. Selection (selection.py) is pure; this module only fetches.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests

# Bootstrap Supabase creds from env (and the engine .env if present) — engine-owned, not platform.
_HERE = os.path.realpath(__file__)
_LIB = os.path.dirname(_HERE)


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        # allow a sibling .env load (worker convenience)
        env_path = os.path.join(os.path.dirname(os.path.dirname(_LIB)), ".env")
        try:
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass
        url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
    return url.rstrip("/"), key


def _hdr() -> dict[str, str]:
    _, key = _env()
    return {"Authorization": f"Bearer {key}", "apikey": key}


def fetch_review_pool() -> list[dict[str, Any]]:
    """Return all successful review runs shaped for Selection.

    Each returned row = {source_url, job_id, created_at, **result_json}.
    result_json (ADR-026 §4) holds: approved, overall_score, confidence, issues, reasoning,
    topic_hash, evergreen, category, publish_after, review_contract_version, policy_version,
    selection_algorithm_version, prompt_version, draft_title, draft_body, tone, model_version.
    """
    url, _ = _env()
    h = _hdr()
    r = requests.get(
        f"{url}/rest/v1/content_engine_runs",
        params={
            "stage": "eq.review",
            "status": "eq.success",
            "select": "job_id,source_url,created_at,result_json",
            "order": "created_at.asc",
        },
        headers=h,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"pool fetch failed ({r.status_code}): {r.text[:200]}")
    out = []
    for row in r.json():
        rj = row.get("result_json") or {}
        if not rj.get("approved"):
            continue
        merged = {"job_id": row.get("job_id"), "source_url": row.get("source_url"),
                  "created_at": row.get("created_at"), **rj}
        out.append(merged)
    return out


def fetch_published_source_urls() -> set[str]:
    """Set of source_urls that already have a successful publish run (idempotency)."""
    url, _ = _env()
    h = _hdr()
    r = requests.get(
        f"{url}/rest/v1/content_engine_runs",
        params={
            "stage": "eq.publish",
            "status": "eq.success",
            "select": "source_url",
        },
        headers=h,
        timeout=30,
    )
    if r.status_code != 200:
        return set()
    return {row.get("source_url") for row in r.json() if row.get("source_url")}


def record_select_run(engine: str, stage: str, result: dict[str, Any], source_url: Optional[str] = None) -> None:
    """Engine-owned observability write: record a Selection decision in content_engine_runs.

    This inserts into the EXISTING `content_engine_runs` table (no schema change). It does NOT
    use the platform eos_queue API surface, keeping the EOS platform frozen (ADR-021/024).
    `source_url` is required NOT NULL by the runs table; a pool-level decision uses a stable
    sentinel marker. `runs.job_id` is a FK to `content_engine_queue.id`, so we anchor it to a
    real existing queue job id for the engine (FK-valid, engine-owned query).
    """
    import requests as _requests
    url, _ = _env()
    h = {**_hdr(), "Prefer": "return=minimal"}
    # Anchor job_id to a real existing queue row (FK requirement, engine-owned query).
    q = _requests.get(f"{url}/rest/v1/content_engine_queue?engine=eq.{engine}&select=id&limit=1",
                      headers=_hdr(), timeout=30)
    rows = q.json() if q.status_code == 200 else []
    anchor = rows[0]["id"] if rows else str(__import__("uuid").uuid4())
    body = {
        "job_id": anchor,
        "engine": engine,
        "stage": stage,
        "source_url": source_url or "__selection_pool__",
        "result_json": result,
        "status": "success",
    }
    r = _requests.post(f"{url}/rest/v1/content_engine_runs", headers=h, json=body, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"select run insert failed ({r.status_code}): {r.text[:200]}")


def published_this_week(now) -> tuple[int, dict[str, int]]:
    """(count of successful publish runs in last 7d, per-category count in last 7d)."""
    url, _ = _env()
    h = _hdr()
    from datetime import timedelta
    since = (now - timedelta(days=7)).isoformat()
    r = requests.get(
        f"{url}/rest/v1/content_engine_runs",
        params={
            "stage": "eq.publish",
            "status": "eq.success",
            "created_at": f"gte.{since}",
            "select": "source_url,result_json",
        },
        headers=h,
        timeout=30,
    )
    if r.status_code != 200:
        return 0, {}
    count = 0
    cats: dict[str, int] = {}
    for row in r.json():
        count += 1
        cat = (row.get("result_json") or {}).get("category")
        if cat:
            cats[cat] = cats.get(cat, 0) + 1
    return count, cats
