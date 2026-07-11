"""Q6 — Scheduler entrypoint tests (external hook, no scheduler logic)."""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

# Make the collector src + tests importable.
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.run_once import build_parser, main, _parse_prompt_refs  # noqa: E402
from orchestrator.collection_result import CollectionStatus  # noqa: E402
from _helpers import FakeBrowserAdapter  # noqa: E402


def _registry_path():
    return ROOT / "docs" / "collector" / "prompts" / "registry.json"


def test_q6_parse_prompt_refs_validates_against_registry():
    from prompt_registry.loader import PromptRegistry  # noqa: E402

    reg = PromptRegistry(_registry_path())
    refs = _parse_prompt_refs("PROMPT-TREND-SCAN@1.0.0", reg)
    assert [r.key() for r in refs] == [("PROMPT-TREND-SCAN", "1.0.0")]


def test_q6_parse_prompt_refs_rejects_unknown():
    from prompt_registry.loader import PromptRegistry  # noqa: E402

    reg = PromptRegistry(_registry_path())
    with pytest.raises(ValueError):
        _parse_prompt_refs("DOES-NOT-EXIST@9.9.9", reg)


def test_q6_parse_prompt_refs_rejects_bad_format():
    from prompt_registry.loader import PromptRegistry  # noqa: E402

    reg = PromptRegistry(_registry_path())
    with pytest.raises(ValueError):
        _parse_prompt_refs("NOVERSION", reg)


def test_q6_entrypoint_runs_one_collection_exits_zero(monkeypatch, tmp_path):
    """Swap the production _run for one using FakeBrowserAdapter (no real
    browser). main() must drive one collection and exit 0 on SUCCESS."""
    async def fake_run(args):
        from orchestrator import CollectorConfig, GrokCollector, PromptRef  # noqa: E402
        from prompt_registry.loader import PromptRegistry  # noqa: E402

        registry = PromptRegistry(args.registry)
        refs = [PromptRef("PROMPT-TREND-SCAN", "1.0.0", {"topic": "AI"})]
        cfg = CollectorConfig(state_dir=tmp_path, store_dir=tmp_path / "store",
                               intake_dir=tmp_path / "intake", quota_limit=args.quota)
        col = GrokCollector(FakeBrowserAdapter(), registry, cfg, refs, args.label, args.date)
        return await col.run_collection()

    monkeypatch.setattr("orchestrator.run_once._run", fake_run)

    rc = main([
        "--registry", str(_registry_path()),
        "--prompts", "PROMPT-TREND-SCAN@1.0.0",
        "--label", "q6-cli",
        "--state-dir", str(tmp_path),
        "--store-dir", str(tmp_path / "store"),
        "--intake-dir", str(tmp_path / "intake"),
    ])
    assert rc == 0


def test_q6_entrypoint_reports_failed_nonzero(monkeypatch):
    async def fake_run_failed(args):
        from orchestrator.collection_result import CollectionResult  # noqa: E402

        res = CollectionResult(collection_id="x", status=CollectionStatus.FAILED)
        res.error = "boom"
        return res

    monkeypatch.setattr("orchestrator.run_once._run", fake_run_failed)
    rc = main([
        "--registry", str(_registry_path()),
        "--prompts", "PROMPT-TREND-SCAN@1.0.0",
        "--label", "q6-fail",
    ])
    assert rc == 2  # EXIT_FAILED


def test_q6_entrypoint_suspended_exits_zero(monkeypatch):
    """SUSPENDED (quota) is expected/resumable -> exit 0, not failure."""
    async def fake_run_suspended(args):
        from orchestrator.collection_result import CollectionResult  # noqa: E402

        res = CollectionResult(collection_id="x", status=CollectionStatus.SUSPENDED)
        res.prompts_completed = 2
        return res

    monkeypatch.setattr("orchestrator.run_once._run", fake_run_suspended)
    rc = main([
        "--registry", str(_registry_path()),
        "--prompts", "PROMPT-TREND-SCAN@1.0.0",
        "--label", "q6-susp",
    ])
    assert rc == 0


def test_q6_entrypoint_precondition_error_exits_nonzero():
    """Unknown prompt spec -> precondition error -> non-zero (not a FAILED run)."""
    rc = main([
        "--registry", str(_registry_path()),
        "--prompts", "NOPE@1.0.0",
        "--label", "q6-bad",
    ])
    assert rc == 2


def test_q6_no_scheduler_logic_in_module():
    """Structural guarantee: the entrypoint must not implement cadence/lock/
    queue/retry/backoff — scheduling is the caller's job (design §11)."""
    src = inspect.getsource(__import__("orchestrator.run_once", fromlist=["main"]))
    for forbidden in ("while True", "time.sleep", "backoff", "apscheduler",
                      "sched.add_job", "schedule.add_job"):
        assert forbidden not in src, f"entrypoint must not contain scheduler logic: {forbidden}"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
