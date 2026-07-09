"""Content Engine — scheduled Selection trigger (EOM §8 operational mechanism).

This is the engine-owned SCHEDULE DEFINITION. It is NOT a new EOS component: it declares WHEN
Selection runs, using the publish_time/timezone from Policy. A platform scheduler (cron/worker)
invokes `run_select.py` at the computed local time. The engine owns the *when* (Policy); the
EOS owns the *mechanism* (the scheduler/cron runner).

To wire it on a host:
  - Linux/cron:  `0 9 * * *  TZ=Asia/Kolkata  python -m engines.content.lib.run_select`
  - Growth OS worker supervisor: register the `select` schedule with the platform scheduler.
"""

from __future__ import annotations

import json
import os
from typing import Optional


def _policy() -> dict:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                        "config", "policy.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def schedule_spec(policy: Optional[dict] = None) -> dict:
    """Return the canonical schedule the platform scheduler should honor.

    Returns a dict the platform scheduler can consume (e.g. cron expression + tz).
    """
    policy = policy or _policy()
    publish_time = str(policy.get("publish_time", "09:00"))
    tz = str(policy.get("timezone", "Asia/Kolkata"))
    hh, mm = publish_time.split(":")
    cron = f"{int(mm)} {int(hh)} * * *"
    return {
        "engine": "content",
        "command": "python -m engines.content.lib.run_select",
        "cron": cron,
        "timezone": tz,
        "description": "Content Engine daily Selection (Approved Pool -> Publish queue)",
    }


if __name__ == "__main__":
    import sys
    spec = schedule_spec()
    print(json.dumps(spec, indent=2))
    sys.exit(0)
