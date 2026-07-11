"""Opportunity Discovery — Collector Intake driver (ENGINE-OWNED ingestion).

This is the Opportunity Discovery Engine's ingestion driver for the Grok Trend
Intelligence Collector's opportunity-intake drop-zone (COLLECTOR-DESIGN-001 §16).

It is a STANDALONE driver, mirroring the allowed run_<stage>_stage.py driver
pattern (AGENTS.md §5: "New Stage = reuse eos_queue.enqueue + a run_<stage>_stage.py
driver"). It does NOT live inside the Producer (run_discover_stage.py) and does NOT
modify any frozen module (Collector, Browser Runtime, Discovery, Editorial Brain,
Content Engine, EOS, Publishing).

Pipeline — preserves evidence fidelity, deduplication, and the verification pipeline:
  1. Read §16 intake via collector_signal (a read-only bridge to the FROZEN collector).
  2. Convert each well-formed record to a Source item (collector_signal owns this).
  3. Dedup by source_url against EOS (ce_queue.is_source_known) — the SAME dedup
     boundary the Producer uses.
  4. Enqueue for the `score` stage via the FROZEN eos_queue contract
     (ce_queue.enqueue("content", "score", payload)) — no new Stage/schema.
  5. Open one Editorial Memory cycle for observability parity (non-fatal).

No opportunity gate is applied (design §3): the collector already passes ALL
findings; selection is the `score` stage's job. This driver does not score, rank,
or curate. It only moves auditable evidence from the collector drop-zone into the
existing verification pipeline.

No new external dependencies. Stdlib + the frozen eos_queue / editorial_memory.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

# --- path bootstrap (identical to run_discover_stage.py) --------------------------------
_HERE = os.path.realpath(__file__)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "_shared")
_LIB = os.path.dirname(_HERE)
for _p in (_LIB, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eos_queue as ce_queue  # noqa: E402  (FROZEN EOS contract)
try:
    import editorial_memory as em  # noqa: E402  (observability: cycle record)
except Exception:  # noqa: BLE001  (module is optional PO-WIP; observability is best-effort)
    em = None  # wrappers below already treat any failure (incl. AttributeError) as non-fatal
import collector_signal  # noqa: E402  (read-only bridge to FROZEN collector drop-zone)

ENGINE = "content"
FIRST_STAGE = "score"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_editorial_cycle() -> str:
    """Open an Editorial Memory cycle for observability parity.

    Non-fatal: if Supabase is unconfigured or the write fails, we only warn and return a
    sentinel cycle id so the real ingestion (enqueue) still proceeds. The actual enqueue
    remains cred-gated (AGENTS.md §9); only observability is best-effort.
    """
    try:
        return em.start_cycle()
    except Exception as e:  # noqa: BLE001
        print(f"[od:collector-intake] WARN: editorial memory cycle unavailable "
              f"({type(e).__name__}: {e}); continuing without cycle", file=sys.stderr)
        return f"no-cycle-{uuid.uuid4().hex[:8]}"


def _record_editorial_candidates(cycle_id: str, candidate_topics: list) -> None:
    """Record discovered candidates to Editorial Memory. Non-fatal (observability only)."""
    try:
        em.record_candidates(cycle_id, candidate_topics)
    except Exception as e:  # noqa: BLE001
        print(f"[od:collector-intake] WARN: editorial memory candidate record failed "
              f"({type(e).__name__}: {e})", file=sys.stderr)


def collect_items(intake_dir=None) -> list[dict]:
    """Return Source items from the collector intake drop-zone (delegates to collector_signal).

    Thin wrapper kept for testability / symmetry with the Producer's collector calls.
    """
    return collector_signal.collector_intake_items(intake_dir)


def discover_once(intake_dir=None) -> dict:
    """Ingest the collector's §16 intake into the frozen EOS score pipeline.

    Returns a report dict: {enqueued, raw, dropped_dup, cycle_id, items:[...]}.
    """
    raw_items = collect_items(intake_dir)
    raw_count = len(raw_items)

    now = _now()
    cycle_id = _open_editorial_cycle()  # observability parity; non-fatal if Supabase absent

    enqueued = 0
    dropped_dup = 0
    candidate_topics = []
    items_out = []
    for it in raw_items:
        url = it.get("url") or ""
        if not url:
            # No stable source_url -> cannot audit/dedup. Skip (never fabricate).
            continue
        if ce_queue.is_source_known(url):
            dropped_dup += 1
            continue  # already processed in a prior run (same dedup boundary as Producer)

        title = (it.get("title") or "")[:300]
        content = (it.get("content") or it.get("summary") or "")[:4000]
        payload = {
            "cycle_id": cycle_id,
            "source_url": url,
            "title": title,
            "content": content,
            "content_type": None,            # Score assigns the AIVIS content_type
            "source_class": it.get("source_class", "B"),  # external, public-verifiable
            "vendor": it.get("vendor"),
            "breaking": bool(it.get("breaking", False)),
            "source_kind": "collector_intake",
            "opportunity_score": None,       # no gate applied (design §3)
            "published": it.get("published"),
            "discovered_at": now.isoformat(),
            # --- evidence fidelity: auditable back to the raw collector output ---
            "raw_evidence_ref": it.get("raw_evidence_ref"),
            "record_key": it.get("record_key"),
            "collector_version": it.get("collector_version"),
            "endpoint": it.get("endpoint"),
        }
        ce_queue.enqueue(ENGINE, FIRST_STAGE, payload)
        enqueued += 1
        candidate_topics.append({
            "source_url": url,
            "title": title,
            "vendor": it.get("vendor"),
            "breaking": bool(it.get("breaking", False)),
            "freshness": payload.get("published"),
        })
        items_out.append(payload)

    _record_editorial_candidates(cycle_id, candidate_topics)  # non-fatal observability

    report = {
        "raw": raw_count,
        "enqueued": enqueued,
        "dropped_dup": dropped_dup,
        "cycle_id": cycle_id,
        "items": items_out,
    }
    print(f"[od:collector-intake] {report['raw']} raw -> "
          f"{report['enqueued']} enqueued ({report['dropped_dup']} dup)")
    return report


def main() -> int:
    try:
        report = discover_once()
        return 0 if report["enqueued"] >= 0 else 1
    except Exception as e:  # noqa: BLE001
        print(f"[od:collector-intake] FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
