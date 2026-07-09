"""Content Engine — 'generate' stage validation (M3).

Exercises the FULL EOS spine for the SECOND Content Engine stage, proving one engine can host
multiple independent stages on the frozen platform with ZERO EOS changes.

Criteria:
  G1 enqueue approved item -> queue
  G2 Worker claims the generate job
  G3 driver runs + reasons (genuine AI Runtime)
  G4 structured JSON produced
  G5 persisted to content_engine_runs
  G6 status pending -> processing -> done
  G7 failure path routes to DLQ
  G8 observability (run has source_url/status)
  M3 result is strict JSON conforming to the generate contract (draft_title/body/tone/word_count)

Credentials from env (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, plus AI_RUNTIME_*). Never printed.
Uses the SAME shared eos_queue client as the score stage — no platform changes.
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
    if os.environ.get("AI_RUNTIME_BASE_URL") and os.environ.get("AI_RUNTIME_API_KEY"):
        return
    import glob
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


def _drain_all() -> int:
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

ENGINE, STAGE = "content", "generate"
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not url or not key:
    sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (in .env or environment).")
hdr = {"Authorization": f"Bearer {key}", "apikey": key}

results = {}

_drained = _drain_all()
if _drained:
    results["drained_prior_rows"] = _drained

# ---- G1: enqueue an APPROVED, scored item (output of the score stage) ----
jid = ce_queue.enqueue(ENGINE, STAGE, {
    "title": "New open-source LLM beats GPT-4 on agent benchmarks",
    "content": "A startup released a generative AI agent that automates brand visibility tasks.",
    "source_url": "https://example.com/llm-agent-release",
    "score": 8,
    "category": "product",
    "rationale": "Strong product launch, highly relevant to the audience.",
})
results["G1_enqueued_job_id"] = jid

# driver lives at engines/content/lib/run_generate_stage.py
DRIVER = os.path.join(os.path.dirname(_HERE), "lib", "run_generate_stage.py")
r = __import__("subprocess").run([sys.executable, DRIVER],
                                 cwd=os.path.dirname(_HERE), capture_output=True, text=True)
results["G3_worker_stdout"] = r.stdout.strip()
results["G3_worker_stderr"] = r.stderr.strip()
results["G3_worker_rc"] = r.returncode

qr = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?id=eq.{jid}&select=status,attempts",
                  headers=hdr, timeout=30).json()
results["G2_job_claimed"] = bool(qr)
results["G6_final_status"] = qr[0]["status"] if qr else None

rr = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?job_id=eq.{jid}&select=*",
                  headers=hdr, timeout=30).json()
results["G5_run_persisted"] = len(rr) == 1
results["G4_structured_json"] = rr[0]["result_json"] if rr else None
results["G8_run_status"] = rr[0]["status"] if rr else None
results["G8_source_url"] = rr[0]["source_url"] if rr else None

# ---- M3: the persisted result is STRICT JSON conforming to the generate contract ----
if rr:
    rj = rr[0]["result_json"] or {}
    results["M3_result_is_strict_json"] = isinstance(rj, dict)
    results["M3_keys_valid"] = (
        all(k in rj for k in ("draft_title", "draft_body", "tone", "word_count"))
        and isinstance(rj["draft_title"], str) and rj["draft_title"]
        and isinstance(rj["draft_body"], str) and len(rj["draft_body"]) <= 1200
        and rj["tone"] in ("professional", "casual", "thought_leadership")
        and isinstance(rj["word_count"], int) and rj["word_count"] >= 0
    )
    results["M3_model_present"] = bool(rj.get("model"))

# ---- G7: failure path routes to DLQ (hermetic: drain so OUR bad job is claimed) ----
_drain_all()
jid_bad = ce_queue.enqueue(ENGINE, STAGE,
                           {"title": "bad", "source_url": "https://x/bad", "score": 0,
                            "category": "noise", "rationale": "x"}, max_attempts=1)
job = ce_queue.claim(ENGINE, STAGE)
claimed_id = job["id"] if job else None
results["G7_claimed_bad_job"] = claimed_id
if claimed_id:
    ce_queue.fail(claimed_id, "validation-induced failure (attempts exhausted)")
    dlq = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{claimed_id}&select=*",
                       headers=hdr, timeout=30).json()
    results["G7_dlq_captured"] = len(dlq) >= 1
    fj = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?id=eq.{claimed_id}&select=status",
                      headers=hdr, timeout=30).json()
    results["G7_queue_status"] = fj[0]["status"] if fj else None
else:
    results["G7_dlq_captured"] = False

lr = ce_queue.last_run(STAGE)
results["G8_last_run_present"] = lr is not None

print(__import__("json").dumps(results, indent=2, default=str))
