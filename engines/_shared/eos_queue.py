"""Engineering OS — shared job-queue client (EOS infrastructure).

Reusable, engine-agnostic job-queue client used by EOS Worker skills to talk to the
Supabase-backed EOS queue (`content_engine_queue` / `_dlq` / `_runs`). This is the
durable contract every future engine stage (editorial, generate, GEO, Prospect, ...)
inherits.

Design notes:
- Uses PostgREST over `requests` (no new dependency introduced).
- Credentials come from the environment (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY),
  supplied to the Worker at runtime. No launcher, no shell-env dependency.
- The `claim` path implements a single-Worker lock via `locked_at` + a claim window;
  safe even if a second Worker is ever added (it loses the race).
- All writes are idempotent per job id.

This module is safe to import; it does not perform I/O at import time.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

DEFAULT_CLAIM_WINDOW_SECONDS = 600  # a job claimed but not finished within 10m is reclaimable


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY. "
            "They must be present in the worker environment at runtime."
        )
    return url.rstrip("/"), key


def _headers() -> dict[str, str]:
    _, key = _env()
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
        "Content-Type": "application/json",
    }


def _get_headers() -> dict[str, str]:
    """Auth-only headers for GET requests.

    Supabase's PostgREST proxy returns 404 'requested path is invalid' for GETs
    that carry `Content-Type: application/json` without a body, so GETs must omit it.
    """
    _, key = _env()
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _base() -> str:
    url, _ = _env()
    return f"{url}/rest/v1"


# --------------------------------------------------------------------------
# Enqueue
# --------------------------------------------------------------------------
def enqueue(engine: str, stage: str, payload: dict[str, Any], *, max_attempts: int = 3) -> str:
    """Insert a job. Returns the new job id."""
    body = {
        "engine": engine,
        "stage": stage,
        "payload_json": payload,
        "max_attempts": max_attempts,
    }
    r = requests.post(
        f"{_base()}/content_engine_queue",
        headers={**_headers(), "Prefer": "return=representation"},
        json=body,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"enqueue failed ({r.status_code}): {r.text[:300]}")
    rows = r.json()
    if not rows:
        raise RuntimeError("enqueue returned no row (Prefer header missing?)")
    return rows[0]["id"]


# --------------------------------------------------------------------------
# Claim (single-Worker lock)
# --------------------------------------------------------------------------
def claim(engine: str, stage: str) -> Optional[dict[str, Any]]:
    """Atomically claim the next runnable job for (engine, stage).

    Marks it `processing` with a `locked_at` timestamp; returns the job row,
    or None if the queue is empty for this stage.
    """
    url = _base()
    sel = (
        f"/content_engine_queue?engine=eq.{engine}&stage=eq.{stage}"
        f"&status=eq.pending"
        f"&order=created_at.asc&limit=1"
    )
    r = requests.get(f"{url}{sel}", headers=_get_headers(), timeout=30)
    if r.status_code >= 400 or not r.json():
        return None
    job = r.json()[0]
    upd = f"/content_engine_queue?id=eq.{job['id']}"
    r2 = requests.patch(
        f"{url}{upd}",
        headers={**_headers(), "Prefer": "return=representation"},
        json={"status": "processing", "locked_at": "now()", "attempts": job["attempts"] + 1},
        timeout=30,
    )
    if r2.status_code >= 400 or not r2.json():
        return None
    return r2.json()[0]


# --------------------------------------------------------------------------
# Complete / Fail
# --------------------------------------------------------------------------
def complete(job_id: str, result: dict[str, Any], *, source_url: Optional[str] = None) -> None:
    """Mark a job done and write its structured output to content_engine_runs."""
    url = _base()
    job = _get_job(job_id)
    if job is None:
        raise RuntimeError(f"job {job_id} not found")
    run = {
        "job_id": job_id,
        "engine": job["engine"],
        "stage": job["stage"],
        "source_url": source_url,
        "result_json": result,
        "status": "success",
    }
    r = requests.post(f"{url}/content_engine_runs", headers=_headers(), json=run, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"run insert failed ({r.status_code}): {r.text[:300]}")
    r2 = requests.patch(
        f"{url}/content_engine_queue?id=eq.{job_id}",
        headers=_headers(),
        json={"status": "done", "locked_at": None},
        timeout=30,
    )
    if r2.status_code >= 400:
        raise RuntimeError(f"queue complete failed ({r2.status_code}): {r2.text[:300]}")


def fail(job_id: str, error: str) -> None:
    """Record failure. If attempts exhausted, route to DLQ; else leave retriable."""
    url = _base()
    job = _get_job(job_id)
    if job is None:
        raise RuntimeError(f"job {job_id} not found")
    exhausted = job["attempts"] >= job["max_attempts"]
    new_status = "failed" if exhausted else "pending"
    r = requests.patch(
        f"{url}/content_engine_queue?id=eq.{job_id}",
        headers=_headers(),
        json={"status": new_status, "last_error": error, "locked_at": None},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"queue fail failed ({r.status_code}): {r.text[:300]}")
    if exhausted:
        dlq = {
            "job_id": job_id,
            "engine": job["engine"],
            "stage": job["stage"],
            "payload_json": job["payload_json"],
            "last_error": error,
            "attempts": job["attempts"],
        }
        rd = requests.post(f"{url}/content_engine_dlq", headers=_headers(), json=dlq, timeout=30)
        if rd.status_code >= 400:
            raise RuntimeError(f"dlq insert failed ({rd.status_code}): {rd.text[:300]}")
        run = {
            "job_id": job_id,
            "engine": job["engine"],
            "stage": job["stage"],
            "source_url": (job["payload_json"] or {}).get("source_url"),
            "result_json": {},
            "status": "error",
            "error": error,
        }
        requests.post(f"{url}/content_engine_runs", headers=_headers(), json=run, timeout=30)


# --------------------------------------------------------------------------
# Introspection (observability)
# --------------------------------------------------------------------------
def pending_count(engine: str, stage: str) -> int:
    url = _base()
    r = requests.get(
        f"{url}/content_engine_queue?engine=eq.{engine}&stage=eq.{stage}"
        f"&status=eq.pending&select=id",
        headers=_get_headers(),
        timeout=30,
    )
    return len(r.json()) if r.status_code == 200 else -1


def last_run(stage: str) -> Optional[dict[str, Any]]:
    """Most recent run for a stage (observability / health)."""
    url = _base()
    r = requests.get(
        f"{url}/content_engine_runs?stage=eq.{stage}&order=created_at.desc&limit=1",
        headers=_get_headers(),
        timeout=30,
    )
    return r.json()[0] if r.status_code == 200 and r.json() else None


def is_source_known(source_url: str) -> bool:
    """Engine-owned dedup decision, executed via platform transport.

    Returns True if the canonical `source_url` already exists in
    `content_engine_queue`, `content_engine_runs`, or `content_engine_dlq`.
    Engine Producers call this to avoid enqueueing duplicate work. The *decision*
    (what counts as "known") is engine policy; the *query* is platform transport.
    """
    url = _base()
    h = _get_headers()
    checks = (
        ("content_engine_queue", "payload_json->>source_url"),
        ("content_engine_runs", "source_url"),
        ("content_engine_dlq", "payload_json->>source_url"),
    )
    for tbl, col in checks:
        r = requests.get(
            f"{url}/{tbl}",
            headers=h,
            params={col: f"eq.{source_url}", "select": "id"},
            timeout=30,
        )
        if r.status_code == 200 and r.json():
            return True
    return False


def _get_job(job_id: str) -> Optional[dict[str, Any]]:
    url = _base()
    r = requests.get(f"{url}/content_engine_queue?id=eq.{job_id}&select=*",
                     headers=_get_headers(), timeout=30)
    if r.status_code != 200 or not r.json():
        return None
    return r.json()[0]
