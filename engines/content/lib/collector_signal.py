"""Collector Intake Signal Collector (Opportunity Discovery Engine).

This is a *reporter*, not a decision-maker (doc 20 §2 / design §3): it collects EVIDENCE of
editorial opportunities from the Grok Trend Intelligence Collector's intake drop-zone and
hands it to the existing Producer enqueue path. It does NOT filter, rank, or curate — the
collector already passes ALL findings (COLLECTOR-DESIGN-001 §16) and selection is the `score`
stage's job.

The collector (frozen) emits §9 normalized records to:
    <intake_dir>/<YYYY-MM-DD>.jsonl
each line carrying `raw_evidence_ref` so OD / EB can audit the untransformed source.

This module converts each well-formed intake record into a Source-item dict shaped exactly
like the RSS/feed items the Producer already consumes, so it can flow through the SAME dedup /
freshness / enqueue logic in the Opportunity Discovery driver (run_opportunity_discovery.py).

Mechanism only — no EOS/DB/policy change. It returns Source items; the OD driver enqueues them
for the `score` stage via the frozen eos_queue contract. This is the canonical read-only bridge
from the collector drop-zone into the frozen Content Engine spine (ADR-024/026), with zero
modification to the collector, Browser Runtime, EOS, or downstream modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

# Canonical default intake dir: mirrors the collector's DEFAULT_OD_INTAKE_DIR
# (engines/content/collector/src/core/od_intake.py). Resolved relative to the repo root so
# this module needs no import of the frozen collector package.
# __file__ = <repo>/engines/content/lib/collector_signal.py
#   .parent        = <repo>/engines/content/lib
#   .parents[1]    = <repo>/engines/content   <-- collector lives here
_DEFAULT_INTAKE_DIR = (
    Path(__file__).resolve().parents[1]
    / "collector"
    / "data"
    / "opportunity-intake"
)


def _intake_dir() -> Path:
    env = os.environ.get("COLLECTOR_INTAKE_DIR")
    if env:
        return Path(env)
    return _DEFAULT_INTAKE_DIR


def _read_intake_records(intake_dir: Path) -> list[dict]:
    """Read every <date>.jsonl in intake_dir. Returns the parsed §9 records in file order."""
    if not intake_dir.exists():
        return []
    records: list[dict] = []
    for path in sorted(intake_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Defensive: a malformed line cannot be audited/deduped; skip it.
                        # The collector is trusted to emit well-formed records (design §16).
                        continue
        except OSError:
            continue
    return records


def _first_text(sections: Optional[list[dict]]) -> str:
    if not sections:
        return ""
    for s in sections:
        body = (s.get("body") or "").strip()
        if body:
            # Prefer the first section heading as the title if present.
            heading = (s.get("heading") or "").strip()
            if heading:
                return heading
    # Fall back to the first non-empty body chunk as a title.
    for s in sections:
        body = (s.get("body") or "").strip()
        if body:
            return body[:200]
    return ""


def _body_text(sections: Optional[list[dict]]) -> str:
    if not sections:
        return ""
    return "\n\n".join((s.get("body") or "").strip() for s in sections if (s.get("body") or "").strip())


def _to_source_item(rec: dict) -> Optional[dict]:
    """Convert one §9 normalized intake record into a Producer Source item.

    Returns None if the record is structurally invalid (missing the fields OD needs to
    audit + dedup). Such records are skipped, never fabricated.
    """
    record_key = rec.get("record_key") or {}
    provenance = rec.get("provenance") or {}
    raw_ref = rec.get("raw_evidence_ref")
    if not record_key or not provenance or not raw_ref:
        # Cannot audit source or dedupe -> unsafe to enqueue. Skip.
        return None

    collection_id = str(record_key.get("collection_id", "") or "")
    # raw_evidence_ref is the canonical, raw-auditable, dedup-stable source_url.
    source_url = str(raw_ref)
    title = _first_text(rec.get("sections"))
    content = _body_text(rec.get("sections"))
    if not content:
        # No extractable text -> nothing for the Editorial Brain to score. Skip.
        return None

    published = provenance.get("collected_at")
    # Vendor: tie to the collector collection for diversity tracking (grok -> xai via
    # content_types entity map). Stable across re-runs of the same collection.
    vendor = f"grok:{collection_id[:8]}" if collection_id else "grok"

    item = {
        "title": title,
        "url": source_url,
        "summary": content[:4000],
        "content": content,
        "published": published,
        "vendor": vendor,
        "source_kind": "collector_intake",
        "source_class": "B",  # external, public-verifiable (Grok trends = public X discussion)
        "breaking": False,
        "raw_evidence_ref": raw_ref,
        "record_key": record_key,
        "collector_version": provenance.get("collector_version"),
        "endpoint": provenance.get("endpoint"),
    }
    return item


def collector_intake_items(intake_dir: Optional[Path] = None) -> list[dict]:
    """Return Source items from the collector intake drop-zone, ready for the Producer.

    No opportunity-score gate is applied (design §3): the collector already passed ALL
    findings; selection is the `score` stage's job. Dedup/freshness against the Source window
    is delegated to run_discover_stage (existing logic), exactly like signal_search_geo.
    """
    base = Path(intake_dir) if intake_dir else _intake_dir()
    items: list[dict] = []
    seen: set[str] = set()
    for rec in _read_intake_records(base):
        item = _to_source_item(rec)
        if item is None:
            continue
        url = item.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(item)
    return items


def collect(threshold: float = 0.0, intake_dir: Optional[Path] = None) -> tuple[list[dict], int]:
    """Signal-collector entry point compatible with run_discover_stage's `collect()` calls.

    `threshold` is accepted for interface symmetry but IGNORED (no opportunity gate — design
    §3). Returns (items, raw_count) where raw_count is the number of intake records read.
    """
    base = Path(intake_dir) if intake_dir else _intake_dir()
    raw = _read_intake_records(base)
    items = collector_intake_items(base)
    return items, len(raw)


__all__ = ["collector_intake_items", "collect", "_DEFAULT_INTAKE_DIR"]
