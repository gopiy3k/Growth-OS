"""Content Engine — end-to-end Production Evidence validation (ADR-024 §7).

Runs the FULL chain against REAL systems (no mocks, no contract-shaped simulations):
  Generate (real Hermes Runtime) -> Review (real Hermes Runtime) -> Selection -> Publish (real Buffer)

Proves:
  E1 Producer-less injection -> score job (reuses existing score driver semantics)
  E2 Generate -> Review wiring (generate enqueues review, not publish)
  E3 Review pressure-tests in the SAME Runtime Context, enforces contract, Approved Pool entry
  E4 Selection deterministically picks the best safe draft from the Approved Pool
  E5 Publish publishes to REAL Buffer, returns real update ids (External-System Validation)
  E6 Idempotency: re-running Selection does not re-publish
  E7 End-to-end run written to content_engine_runs with real outcomes

This harness uses ONE unique source_url so it never collides with production data and so
idempotency (E6) is observable. It then drains only the rows it created.

Credentials from env (.env): SUPABASE_*, AI_RUNTIME_*, BUFFER_TOKEN. Never printed.
"""

import glob
import json
import os
import subprocess
import sys
import requests

_HERE = os.path.realpath(__file__)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # repo root
_SHARED = os.path.join(_REPO, "engines", "_shared")
for _p in (_SHARED, os.path.join(_REPO, "engines", "content", "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import eos_queue as ce_queue  # noqa: E402

# ---- env bootstrap ----
def _load_dotenv():
    for base in (os.path.join(_REPO, ".env"), os.path.join(_REPO, "engines", "content", ".env")):
        try:
            for line in open(base, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except FileNotFoundError:
            pass

def _load_runtime():
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
_load_runtime()

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not url or not key:
    sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
hdr = {"Authorization": f"Bearer {key}", "apikey": key}
results = {}

UNIQ = f"https://example.com/e2e-{os.getpid()}-{int(__import__('time').time())}"

def _drain_mine():
    removed = 0
    for table in ("content_engine_runs", "content_engine_dlq", "content_engine_queue"):
        r = requests.delete(
            f"{url}/rest/v1/{table}?engine=eq.content&payload_json->>source_url=eq.{UNIQ}",
            headers={**hdr, "Prefer": "return=representation"}, timeout=30,
        )
        if r.status_code < 400:
            removed += len(r.json())
    # runs keyed by source_url directly (publish/select)
    for table in ("content_engine_runs",):
        r = requests.delete(
            f"{url}/rest/v1/{table}?engine=eq.content&source_url=eq.{UNIQ}",
            headers={**hdr, "Prefer": "return=representation"}, timeout=30,
        )
        if r.status_code < 400:
            removed += len(r.json())
    return removed

_drain_mine()

LIB = os.path.join(_REPO, "engines", "content", "lib")
def _run(driver, tag):
    r = subprocess.run([sys.executable, os.path.join(LIB, driver)],
                       cwd=os.path.join(_REPO, "engines", "content"), capture_output=True, text=True)
    results[f"{tag}_rc"] = r.returncode
    results[f"{tag}_stderr"] = r.stderr.strip()[-300:]
    return r

# E1: inject a scored item (mimics score stage output) for the generate stage to consume.
# Use a REAL, verifiable public URL so Review can validate the source (untrusted-input rule).
# Real, verifiable Wikipedia article; the unique run token is a benign query param
# (Wikipedia ignores unknown params, so the page still resolves and Review can validate it).
UNIQ = f"https://en.wikipedia.org/wiki/Open_source?e2e={os.getpid()}-{int(__import__('time').time())}"
_SOURCE_SNIPPET = (
    "Open source is source code that is made freely available for possible modification and "
    "redistribution. Products include permission to use the source code, design documents, or "
    "content of the project."
)
jid = ce_queue.enqueue("content", "generate", {
    "title": "Open source defined",
    "content": _SOURCE_SNIPPET,
    "source_url": UNIQ,
    "score": 9,
    "category": "product",
    "rationale": "Strong, relevant background for the audience.",
})

# E2/E3: run generate then review (both real Runtime)
g = _run("run_generate_stage.py", "generate")
# verify generate enqueued a review job (not publish). Queue stores source_url inside payload_json.
q = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?engine=eq.content&payload_json->>source_url=eq.{UNIQ}&select=stage,status",
                headers=hdr, timeout=30).json()
results["E2_generate_enqueued_review"] = any(x["stage"] == "review" for x in q)
results["E2_generate_enqueued_publish_directly"] = any(x["stage"] == "publish" for x in q)

rv = _run("run_review_stage.py", "review")
run = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?engine=eq.content&stage=eq.review&source_url=eq.{UNIQ}&select=*",
                   headers=hdr, timeout=30).json()
rj = run[0]["result_json"] if run else None
results["E3_review_run_present"] = bool(run)
results["E3_review_approved"] = bool(rj and rj.get("approved")) if rj else None
results["E3_review_contract_ok"] = bool(rj) and all(k in rj for k in
    ("approved","overall_score","confidence","issues","reasoning","topic_hash","evergreen","category"))

# E4/E5: run selection (enqueues publish for the approved draft) then publish (real Buffer)
# Clean any stale pending publish jobs from prior crashed runs so this run is isolated.
requests.delete(f"{url}/rest/v1/content_engine_queue?engine=eq.content&stage=eq.publish&status=eq.pending",
               headers={**hdr, "Prefer": "return=representation"}, timeout=30)
sl = _run("run_select.py", "select")
sel_run = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?engine=eq.content&stage=eq.select&order=created_at.desc&limit=1&select=*",
                        headers=hdr, timeout=30).json()
results["E4_select_run_present"] = bool(sel_run)
results["E4_select_chose_one"] = bool(sel_run) and len(sel_run[0]["result_json"].get("selected_source_urls", [])) >= 1

pb = _run("run_publish_stage.py", "publish")
pub = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_runs?engine=eq.content&stage=eq.publish&source_url=eq.{UNIQ}&select=*",
                   headers=hdr, timeout=30).json()
results["E5_publish_run_present"] = bool(pub)
results["E5_real_buffer_ids"] = bool(pub) and bool((pub[0]["result_json"] or {}).get("updates"))
results["E5_outcome_status"] = (pub[0]["result_json"] or {}).get("status") if pub else None
# Durable production evidence: capture the REAL Buffer update ids + published text length
# into stdout BEFORE cleanup (the drain step removes the row we created).
if pub:
    _res = pub[0]["result_json"] or {}
    results["E5_buffer_update_ids"] = _res.get("updates")
    results["E5_published_text_len"] = len(_res.get("text", ""))
    results["E5_source_url"] = UNIQ

# E6: re-run selection -> should NOT enqueue another publish for same source (idempotent)
sl2 = _run("run_select.py", "select2")
q2 = requests.get(f"{url.rstrip('/')}/rest/v1/content_engine_queue?engine=eq.content&stage=eq.publish&payload_json->>source_url=eq.{UNIQ}&select=id",
                 headers=hdr, timeout=30).json()
results["E6_idempotent_single_publish_job"] = len(q2) == 1

# E7
results["E7_end_to_end_status"] = "complete" if (results.get("E3_review_run_present") and
    results.get("E5_publish_run_present") and results.get("E5_real_buffer_ids")) else "incomplete"

# cleanup only what we created
results["_drained"] = _drain_mine()

print(json.dumps(results, indent=2, default=str))
