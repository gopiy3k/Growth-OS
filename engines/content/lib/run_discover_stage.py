"""Content Engine - Producer (discovery).

This is the engine PRODUCER, not a Stage. It:
  - discovers candidate work from configured sources (RSS/Atom feeds),
  - deduplicates by canonical `source_url` across queue + runs + dlq,
  - ENQUEUES first-stage `score` jobs only.

It NEVER claims, completes, fails, or writes `content_engine_runs` rows. Those are
Stage responsibilities. The Producer produces; it does not process. This is enforced
by the frozen Engine Operating Model (Producer boundary).

Credentials (SUPABASE_*) come from the worker environment (loaded by the cron host).
No model/provider/auth here - discovery is deterministic, so no AI Runtime call.

The Producer reads its source list from `config/sources.json` (engine-owned config).
Phase 4 will fold this into the engine Policy; the file is the minimal testable seam.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from typing import Optional

# Shared EOS client (single source of truth).
_HERE = os.path.realpath(__file__)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import eos_queue as ce_queue  # noqa: E402

ENGINE = "content"
FIRST_STAGE = "score"

# --------------------------------------------------------------------------
# Source list (engine-owned config; Phase 4 moves this into Policy)
# --------------------------------------------------------------------------
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "config")
_SOURCES_FILE = os.path.join(_CONFIG_DIR, "sources.json")


def load_sources() -> list[dict]:
    """Return configured sources: [{"id": str, "url": str}, ...]."""
    if not os.path.exists(_SOURCES_FILE):
        return []
    with open(_SOURCES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("sources", [])


# --------------------------------------------------------------------------
# Feed parsing (stdlib only - no new dependency)
# --------------------------------------------------------------------------
def _text(el: Optional[ET.Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(raw: str) -> list[dict]:
    """Parse RSS or Atom into items: [{"source_url", "title", "content"}, ...]."""
    items: list[dict] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items

    # RSS: <item><link/><title/><description/></item>
    for item in root.iter("item"):
        link = _text(item.find("link")) or _text(item.find("guid"))
        if not link:
            continue
        items.append({
            "source_url": link,
            "title": _text(item.find("title")),
            "content": _text(item.find("description")),
        })

    # Atom: <entry><link href/><title/><summary/></entry>
    if not items:
        ns = {}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            link = ""
            for l in entry.findall("{http://www.w3.org/2005/Atom}link"):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href") or ""
                    break
            if not link:
                continue
            items.append({
                "source_url": link,
                "title": _text(entry.find("{http://www.w3.org/2005/Atom}title")),
                "content": _text(entry.find("{http://www.w3.org/2005/Atom}summary")),
            })
    return items


# --------------------------------------------------------------------------
# Discover + dedup + enqueue (Producer behavior only)
# --------------------------------------------------------------------------
def discover_once() -> dict:
    """Run one discovery pass. Returns a small report dict (for evidence)."""
    import requests  # available in worker runtime; local import keeps import surface minimal

    sources = load_sources()
    enqueued = 0
    skipped_dup = 0
    discovered = 0
    feed_errors = 0

    for src in sources:
        url = src.get("url")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "GrowthOS-Producer/1.0"})
            resp.raise_for_status()
            items = parse_feed(resp.text)
        except Exception as e:  # noqa: BLE001
            feed_errors += 1
            print(f"[discover] feed error {url}: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        for it in items:
            surl = it.get("source_url")
            if not surl:
                continue
            discovered += 1
            if ce_queue.is_source_known(surl):
                skipped_dup += 1
                continue
            ce_queue.enqueue(ENGINE, FIRST_STAGE, {
                "source_url": surl,
                "title": it.get("title", ""),
                "content": it.get("content", ""),
            })
            enqueued += 1

    report = {
        "sources": len(sources),
        "feed_errors": feed_errors,
        "discovered": discovered,
        "enqueued": enqueued,
        "skipped_duplicate": skipped_dup,
    }
    print(f"[discover] {report}")
    return report


def main() -> int:
    try:
        discover_once()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[discover] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
