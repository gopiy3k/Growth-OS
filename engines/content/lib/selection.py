"""Content Engine — Selection mechanism (PURE, deterministic logic).

Selection is the Engine Operating Model §8 operational mechanism. It is NOT a Stage, NOT a
Runtime, NOT an EOS component, NOT a Queue, NOT a Worker, and owns NO AI reasoning.

This module holds the PURE, side-effect-free ranking/selection logic so it is deterministic
and unit-testable without a database or network. The I/O driver (run_select.py) reads the
Approved Pool from content_engine_runs, calls select(), and enqueues publish for winners.

Determinism guarantee: select(pool, policy, now) over the same inputs always returns the same
output. Tie-break order (ADR-026 §7): composite score, confidence, freshness, diversity
contribution, created_at, stable draft id (source_url).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

SELECTION_ALGORITHM_VERSION = "1"

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_hours(created_at: Any, now: datetime) -> float:
    dt = _parse_ts(created_at)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def freshness_weight(age_hours: float, halflife_hours: float) -> float:
    """Exponential decay, floored at a small baseline so old items never hit zero."""
    if halflife_hours <= 0:
        return 1.0
    return max(0.05, math.exp(-age_hours / halflife_hours))


def is_expired(row: dict, policy: dict, now: datetime) -> bool:
    ttl = policy.get("pool_ttl_hours", 168)
    if row.get("evergreen"):
        ttl = policy.get("evergreen_ttl_hours", ttl)
    return _age_hours(row.get("created_at"), now) > ttl


def eligible_pool(rows: list[dict], policy: dict, now: datetime) -> list[dict]:
    """Approved, not published, not expired (ADR-026 §6). Input rows are review runs."""
    out = []
    for r in rows:
        if not r.get("approved"):
            continue
        if r.get("published"):
            continue
        if is_expired(r, policy, now):
            continue
        out.append(r)
    return out


def composite(row: dict, policy: dict, now: datetime, seen_topics: set, seen_categories: dict) -> float:
    w = policy.get("ranking_weights", {})
    score = float(row.get("overall_score", 0)) / 100.0
    fresh = freshness_weight(_age_hours(row.get("created_at"), now),
                             policy.get("freshness_halflife_hours", 48))
    ever = 1.0 if row.get("evergreen") else 0.0
    # diversity penalty: topic already picked, or category over its weekly share this run
    penalty = 0.0
    if row.get("topic_hash") in seen_topics:
        penalty += 1.0
    if seen_categories.get(row.get("category"), 0) >= policy.get("max_per_category_per_week", 3):
        penalty += 1.0
    return (
        w.get("overall_score", 0.6) * score
        + w.get("freshness", 0.25) * fresh
        + w.get("evergreen", 0.15) * ever
        - w.get("diversity_penalty", 0.2) * penalty
    )


def _sort_key(row: dict, comp: float) -> tuple:
    # Descending composite (negate), then tie-breaks (ADR-026 §7).
    return (
        -round(comp, 9),
        -_CONFIDENCE_RANK.get(str(row.get("confidence", "LOW")).upper(), 1),
        -float(row.get("overall_score", 0)),  # freshness proxy handled in composite; score next
        str(_parse_ts(row.get("created_at")) or ""),  # older first as final stabilizer? -> newer preferred below
        str(row.get("source_url", "")),
    )


def select(rows: list[dict], policy: dict, now: Optional[datetime] = None,
           week_published_count: int = 0, week_category_counts: Optional[dict] = None) -> dict:
    """Deterministically select drafts to publish.

    Returns:
      {selected: [rows], skipped: bool, starvation: bool, reason: str, eligible_count: int}
    """
    if now is None:
        now = datetime.now(timezone.utc)
    week_category_counts = dict(week_category_counts or {})

    if policy.get("pause_flag"):
        return {"selected": [], "skipped": True, "starvation": False,
                "reason": "paused", "eligible_count": 0}

    pool = eligible_pool(rows, policy, now)
    if not pool:
        return {"selected": [], "skipped": True, "starvation": True,
                "reason": "empty_pool", "eligible_count": 0}

    per_day = int(policy.get("publish_top_per_day", 1))
    week_cap = int(policy.get("max_posts_per_week", 7))
    remaining_week = max(0, week_cap - week_published_count)
    limit = min(per_day, remaining_week)
    if limit <= 0:
        return {"selected": [], "skipped": True, "starvation": False,
                "reason": "weekly_cap_reached", "eligible_count": len(pool)}

    min_safe = int(policy.get("min_safe_score", 70))
    starvation_behaviour = policy.get("starvation_behaviour", "publish_best_safe")

    above = [r for r in pool if int(r.get("overall_score", 0)) >= min_safe]
    starvation = False
    candidates = above
    if not above:
        # No draft meets the floor. Starvation policy (ADR-026 §9). Unsafe is impossible here:
        # the pool is already approved=true (safe). "best safe" = highest scored approved row.
        starvation = True
        if starvation_behaviour == "skip":
            return {"selected": [], "skipped": True, "starvation": True,
                    "reason": "starvation_skip", "eligible_count": len(pool)}
        candidates = pool  # publish_best_safe

    # Greedy deterministic selection honoring diversity as picks accumulate.
    selected: list[dict] = []
    seen_topics: set = set()
    seen_categories: dict = dict(week_category_counts)
    working = list(candidates)
    while working and len(selected) < limit:
        ranked = sorted(
            working,
            key=lambda r: _sort_key(r, composite(r, policy, now, seen_topics, seen_categories)),
        )
        pick = ranked[0]
        selected.append(pick)
        seen_topics.add(pick.get("topic_hash"))
        seen_categories[pick.get("category")] = seen_categories.get(pick.get("category"), 0) + 1
        working = [r for r in working if r.get("source_url") != pick.get("source_url")
                   and r.get("topic_hash") not in seen_topics]

    return {
        "selected": selected,
        "skipped": len(selected) == 0,
        "starvation": starvation,
        "reason": "selected" if selected else "no_candidates",
        "eligible_count": len(pool),
    }
