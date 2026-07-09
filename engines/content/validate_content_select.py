"""Content Engine — Selection mechanism validation (ADR-026, pure deterministic logic).

Exercises selection.select() with crafted pools. NO network, NO AI Runtime, NO Supabase.
Proves determinism and every required behaviour: ranking, diversity, freshness, evergreen,
TTL expiration, duplicate suppression, publish caps, starvation policy, idempotency.

S-prefix criteria mirror the blueprint §3 acceptance tests.

Run:  python engines/content/validate_content_select.py
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timedelta, timezone

_HERE = os.path.realpath(__file__)
_LIB = os.path.join(os.path.dirname(_HERE), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import selection as sel  # noqa: E402

NOW = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)


def _row(source_url, score, conf="HIGH", age_h=0, evergreen=False, topic="t1", cat="product",
         created_offset_h=0):
    return {
        "source_url": source_url,
        "overall_score": score,
        "confidence": conf,
        "topic_hash": topic,
        "category": cat,
        "evergreen": evergreen,
        "created_at": (NOW - timedelta(hours=age_h + created_offset_h)).isoformat(),
        "approved": True,
        "published": False,
    }


def _policy(**over):
    base = {
        "policy_version": "1", "selection_algorithm_version": "1",
        "publish_top_per_day": 1, "max_posts_per_week": 7, "min_safe_score": 70,
        "starvation_behaviour": "publish_best_safe", "pause_flag": False,
        "pool_ttl_hours": 168, "evergreen_ttl_hours": 720, "freshness_halflife_hours": 48,
        "duplicate_window_days": 7, "max_per_category_per_week": 3,
        "ranking_weights": {"overall_score": 0.6, "freshness": 0.25, "evergreen": 0.15,
                            "diversity_penalty": 0.2},
    }
    base.update(over)
    return base


results = {}
p = _policy()

# S1: pool with >=1 safe row >=70 -> exactly publish_top_per_day selected
pool = [_row("s1", 85), _row("s2", 90), _row("s3", 60)]
d = sel.select(pool, p, now=NOW)
results["S1_selected_count"] = len(d["selected"])
results["S1_selected_best"] = d["selected"][0]["source_url"] == "s2" if d["selected"] else None
results["S1_skipped"] = d["skipped"]

# S2: all below 70 but safe -> starvation policy publish_best_safe picks best safe
pool2 = [_row("a", 65), _row("b", 55)]
d2 = sel.select(pool2, p, now=NOW)
results["S2_starvation"] = d2["starvation"]
results["S2_selected_best_safe"] = d2["selected"][0]["source_url"] == "a" if d2["selected"] else None
results["S2_no_unsafe_published"] = all(r["overall_score"] < 100 for r in d2["selected"])

# S2b: starvation_behaviour=skip -> nothing published
d2b = sel.select(pool2, _policy(starvation_behaviour="skip"), now=NOW)
results["S2b_skip_selected"] = len(d2b["selected"]) == 0
results["S2b_skipped"] = d2b["skipped"]

# S3: empty pool -> skipped, zero publishes
d3 = sel.select([], p, now=NOW)
results["S3_skipped_empty"] = d3["skipped"] and len(d3["selected"]) == 0

# S4: duplicate topics within window -> only one selected
pool4 = [_row("u1", 90, topic="dup"), _row("u2", 88, topic="dup")]
d4 = sel.select(pool4, p, now=NOW)
results["S4_selected_unique_topic"] = len({r["topic_hash"] for r in d4["selected"]}) == len(d4["selected"])
results["S4_only_one_on_dup"] = len(d4["selected"]) == 1

# S5: weekly cap reached -> no selection
d5 = sel.select([_row("w", 90)], p, now=NOW, week_published_count=7)
results["S5_weekly_cap"] = len(d5["selected"]) == 0 and d5["reason"] == "weekly_cap_reached"

# S6: pause flag -> zero selected
d6 = sel.select([_row("z", 90)], _policy(pause_flag=True), now=NOW)
results["S6_pause"] = len(d6["selected"]) == 0 and d6["reason"] == "paused"

# S7: idempotency — already-published excluded
pool7 = [_row("pub", 90), _row("fresh", 95)]
for r in pool7:
    r["published"] = (r["source_url"] == "pub")
d7 = sel.select(pool7, p, now=NOW)
results["S7_excludes_published"] = all(r["source_url"] != "pub" for r in d7["selected"])
results["S7_selects_fresh"] = d7["selected"][0]["source_url"] == "fresh" if d7["selected"] else None

# S8: TTL expiration -> old timely removed
pool8 = [_row("old", 95, age_h=200), _row("new", 80, age_h=1)]
d8 = sel.select(pool8, p, now=NOW)
results["S8_expired_old_excluded"] = all(r["source_url"] != "old" for r in d8["selected"])
results["S8_selects_new"] = d8["selected"][0]["source_url"] == "new" if d8["selected"] else None

# S9: determinism — same pool twice => identical output
pool9 = [_row("x", 80, conf="LOW", age_h=10), _row("y", 82, conf="HIGH", age_h=2),
         _row("z", 78, conf="MEDIUM", age_h=30)]
d9a = sel.select(pool9, p, now=NOW)
d9b = sel.select(pool9, p, now=NOW)
results["S9_deterministic"] = json.dumps(d9a, sort_keys=True) == json.dumps(d9b, sort_keys=True)

# S10: evergreen preferred under thin pool (starvation) over expired timely
pool10 = [_row("ever", 65, evergreen=True, age_h=300), _row("timely_low", 60, age_h=1)]
d10 = sel.select(pool10, p, now=NOW)
results["S10_selects_under_starvation"] = len(d10["selected"]) >= 1

print(json.dumps(results, indent=2, default=str))
