"""ENGINE-009 regression test — provenance must survive the FROZEN downstream pipeline.

Drives the real frozen stage drivers (score -> generate -> review) against an in-memory
eos_queue and a stubbed `requests.post` (the frozen AI-Runtime call). The collector attaches
four immutable evidence fields to every opportunity:

    raw_evidence_ref, record_key, collector_version, endpoint

This test asserts they ride untouched through each stage's result_json and land in the
Review result_json — the row the Approved Pool (logical view over content_engine_runs)
reads. No schema / stage / queue changes are exercised; only the contract-preserving fold.

Frozen-baseline compatible: stubs `requests.post`, NOT `llm_runtime` (the latter exists
only in the PO newsroom WIP and is intentionally out of scope here).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types

import pytest

ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)  # engines/content/lib/tests -> repo root
LIB = os.path.join(ROOT, "engines", "content", "lib")
SHARED = os.path.join(ROOT, "engines", "_shared")
for p in (LIB, SHARED):
    if p not in sys.path:
        sys.path.insert(0, p)

PROVENANCE = ("raw_evidence_ref", "record_key", "collector_version", "endpoint")

SAMPLE_PROVENANCE = {
    "raw_evidence_ref": "evidence/2026-07-11/abc123/PROMPT-TREND-SCAN@1.2.0.json",
    "record_key": {
        "collection_id": "55d63dd9c0212287c0acd68a0e6e0e804a0aa1aa563e1a879b5e8c235758e828",
        "prompt_id": "PROMPT-TREND-SCAN",
        "prompt_version": "1.2.0",
    },
    "collector_version": "1.0.0",
    "endpoint": "https://x.com/i/grok",
}


# --- frozen-compatible in-memory eos_queue --------------------------------------
def _in_mem_queue():
    store = {"jobs": [], "n": 0}

    def enqueue(engine, stage, payload, *, max_attempts=3):
        store["n"] += 1
        jid = f"job-{store['n']}"
        store["jobs"].append(
            {"id": jid, "engine": engine, "stage": stage,
             "payload_json": payload, "status": "pending"}
        )
        return jid

    def claim(engine, stage):
        for j in store["jobs"]:
            if j["engine"] == engine and j["stage"] == stage and j["status"] == "pending":
                j["status"] = "processing"
                return {"id": j["id"], "payload_json": j["payload_json"]}
        return None

    def complete(job_id, result, *, source_url=None):
        for j in store["jobs"]:
            if j["id"] == job_id:
                j["status"] = "success"
                j["result_json"] = result
                return

    def fail(job_id, error):
        for j in store["jobs"]:
            if j["id"] == job_id:
                j["status"] = "failed"
                j["error"] = error
                return

    def is_source_known(url):
        return False

    def pending_count(engine, stage):
        return 0

    mod = types.ModuleType("eos_queue")
    for name, fn in {
        "enqueue": enqueue, "claim": claim, "complete": complete,
        "fail": fail, "is_source_known": is_source_known,
        "pending_count": pending_count,
    }.items():
        setattr(mod, name, fn)
    mod._store = store
    return mod


def _fake_requests_post():
    """Stub for requests.post that yields stage-appropriate JSON.

    Branches by inspecting the system prompt captured from the real stage call, so it
    mimics each frozen stage's AI-Runtime response without any runtime/transport coupling.
    """
    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            sp = (captured.get("system") or "").lower()
            if "scoring" in sp:
                return {"model": "stub-score",
                        "choices": [{"message": {"content": json.dumps(
                            {"score": 8, "category": "product", "decision": "approve",
                             "rationale": "relevant"})}}]}
            if "generation" in sp:
                return {"model": "stub-gen",
                        "choices": [{"message": {"content": json.dumps(
                            {"draft_title": "T", "draft_body": "B",
                             "tone": "professional", "word_count": 10})}}]}
            if "review" in sp:
                return {"model": "stub-rev",
                        "choices": [{"message": {"content": json.dumps(
                            {"approved": True, "overall_score": 80, "confidence": "HIGH",
                             "issues": [], "reasoning": "ok", "evergreen": False,
                             "category": "product"})}}]}
            return {"model": "stub", "choices": [{"message": {"content": "{}"}}]}

    def post(url, **kwargs):
        messages = (kwargs.get("json") or {}).get("messages", [])
        captured["system"] = messages[0]["content"] if messages else ""
        return _Resp()

    post._captured = captured
    return post


@pytest.fixture
def harness(monkeypatch):
    os.environ["AI_RUNTIME_BASE_URL"] = "http://stub.local/v1"
    os.environ["AI_RUNTIME_API_KEY"] = "stub-key"

    q = _in_mem_queue()
    monkeypatch.setitem(sys.modules, "eos_queue", q)

    # ensure the frozen stage modules are importable from this test
    if LIB not in sys.path:
        sys.path.insert(0, LIB)
    import requests  # monkeypatch target for the frozen stages' requests.post

    # import the frozen stage modules AFTER eos_queue is injected
    rs = importlib.import_module("run_score_stage")
    rg = importlib.import_module("run_generate_stage")
    rv = importlib.import_module("run_review_stage")
    for m in (rs, rg, rv):
        m.ce_queue = q

    fake_post = _fake_requests_post()
    monkeypatch.setattr("requests.post", fake_post)

    return {"queue": q, "score": rs, "generate": rg, "review": rv,
            "store": q._store, "fake_post": fake_post}


def _assert_prov(result, where):
    missing = [k for k in PROVENANCE if result.get(k) is None]
    assert not missing, f"provenance lost at {where}: missing {missing} in {result!r}"


def test_provenance_survives_frozen_pipeline(harness):
    q, rs, rg, rv = harness["queue"], harness["score"], harness["generate"], harness["review"]
    store = harness["store"]

    # 1) OD egress -> score job (collector attaches the 4 fields)
    score_payload = {
        "title": "Open-source LLM tops agent benchmarks",
        "content": "A startup shipped an agent framework.",
        "source_url": SAMPLE_PROVENANCE["raw_evidence_ref"],
        **SAMPLE_PROVENANCE,
    }
    q.enqueue("content", "score", score_payload)

    # 2) run frozen SCORE
    assert rs.main() == 0
    score_result = next(j["result_json"] for j in store["jobs"] if j["stage"] == "score")
    _assert_prov(score_result, "score result_json")

    # 3) bridge score -> generate (the frozen Selection/validate enqueue):
    #    pass the score result forward verbatim, including the 4 fields.
    q.enqueue("content", "generate", {
        "title": score_payload["title"],
        "content": score_payload["content"],
        "source_url": score_payload["source_url"],
        "score": score_result["score"],
        "category": score_result["category"],
        "rationale": score_result["rationale"],
        **SAMPLE_PROVENANCE,
    })

    # 4) run frozen GENERATE (enqueues review)
    assert rg.main() == 0
    gen_result = next(j["result_json"] for j in store["jobs"] if j["stage"] == "generate")
    _assert_prov(gen_result, "generate result_json")

    review_job = next(j for j in store["jobs"] if j["stage"] == "review")
    _assert_prov(review_job["payload_json"], "generate->review enqueue payload")

    # 5) run frozen REVIEW -> Approved Pool row
    assert rv.main() == 0
    review_result = review_job["result_json"]
    _assert_prov(review_result, "review result_json (Approved Pool row)")

    # 6) the Approved Pool read shape (pool_client.fetch_review_pool merges result_json) must
    #    expose the 4 fields, proving lossless downstream audit is restored.
    pooled = {"source_url": review_job["payload_json"].get("source_url"),
              "job_id": review_job["id"], **review_result}
    _assert_prov(pooled, "Approved Pool view")


def test_provenance_is_never_invented_without_source(harness):
    """If the upstream does NOT attach provenance, stages must not fabricate it."""
    q, rs = harness["queue"], harness["score"]
    store = harness["store"]

    q.enqueue("content", "score", {
        "title": "No provenance here",
        "content": "x",
        "source_url": "https://example.com/plain",
    })
    assert rs.main() == 0
    score_result = next(j["result_json"] for j in store["jobs"] if j["stage"] == "score")
    for k in PROVENANCE:
        assert k not in score_result, f"stage invented {k}={score_result.get(k)!r}"
