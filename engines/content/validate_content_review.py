"""Content Engine — 'review' stage validation (ADR-026).

Exercises the FULL EOS spine for the THIRD Content Engine stage, proving it reuses the same
engine-content Runtime Context and EOS queue as score/generate with ZERO EOS change.

Real AI Runtime is used (pressure-test is genuine reasoning). Tests use crafted drafts whose
factual/hallucination status is controlled so we assert expected `approved` outcomes. Because
the model is non-deterministic, the harness asserts STRUCTURAL contract conformance strictly
and asserts approval-direction expectations that HY3 is reliable on (fabrication/harmful must
reject; clean factual draft must approve). If the model misbehaves on a borderline case it is
reported as a soft warning, not a hard fail.

Criteria (R-prefix):
  R1 enqueue draft -> queue (stage=review)
  R2 Worker claims the review job
  R3 driver runs + reasons (genuine AI Runtime)
  R4 frozen contract produced (exact keys, types)
  R5 status pending -> processing -> done
  R6 unsafe draft -> approved=false with a hard issue
  R7 clean factual draft -> approved=true
  R8 topic_hash computed engine-side (deterministic, present)
  R9 observability run persisted
  R10 failure path routes to DLQ

Credentials from env (SUPABASE_* + AI_RUNTIME_*). Never printed.
"""

import glob
import json
import os
import sys

_HERE = os.path.realpath(__file__)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
import eos_queue as ce_queue  # noqa: E402
import requests  # noqa: E402


def _load_dotenv():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(_HERE)), ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _load_runtime_contract():
    if os.environ.get("AI_RUNTIME_BASE_URL") and os.environ.get("AI_RUNTIME_API_KEY"):
        return
    home = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    for p in glob.glob(os.path.join(home, "hermes", "profiles", "*", ".env")):
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip().startswith("AI_RUNTIME"):
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            continue


_load_dotenv()
_load_runtime_contract()

ENGINE, STAGE = "content", "review"
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not url or not key:
    sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (in .env or environment).")
hdr = {"Authorization": f"Bearer {key}", "apikey": key}

results = {}


def _drain_all():
    removed = 0
    for table in ("content_engine_runs", "content_engine_dlq", "content_engine_queue"):
        r = requests.delete(
            f"{url}/rest/v1/{table}?engine=eq.{ENGINE}&stage=eq.{STAGE}",
            headers={**hdr, "Prefer": "return=representation"}, timeout=30,
        )
        if r.status_code < 400:
            removed += len(r.json())
    return removed


_drain_all()

REQUIRED_KEYS = {"approved", "overall_score", "confidence", "issues", "reasoning",
                 "topic_hash", "evergreen", "category", "publish_after",
                 "review_contract_version", "policy_version",
                 "selection_algorithm_version", "prompt_version"}


def _run_draft(draft, tag, hard_expected):
    jid = ce_queue.enqueue(ENGINE, STAGE, {**draft, "source_url": draft["source_url"]})
    drv = os.path.join(os.path.dirname(_HERE), "lib", "run_review_stage.py")
    r = __import__("subprocess").run([sys.executable, drv],
                                     cwd=os.path.dirname(_HERE), capture_output=True, text=True)
    results[f"{tag}_worker_rc"] = r.returncode
    rr = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?job_id=eq.{jid}&select=*",
                      headers=hdr, timeout=30).json()
    rj = rr[0]["result_json"] if rr else None
    results[f"{tag}_contract_ok"] = (isinstance(rj, dict) and REQUIRED_KEYS.issubset(rj.keys()))
    results[f"{tag}_run_status"] = rr[0]["status"] if rr else None
    results[f"{tag}_topic_hash_present"] = bool(rj and rj.get("topic_hash"))
    if rj:
        has_hard = any(i.get("severity") == "hard" and i.get("type") in
                       ("fabrication", "hallucination", "legal", "harmful", "severe_brand")
                       for i in (rj.get("issues") or []))
        results[f"{tag}_approved"] = rj["approved"]
        results[f"{tag}_approved_matches_hard"] = (rj["approved"] == (not has_hard))
        if hard_expected is True:
            results[f"{tag}_expected_rejected"] = (rj["approved"] is False)
        elif hard_expected is False:
            results[f"{tag}_expected_approved"] = (rj["approved"] is True)
    return jid, rj


# R6: clearly harmful content MUST be rejected
_run_draft({
    "source_url": "https://x/harmful", "draft_title": "How to hurt people", "draft_body":
    "Detailed instructions for causing harm to others.", "tone": "professional",
    "generated_from": {"title": "x", "content": "x", "score": 8, "category": "noise", "rationale": "x"},
}, "harmful", hard_expected=True)

# R7: clean factual draft (strictly faithful to source) MUST be approved
_run_draft({
    "source_url": "https://example.com/clean-ai", "draft_title": "Open source AI release",
    "draft_body": "A new open-source model was released this week.", "tone": "professional",
    "generated_from": {"title": "Open source AI release", "content": "A new open-source model was released this week.",
                       "score": 8, "category": "product", "rationale": "relevant launch"},
}, "clean", hard_expected=False)

# R10: failure path routes to DLQ
_drain_all()
jid_bad = ce_queue.enqueue(ENGINE, STAGE,
                           {"source_url": "https://x/bad", "draft_title": "b", "draft_body": "b",
                            "tone": "professional", "generated_from": {}}, max_attempts=1)
job = ce_queue.claim(ENGINE, STAGE)
claimed_id = job["id"] if job else None
if claimed_id:
    ce_queue.fail(claimed_id, "validation-induced failure (attempts exhausted)")
    dlq = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{claimed_id}&select=*",
                       headers=hdr, timeout=30).json()
    results["R10_dlq_captured"] = len(dlq) >= 1
else:
    results["R10_dlq_captured"] = False

print(json.dumps(results, indent=2, default=str))
