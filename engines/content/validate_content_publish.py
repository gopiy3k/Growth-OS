"""Content Engine - 'publish' stage validation (Phase 2).

Proves the generate->publish chaining and the Publish (terminal) Stage on the frozen
platform, with ZERO EOS changes.

Criteria:
  P1 enqueue an approved, scored+generated item into the generate queue
  P2 run the generate driver -> it reasons (AI Runtime) AND enqueues a publish job
  P3 a publish job exists in the queue (PROOF: Generate automatically enqueues Publish)
  P4 run the publish driver -> WITHOUT a Buffer token it must FAIL and route to DLQ
     (proves Publish claims jobs + retry/DLQ unchanged + no silent success)
  P5 contract test: buffer_client constructs the correct real Buffer request
  P6 simulate a successful Buffer response -> publish stage completes and writes the
     terminal Outcome run record (labeled SIMULATED Buffer response; real proof needs token)

Credentials from env (SUPABASE_*, AI_RUNTIME_*, BUFFER_*). Never printed.
Live Buffer publication evidence is BLOCKED until BUFFER_ACCESS_TOKEN (+ profile ids) is
provisioned - this harness proves the code paths without the secret.
"""

import json
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
            f"{url}/rest/v1/{table}?engine=eq.{ENGINE}&stage=in.(generate,publish)",
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

# ---- P1: enqueue an approved, scored+generated item (output of generate stage) ----
jid = ce_queue.enqueue(ENGINE, STAGE, {
    "title": "Open-source agent framework hits 10k stars",
    "content": "A new autonomous agent framework released under Apache-2.0.",
    "source_url": "https://example.com/agent-framework-release",
    "score": 9,
    "category": "product",
    "rationale": "Strong product launch, highly relevant.",
})
results["P1_enqueued_generate_job_id"] = jid

# ---- P2: run the generate driver (reasons via AI Runtime + now enqueues publish) ----
GEN_DRIVER = os.path.join(os.path.dirname(_HERE), "lib", "run_generate_stage.py")
r = __import__("subprocess").run(
    [sys.executable, GEN_DRIVER],
    cwd=os.path.dirname(_HERE), capture_output=True, text=True,
)
results["P2_generate_stdout"] = r.stdout.strip()
results["P2_generate_stderr"] = r.stderr.strip()
results["P2_generate_rc"] = r.returncode

# ---- P3: a publish job must now exist (Generate automatically enqueues Publish) ----
pub = requests.get(
    f"{url.rstrip('/')}/rest/v1/content_engine_queue"
    f"?engine=eq.{ENGINE}&stage=eq.publish&payload_json->>source_url=eq.https://example.com/agent-framework-release"
    f"&select=id,status",
    headers=hdr, timeout=30,
).json()
if isinstance(pub, dict):  # PostgREST returns {} for "no rows" on a single-object read
    pub = []
results["P3_publish_job_created"] = len(pub) >= 1
pub_id = pub[0]["id"] if pub else None
results["P3_publish_job_id"] = pub_id
results["P3_publish_job_status"] = pub[0]["status"] if pub else None

# ---- P4: run the publish driver WITHOUT a Buffer token -> must FAIL and stay pending (retry)
# A single fail on a max_attempts=3 job should NOT yet be in the DLQ - it must retry
# (proves Publish claims jobs + retry/DLQ mechanism unchanged). DLQ routing is proven
# separately below with a max_attempts=1 job.
os.environ.pop("BUFFER_ACCESS_TOKEN", None)  # ensure no token in this env
PUB_DRIVER = os.path.join(os.path.dirname(_HERE), "lib", "run_publish_stage.py")
r2 = __import__("subprocess").run(
    [sys.executable, PUB_DRIVER],
    cwd=os.path.dirname(_HERE), capture_output=True, text=True,
)
results["P4_publish_stdout"] = r2.stdout.strip()
results["P4_publish_stderr"] = r2.stderr.strip()
results["P4_publish_rc"] = r2.returncode
results["P4_publish_failed_correctly"] = (
    "BUFFER_ACCESS_TOKEN not set" in r2.stderr
    or "BUFFER_ACCESS_TOKEN not set" in r2.stdout
)
# job should still be pending (retry), not yet in DLQ
q_after = requests.get(
    f"{url.rstrip('/')}/rest/v1/content_engine_queue?id=eq.{pub_id}&select=status,attempts",
    headers=hdr, timeout=30,
).json()
q_after = q_after if isinstance(q_after, list) else []
results["P4_still_pending_for_retry"] = bool(q_after) and q_after[0]["status"] == "pending"
dlq = requests.get(
    f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{pub_id}&select=*",
    headers=hdr, timeout=30,
).json() if pub_id else []
# ---- P5: contract test - buffer_client builds the correct real Buffer GraphQL request ----
# We patch ONLY buffer_client's transport (requests.post) so the real Supabase calls
# in later steps stay intact. The fake emulates the supported Buffer Developer API
# (GraphQL, Bearer auth, single https://api.buffer.com endpoint).
import sys as _sys
_LIB = os.path.join(os.path.dirname(_HERE), "lib")
if _LIB not in _sys.path:
    _sys.path.insert(0, _LIB)
