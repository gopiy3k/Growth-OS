"""Minimal Growth OS consumer — demonstrates API/event-only integration.

A *consumer* (a product, a script, another service) never touches Growth OS tables
directly and never shares the platform's service-role key. It enqueues work through the
public queue API (Supabase PostgREST) and reads results from `content_engine_runs`.

This file is illustrative: fill `.env` (SUPABASE_URL + a role-scoped key) and run:

    python examples/minimal-consumer/example_consumer.py

No model, provider, or runtime name appears here — that is the platform's concern.
"""

from __future__ import annotations

import os
import sys

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")  # a role-scoped key, NOT the service-role key
ENGINE, STAGE = "content", "score"


def _hdr() -> dict:
    if not SUPABASE_URL or not KEY:
        sys.exit("Set SUPABASE_URL and SUPABASE_KEY in .env (role-scoped, not service-role).")
    return {"Authorization": f"Bearer {KEY}", "apikey": KEY, "Content-Type": "application/json"}


def enqueue(payload: dict) -> str:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/content_engine_queue",
        headers={**_hdr(), "Prefer": "return=representation"},
        json={"engine": ENGINE, "stage": STAGE, "payload_json": payload, "max_attempts": 3},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]["id"]


def read_result(job_id: str) -> dict | None:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/content_engine_runs?job_id=eq.{job_id}&select=*",
        headers=_hdr(), timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


if __name__ == "__main__":
    jid = enqueue({
        "title": "Open-source AI agent automates brand visibility",
        "content": "A new agent framework ships a visibility scoring plugin.",
        "source_url": "https://example.com/agent-release",
        "source_type": "rss",
    })
    print(f"enqueued job {jid}")
    print("poll content_engine_runs for this job_id to read the structured result")
    print("example:", read_result(jid))
