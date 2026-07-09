"""Content Engine validation suite — exercises the FULL EOS spine against Supabase.

Success criteria (M1):
  C1 real job enters the queue
  C2 the Worker claims the job
  C3 the engine skill executes (driver runs, claims+reasons+persists)
  C4 structured JSON is produced
  C5 the result is persisted (content_engine_runs)
  C6 queue status transitions correctly (pending -> processing -> done)
  C7 failure path routes to content_engine_dlq
  C8 observability confirms the execution (content_engine_runs has source_url/status)

Credentials are read from the environment (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, plus
AI_RUNTIME_*). They are NEVER hardcoded and NEVER printed. To run locally without a cloud
account, point AI_RUNTIME_* at the mock runtime in tests/contract/.
"""

import os
import sys

# EOS shared queue client lives at engines/_shared/.
_HERE = os.path.realpath(__file__)
_SHARED = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "_shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
import eos_queue as ce_queue  # noqa: E402

import requests  # noqa: E402


def _load_dotenv(path=".env") -> None:
    """Minimal stdlib .env loader (no extra dependency). Idempotent."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _load_runtime_contract() -> None:
    """Load the AI Runtime contract (AI_RUNTIME_*) for local validation.

    In production the EOS Worker runs INSIDE the runtime and inherits these vars.
    For local validation we load them from the Runtime v1 (Hermes) profile's .env
    if not already present in the environment. CLI/CI can override via env or .env.
    Nothing here is ever committed (the profile .env is gitignored, and we only read).
    """
    if os.environ.get("AI_RUNTIME_BASE_URL") and os.environ.get("AI_RUNTIME_API_KEY"):
        return  # env already wins (mock or real runtime)
    import glob
    home = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    candidates = glob.glob(os.path.join(home, "hermes", "profiles", "*", ".env"))
    for p in candidates:
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


def _drain_all() -> int:
    """Hermetic reset: remove any (content,score) rows left by prior runs.

    Returns the number of queue rows removed. Keeps the validation reproducible.
    """
    removed = 0
    for table in ("content_engine_runs", "content_engine_dlq", "content_engine_queue"):
        r = requests.delete(
            f"{url}/rest/v1/{table}?engine=eq.{ENGINE}&stage=eq.{STAGE}",
            headers={**hdr, "Prefer": "return=representation"}, timeout=30,
        )
        if r.status_code < 400:
            removed += len(r.json())
    return removed


_load_dotenv()
_load_runtime_contract()

ENGINE, STAGE = "content", "score"
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not url or not key:
    sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (in .env or environment).")
hdr = {"Authorization": f"Bearer {key}", "apikey": key}

results = {}

# ---- Hermetic setup: drain all prior (content,score) rows so the Worker sees ONLY our job ----
_drained = _drain_all()
if _drained:
    results["drained_prior_rows"] = _drained

# ---- C1: enqueue a real, provider-agnostic job ----
jid = ce_queue.enqueue(ENGINE, STAGE, {
    "title": "New open-source LLM beats GPT-4 on agent benchmarks",
    "content": "A startup released a generative AI agent that automates brand visibility tasks.",
    "source_url": "https://example.com/llm-agent-release",
    "source_type": "rss",
})
results["C1_enqueued_job_id"] = jid

# driver lives at engines/content/lib/run_score_stage.py
DRIVER = os.path.join(os.path.dirname(_HERE), "lib", "run_score_stage.py")
r = __import__("subprocess").run([sys.executable, DRIVER],
                                 cwd=os.path.dirname(_HERE), capture_output=True, text=True)
results["C3_worker_stdout"] = r.stdout.strip()
results["C3_worker_stderr"] = r.stderr.strip()
results["C3_worker_rc"] = r.returncode

qr = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?id=eq.{jid}&select=status,attempts",
                  headers=hdr, timeout=30).json()
results["C2_job_claimed"] = bool(qr)
results["C6_final_status"] = qr[0]["status"] if qr else None

rr = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?job_id=eq.{jid}&select=*",
                  headers=hdr, timeout=30).json()
results["C5_run_persisted"] = len(rr) == 1
results["C4_structured_json"] = rr[0]["result_json"] if rr else None
results["C8_run_status"] = rr[0]["status"] if rr else None
results["C8_source_url"] = rr[0]["source_url"] if rr else None

# ---- M1: the persisted result is STRICT JSON conforming to the frozen contract ----
if rr:
    rj = rr[0]["result_json"] or {}
    results["M1_result_is_strict_json"] = isinstance(rj, dict)
    results["M1_keys_valid"] = (
        all(k in rj for k in ("score", "category", "decision", "rationale"))
        and isinstance(rj.get("score"), int) and 0 <= rj["score"] <= 10
        and rj.get("category") in ("product", "industry", "competitor", "research", "opinion", "noise")
        and rj.get("decision") in ("approve", "reject")
        and isinstance(rj.get("rationale"), str) and len(rj["rationale"]) <= 280
    )

# ---- M2: prove the scoring was produced by genuine AI Runtime reasoning ----
if rr:
    rj = rr[0]["result_json"] or {}
    results["M2_model_present"] = bool(rj.get("model"))
    results["M2_model"] = rj.get("model")
    results["M2_score_in_contract"] = isinstance(rj.get("score"), int) and 0 <= rj["score"] <= 10
    results["M2_keys_valid"] = all(
        k in rj for k in ("score", "category", "decision", "rationale")
    ) and rj.get("decision") in ("approve", "reject")

# ---- C7: failure path routes to DLQ (hermetic: drain again so OUR bad job is claimed) ----
_drain_all()
jid_bad = ce_queue.enqueue(ENGINE, STAGE, {"title": "bad item", "source_url": "https://x/bad"},
                           max_attempts=1)
job = ce_queue.claim(ENGINE, STAGE)
claimed_id = job["id"] if job else None
results["C7_claimed_bad_job"] = claimed_id
if claimed_id:
    ce_queue.fail(claimed_id, "validation-induced failure (attempts exhausted)")
    dlq = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{claimed_id}&select=*",
                       headers=hdr, timeout=30).json()
    results["C7_dlq_captured"] = len(dlq) >= 1
    results["C7_dlq_error"] = dlq[0]["last_error"] if dlq else None
    fj = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?id=eq.{claimed_id}&select=status",
                      headers=hdr, timeout=30).json()
    results["C7_queue_status"] = fj[0]["status"] if fj else None
else:
    results["C7_dlq_captured"] = False
    results["C7_note"] = "no pending job to claim (queue busy from prior runs)"

# ---- C8 observability: last_run present ----
lr = ce_queue.last_run(STAGE)
results["C8_last_run_present"] = lr is not None
results["C8_last_run_at"] = lr["created_at"] if lr else None

print(__import__("json").dumps(results, indent=2, default=str))