import run_publish_stage  # noqa: E402  (gives us the module + its buffer_client)
import buffer_client  # noqa: E402

_captured = {}

_orig_post = buffer_client.requests.post

def _fake_post(post_url, headers=None, json=None, timeout=None, **_kw):
    _captured["url"] = post_url
    _captured["headers"] = headers
    _captured["json"] = json
    class _R:
        status_code = 200
        def json(self):
            # emulate a successful GraphQL createPost for every channel
            return {"data": {"createPost": {"post": {"id": "u_graphql_1"}}}}
        def raise_for_status(self):
            pass
    return _R()

buffer_client.requests.post = _fake_post  # type: ignore[attr-defined]
os.environ["BUFFER_ACCESS_TOKEN"] = "test-token"
os.environ["BUFFER_LINKEDIN_PROFILE_ID"] = "prof_li"
os.environ["BUFFER_X_PROFILE_ID"] = "prof_x"
try:
    out = buffer_client.publish("hello world")
    _h = _captured.get("headers") or {}
    _j = _captured.get("json") or {}
    results["P5_buffer_url_ok"] = _captured.get("url") == "https://api.buffer.com"
    results["P5_buffer_auth_bearer"] = _h.get("Authorization") == "Bearer test-token"
    results["P5_buffer_graphql_createPost"] = "createPost" in (_j.get("query") or "")
    results["P5_buffer_returned_updates"] = out["updates"]
except Exception as e:  # noqa: BLE001
    results["P5_error"] = f"{type(e).__name__}: {e}"
finally:
    buffer_client.requests.post = _orig_post  # restore real transport for later steps
    # clear the test token so subsequent real Supabase steps aren't affected
    os.environ.pop("BUFFER_ACCESS_TOKEN", None)
    os.environ.pop("BUFFER_LINKEDIN_PROFILE_ID", None)
    os.environ.pop("BUFFER_X_PROFILE_ID", None)

# ---- P6: simulate a SUCCESSFUL Buffer response -> publish completes terminal Outcome ----
# (labeled SIMULATED: real proof requires the live BUFFER_ACCESS_TOKEN)
# Run IN-PROCESS so the simulated publish response is honored and no env token leaks.
_drain_all()
jid2 = ce_queue.enqueue(ENGINE, "publish", {
    "source_url": "https://example.com/sim-publish",
    "draft_title": "Simulated Post",
    "draft_body": "Body of the simulated publish.",
})
run_publish_stage.buffer_client.publish = lambda text: {  # type: ignore
    "success": True,
    "updates": [{"id": "sim_li_123", "service": "linkedin"},
                {"id": "sim_x_456", "service": "twitter"}],
}
rc = run_publish_stage.main()
results["P6_publish_rc"] = rc
rr = requests.get(
    f"{url.rstrip('/')}/rest/v1/content_engine_runs"
    f"?job_id=eq.{jid2}&select=*",
    headers=hdr, timeout=30,
).json()
results["P6_terminal_run_written"] = len(rr) == 1
results["P6_terminal_outcome"] = rr[0]["result_json"] if rr else None
results["P6_terminal_stage"] = rr[0]["stage"] if rr else None
results["P6_terminal_source_url"] = rr[0]["source_url"] if rr else None

# ---- P7: DLQ routing on exhaustion (retry/DLQ unchanged from score/generate) ----
# Restore the REAL buffer_client.publish (P6 swapped it for a simulation lambda).
import importlib
buffer_client.publish = importlib.reload(buffer_client).publish
jid_dlq = ce_queue.enqueue(ENGINE, "publish",
    {"source_url": "https://example.com/dlq-proof", "draft_title": "X", "draft_body": "Y"},
    max_attempts=1)
os.environ.pop("BUFFER_ACCESS_TOKEN", None)  # real publish path -> raises, job fails -> DLQ
dlq_rc = run_publish_stage.main()
dlq = requests.get(
    f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{jid_dlq}&select=*",
    headers=hdr, timeout=30,
).json()
dlq = dlq if isinstance(dlq, list) else []
results["P7_dlq_rc"] = dlq_rc
results["P7_routed_to_dlq"] = len(dlq) >= 1
results["P7_dlq_last_error"] = dlq[0]["last_error"] if dlq else None
# cleanup the DLQ proof row
requests.delete(
    f"{url.rstrip('/')}/rest/v1/content_engine_dlq?job_id=eq.{jid_dlq}",
    headers={**hdr, "Prefer": "return=representation"}, timeout=30,
)

print(json.dumps(results, indent=2, default=str))
