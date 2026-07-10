# COLLECTOR-DESIGN-001 — Grok Trend Intelligence Collector (Production Design)

**Status:** APPROVED — Design v1 (FROZEN). Implementation authorized in increments.
**Date:** 2026-07-11
**Approved:** 2026-07-11 (with Amendments 1–3 incorporated)
**Profile:** engine-content
**Depends on:** ADR-027 (Local Chrome via CDP as Canonical Browser Runtime) — FROZEN
**Prerequisite gates:** Browser Runtime VERIFIED ✅ · Interaction Model VERIFIED ✅ · Multi-interaction VERIFIED ✅

This is the **frozen production design (v1)**. No further architectural redesign without
explicit PO approval. Implementation proceeds in small, verifiable increments strictly
within ADR-027 boundaries and the constraints in §22.

---

## 0. Design principles (from PO direction, binding)

- **Deterministic** — same inputs + same runtime → same collection record.
- **Replayable** — any collection can be re-run from its evidence.
- **Idempotent** — re-running a collection does not duplicate or corrupt stored evidence.
- **Evidence-first** — raw Grok output is preserved *before* any transformation.
- **Every collection reproducible** — prompt version + conversation id + timestamps recorded.
- **Every prompt versioned** — no unversioned prompt text enters production.
- **Every response preserved before normalization** — normalization never destroys raw.
- **No silent transformations** — normalization is structural parsing, not editorial judgement.
- **No editorial judgement** — the collector does NOT decide what is important. It faithfully
  collects, preserves, and normalizes Grok's research output for downstream processing.

The collector is an **evidence collector**, not an analyst. The pipeline it feeds:

```
Browser Runtime (frozen, ADR-027)
  -> Grok Trend Intelligence Collector   [this design]
  -> Evidence Store
  -> Opportunity Discovery               [consumes, NOT bypassed]
  -> Editorial Brain                     [consumes OD output, NOT bypassed]
  -> Publishing
```

The collector must **not** bypass or duplicate Opportunity Discovery or the Editorial Brain.

---

## 0.1 Approved amendments (binding — incorporated per PO approval 2026-07-11)

COLLECTOR-DESIGN-001 is APPROVED as Design v1 with three mandatory amendments. They are
normative: any implementation MUST satisfy them.

### Amendment 1 — Idempotency

Every collection run has a **deterministic `collection_id`** (not random per process).
- `collection_id` is derived from the collection's defining inputs, not from process/clock:
  `collection_id = sha256(prompt_id + ":" + prompt_version + ":" + collection_date + ":" + collection_label)`
  where `collection_date` is the UTC calendar date (YYYY-MM-DD) and `collection_label`
  identifies the scheduled collection (e.g. "daily-trend-scan"). Same inputs on the same
  day → same id. (A `run_id` UUID is still recorded per *execution* for tracing, distinct
  from the deterministic `collection_id`.)
- **Resume markers:** an append-only state file
  `state/<collection_id>.json` records per-prompt status:
  `pending | submitted | completed | failed`. The collector reads it at INIT; prompts
  already `completed` are **skipped** (never re-submitted). A crash mid-prompt leaves the
  prompt `submitted` (not `completed`) → on resume it is re-run from scratch in a fresh
  conversation (Mode B) — safe because Mode B isolates context.
- **Duplicate detection:** before writing raw evidence, the collector checks
  `EvidenceStore.exists(collection_id, prompt_id, prompt_version)`. If present and
  `completed`, the prompt is treated as already collected (idempotent no-op).
- **Exactly-once evidence preservation:** raw evidence write is atomic + keyed by
  `(collection_id, prompt_id, prompt_version)`; a second write with the same key is a
  no-op (or versioned append, never a duplicate row). Storage contract §10 enforces this.
- **Restart safety:** a collector restart never creates duplicate evidence — resume
  markers + duplicate detection guarantee exactly-once preservation across restarts.

### Amendment 2 — Prompt Registry (externalized)

Prompts are **never embedded in collector code**. They live in a versioned **Prompt
Registry** consumed by the collector:
- Location: `docs/collector/prompts/registry.json` (or equivalent DB table) — outside the
  collector source tree.
- Each collection references exactly: `prompt_id`, `prompt_version`, `prompt_hash`.
- The collector **executes prompt definitions only** — it loads `prompt_id`+`version`,
  renders the template, and hashes the rendered text; it never contains prompt strings.
- **Prompt evolution needs no collector code change:** adding/editing a prompt = edit the
  registry (new `version`), update the collection's reference. Collector code is untouched.
- This supersedes and extends §3 (Prompt versioning) — §3 is the mechanism; Amendment 2 is
  the hard boundary (no inline prompts).

### Amendment 3 — Provenance (mandatory)

Every evidence record MUST carry complete provenance **before** any downstream processing.
Minimum mandatory fields (all required, no omissions):

| Field | Source |
|-------|--------|
| `collection_id` | deterministic id (Amendment 1) |
| `prompt_id` | from Prompt Registry reference |
| `prompt_version` | from Prompt Registry reference |
| `conversation_id` | from Grok URL `?conversation=<id>` |
| `browser_session_id` | Chrome session/browser-context id if available (else null) |
| `collected_at` | UTC ISO-8601 timestamp |
| `collector_version` | semantic version of the collector build (e.g. `1.0.0`) |
| `runtime_version` | ADR-027 Browser Runtime version/ref (e.g. `ADR-027`) |
| `source` | constant `"grok"` |
| `endpoint` | constant `"https://x.com/i/grok"` |

The schemas in §8 (raw) and §9 (normalized) are extended to include ALL of the above as
required keys. Downstream (Opportunity Discovery, Editorial Brain) MUST NOT process a
record missing any mandatory provenance field.

---

## 1. Collector lifecycle

Phases, in order. Each phase is atomic and leaves the system in a known state.

1. **INIT** — load config (CDP url, profile, prompt registry, storage path, quota budget).
   Compute **deterministic `collection_id`** (Amendment 1). Load **resume markers**
   `state/<collection_id>.json` (Amendment 1) — know which prompts are already `completed`.
2. **ATTACH** — connect to Chrome CDP (`Target.getTargets` liveness probe).
3. **OPEN_TAB** — `Target.createTarget` → **new automation tab only** (ADR-027 §6 invariant).
4. **AUTH_VERIFY** — navigate to `https://x.com/i/grok`; assert `twid`/`auth_token` present
   and composer `<textarea>` present. If absent → FAILURE(AUTH), stop-and-report.
5. **CONV_SETUP** — per conversation strategy (§4): open fresh conversation (Mode B default).
6. **FOR each prompt in the collection's prompt set:**
   a. **SKIP_IF_DONE** — if resume marker = `completed` (or `EvidenceStore.exists`) → skip
      (Amendment 1, idempotent no-op).
   b. **PROMPT_RESOLVE** — load prompt template by id+version from Prompt Registry
      (Amendment 2), render, hash; assert `prompt_hash` matches registry.
   c. **SUBMIT** — mark resume marker `submitted`; set textarea via React setter,
      re-locate send button, click (§5).
   d. **WAIT** — poll completion (§6) with per-prompt timeout.
   e. **EXTRACT** — pull raw response (§7).
   f. **PRESERVE_RAW** — write raw evidence (§8, with full provenance §0.1-A3) **before**
      any normalization; mark resume marker `completed`.
   g. **NORMALIZE** — structural parse → normalized record (§9, with full provenance).
   h. **STORE** — write normalized record (§10) + hand to Opportunity Discovery intake (§16).
   i. **QUOTA_CHECK** — decrement quota accounting (§5/§11); if exhausted → SUSPEND.
7. **CLOSE_TAB** — `Target.closeTarget` on the automation tab **only** (finally-block, always).
8. **DETACH** — release CDP session handle.
9. **DONE / SUSPENDED** — exit status recorded.

User tabs are never touched in any phase (ADR-027 §6). The close is in a `finally` so a
failure mid-collection still cleans up the automation tab.

---

## 2. State machine

```
            ┌─────────┐
            │  IDLE   │
            └────┬────┘
                 │ schedule trigger / manual
                 ▼
            ┌─────────┐   attach fail (transport, retried)   ┌──────────┐
            │ ATTACH  │──────────────────────────────────────│ TRANSPORT│
            └────┬────┘                                       │  RETRY   │
                 │ ok                                         └────┬─────┘
                 ▼                                                │ ok
            ┌─────────┐                                           │
            │ OPEN_TAB │◄──────────────────────────────────────────┘
            └────┬────┘
                 ▼
            ┌──────────┐  auth lost / composer missing   ┌──────────┐
            │AUTH_VERIFY│──────────────────────────────►│  FAILED   │
            └────┬─────┘                                 │ (AUTH)    │
                 │ ok                                    └──────────┘
                 ▼
            ┌──────────┐
            │CONV_SETUP│
            └────┬─────┘
                 ▼
         ┌─────────────── LOOP per prompt ───────────────┐
         │  PROMPT_RESOLVE → SUBMIT → WAIT → EXTRACT →    │
         │  PRESERVE_RAW → NORMALIZE → STORE → QUOTA_CHECK│
         └───────────────┬───────────────────────────────┘
                         │ all prompts done
                         ▼
                    ┌──────────┐  quota exhausted   ┌────────────┐
                    │CLOSE_TAB │───────────────────►│ SUSPENDED  │
                    └────┬─────┘                    │ (resumable)│
                         │                           └────────────┘
                         ▼
                    ┌──────────┐
                    │  DETACH  │
                    └────┬─────┘
                         ▼
                    ┌──────────┐
                    │   DONE   │
                    └──────────┘
```

**Terminal states:** `DONE` (success), `SUSPENDED` (quota — resumable, not an error),
`FAILED` (auth / interaction / extraction / storage — stop-and-report, no auto-retry).

---

## 3. Prompt versioning

Prompts are **versioned, registered artifacts** — never inline strings in code.

- **Registry:** `docs/collector/prompts/registry.json` (or DB table). Each entry:
  ```json
  {
    "prompt_id": "PROMPT-TREND-SCAN",
    "version": "1.2.0",
    "description": "Scan X/Grok for emerging AI tooling trends",
    "template": "Survey the last 24h of discussions on {topic}. List distinct trends with 1-line evidence each.",
    "variables": ["topic"],
    "created_at": "2026-07-11T00:00:00Z",
    "owner": "engine-content"
  }
  ```
- **SemVer:** `MAJOR` = meaning/changes intent; `MINOR` = wording/structure tweak;
  `PATCH` = typo/fix. Any change → new version (immutable history).
- **Render + hash:** rendered prompt text is SHA-256 hashed. The hash is stored in every
  raw-evidence record so a collection is reproducible from its exact prompt bytes.
- **No unversioned prompts** enter production. The collector refuses to submit a prompt
  not resolvable to `prompt_id`+`version` in the registry.
- **Change control:** prompt edits go through the same PO-approval discipline as runtime
  changes — they alter collector *output*, which feeds Editorial Brain.

---

## 4. Conversation strategy (Mode A vs Mode B)

**Mode A — one persistent conversation per collection session.**
- Pros: matches validated continuity (ADR-027 §13); least setup overhead.
- Cons: **context pollution** — Grok conditions each response on the entire thread, so a
  later prompt's output is contaminated by earlier, unrelated prompts. Violates
  *deterministic* and *reproducible*. Shared `conversation_id` across unrelated topics
  breaks the 1:1 evidence→prompt mapping. Thread length/quota risk grows unbounded.

**Mode B — one fresh conversation per collection.**
- Pros: each collection is an **isolated context** → deterministic and reproducible;
  clean 1:1 `collection_id ↔ conversation_id` mapping (essential for evidence linking and
  resumability); no cross-topic contamination; resume = open a new conversation.
- Cons: marginally more setup (one extra navigation) — proven cheap in §13 (cost was
  negligible vs prompt generation time).

**Recommendation: MODE B (fresh conversation per collection) as the production default.**

Rationale, weighed against the PO's three criteria:
- *Context pollution* → Mode B eliminates it; Mode A guarantees it.
- *Determinism* → Mode B gives isolation; Mode A cannot (prior prompts leak).
- *Reproducibility* → Mode B lets any collection be re-run identically; Mode A cannot
  isolate a single collection's causal prompt.

**Local exception:** a collection that is *itself* a deliberate multi-turn research thread
(e.g., "ask, then drill into the answer") may use Mode A *within that single collection's
scope*. This is an explicit, versioned research pattern — not the default, and the
conversation_id still maps 1:1 to that one collection.

Mode B also dovetails with quota resumability: a suspended collection resumes by opening a
new conversation, never by appending to a polluted one.

---

## 5. Browser interaction sequence

All interactions use **raw CDP** (per ADR-027 §12 — high-level `browser_navigate` is
forbidden: it reuses the live user tab). Sequence per prompt:

1. **Resolve composer (dynamic, every time):** `Runtime.evaluate` →
   `document.querySelector('textarea')`. Assert present.
2. **Set value via React setter (never raw `el.value`):**
   ```js
   const ta = document.querySelector('textarea');
   const setter = Object.getOwnPropertyDescriptor(
     window.HTMLTextAreaElement.prototype, 'value').set;
   setter.call(ta, RENDERED_PROMPT);
   ta.dispatchEvent(new Event('input', { bubbles: true }));
   ta.focus();
   ```
3. **Resolve send button (dynamic, every time — NEVER cached coordinates):**
   ```js
   const send = [...document.querySelectorAll('button')]
     .find(b => (b.getAttribute('aria-label') || '') === 'Grok something');
   // fallback: button whose center is near textarea bottom-right corner
   const r = send.getBoundingClientRect();
   const x = Math.round(r.x + r.width/2), y = Math.round(r.y + r.height/2);
   ```
4. **Click with real mouse events (not `element.click()` / synthetic Enter — see §12.1):**
   ```js
   Input.dispatchMouseEvent({ type:'mousePressed', button:'left', x, y, clickCount:1 });
   Input.dispatchMouseEvent({ type:'mouseReleased', button:'left', x, y, clickCount:1 });
   ```
5. **Confirm submission:** poll until `textarea` value is `''` AND prompt text appears as
   an echoed user message.

**Dynamic-UI rules (binding):**
- Never cache coordinates across interactions. Resolve DOM targets immediately before each.
- Prefer semantic selectors (`aria-label`, tag) over coordinates.
- Coordinates are a **fallback only**, resolved fresh each time.

---

## 6. Completion detection

A prompt is **complete** only when **all** verified conditions hold (ADR-027 §13):

1. `textarea` value is empty (prompt submitted, composer reset).
2. Response text is present (the assistant message after the echoed prompt exists).
3. No `"Thinking about your request"` indicator in `document.body.innerText`.
4. No active spinner: no `[class*=animate-spin]` / `[role=progressbar]` element.

Additionally, a **per-prompt timeout** (default 120s, configurable) bounds the wait; on
timeout → FAILURE(INTERACTION: response-incomplete), stop-and-report. Polling interval
default 1.5s. Completion is re-checked twice (debounce) to avoid catching a mid-stream
transient "Thinking" flicker.

---

## 7. Extraction strategy

Goal: capture the **complete** assistant response for the just-submitted prompt, with no
truncation and a detectable boundary.

**Primary (semantic):** Grok renders the conversation as an ordered list of message blocks.
Locate the **last assistant message block** after the echoed user prompt. Prefer a stable
selector (e.g. a message-container `data-testid` / `article` if present); extract its
`innerText` (and optionally `innerHTML` for fidelity).

**Fallback (validated in ADR-027 §12/§13):** if no stable message selector exists, window
the page `innerText` between the echoed prompt and the next control marker
(`"Fast"` / suggestion-chip text / composer). This was proven to yield clean boundaries
for single- and multi-line responses.

**Boundary checks:**
- Assert the extracted block starts after the echoed prompt and ends before the next
  control marker.
- Assert `char_count > 0`.
- Flag `truncated: true` if a "Learn more" / "Continue" / cut-off indicator is detected,
  or if response ended mid-sentence with no terminator → FAILURE(EXTRACTION).

**Capture scope:** raw text always; full `innerHTML` optional (config). Screenshots are
**not** routine evidence (non-deterministic, large) — captured only on failure.

---

## 8. Raw evidence schema

Written **before** any normalization. Append-only, immutable.

```json
{
  "schema_version": "1.0",
  "provenance": {
    "collection_id": "<deterministic sha256, Amendment 1>",
    "prompt_id": "PROMPT-TREND-SCAN",
    "prompt_version": "1.2.0",
    "conversation_id": "<id from grok url>",
    "browser_session_id": "<chrome browser-context id if available, else null>",
    "collected_at": "2026-07-11T09:00:15Z",
    "collector_version": "1.0.0",
    "runtime_version": "ADR-027",
    "source": "grok",
    "endpoint": "https://x.com/i/grok"
  },
  "record_key": {
    "collection_id": "<deterministic>",
    "prompt_id": "PROMPT-TREND-SCAN",
    "prompt_version": "1.2.0"
  },
  "prompt": {
    "prompt_id": "PROMPT-TREND-SCAN",
    "version": "1.2.0",
    "prompt_text": "<rendered prompt>",
    "prompt_hash": "sha256:<hex>",
    "variables": { "topic": "AI tooling" }
  },
  "raw_response": "<complete Grok response text>",
  "raw_response_html": "<optional full html of response block>",
  "timestamps": {
    "submitted_at": "2026-07-11T09:00:01Z",
    "first_token_at": "2026-07-11T09:00:03Z",
    "completed_at": "2026-07-11T09:00:14Z",
    "extracted_at": "2026-07-11T09:00:15Z"
  },
  "browser": {
    "cdp_url": "http://127.0.0.1:9333",
    "target_id": "<automation tab id>",
    "user_agent": "<chrome ua>",
    "grok_url": "https://x.com/i/grok?conversation=<id>",
    "conversation_id": "<id>"
  },
  "model_metadata": {
    "model": "<if available>",
    "grok_variant": "<if available>"
  },
  "extraction": {
    "method": "dom_message_block | text_window",
    "boundary_detected": true,
    "truncated": false,
    "char_count": 1234
  },
  "quota": {
    "remaining_estimate": 17,
    "limit_hit": false
  }
}
```

**Provenance is mandatory (Amendment 3):** every field under `provenance` is required. The
collector MUST NOT write a raw record without all ten fields populated. `record_key`
`(collection_id, prompt_id, prompt_version)` enforces exactly-once preservation (§10).

---

## 9. Normalized schema

Normalization is **structural parsing only** — it does NOT judge importance, rank, filter,
or editorialize. It converts Grok's free text into a reproducible structured record and
links back to the raw evidence.

```json
{
  "schema_version": "1.0",
  "provenance": {
    "collection_id": "<deterministic sha256, Amendment 1>",
    "prompt_id": "PROMPT-TREND-SCAN",
    "prompt_version": "1.2.0",
    "conversation_id": "<id>",
    "browser_session_id": "<if available, else null>",
    "collected_at": "2026-07-11T09:00:15Z",
    "collector_version": "1.0.0",
    "runtime_version": "ADR-027",
    "source": "grok",
    "endpoint": "https://x.com/i/grok"
  },
  "record_key": {
    "collection_id": "<deterministic>",
    "prompt_id": "PROMPT-TREND-SCAN",
    "prompt_version": "1.2.0"
  },
  "raw_evidence_ref": "evidence/2026-07-11/<collection_id>/<prompt_id>@<prompt_version>.json",
  "sections": [
    { "heading": "<if present>", "body": "<text>" }
  ],
  "items": [
    { "index": 1, "text": "<a distinct finding/list item>", "embedded_links": ["<url>"] }
  ],
  "confidence": null,
  "notes": "<verbatim structural notes, e.g. 'response was a 3-bullet list'>"
}
```

- `confidence` is `null` by design — the collector does not assert signal quality.
- `items` are faithful extractions (bullets, lines, sections), not curated insights.
- Every normalized record carries full `provenance` (Amendment 3, mandatory) and
  `raw_evidence_ref` so Opportunity Discovery can audit the untransformed source.

---

## 10. Storage contract

- **Evidence Store (raw):** append-only, immutable files or DB rows keyed by
  `collection_id`. Layout: `evidence/<YYYY-MM-DD>/<collection_id>.json`. Never overwritten.
- **Normalized Store:** `normalized/<YYYY-MM-DD>/<collection_id>.jsonl` (one JSON object
  per line; idempotent append — re-run with same id is a no-op or versioned, never dup).
- **Interface:** an `EvidenceStore` abstraction (write_raw, write_normalized, read_raw,
  exists) so the backing store (filesystem / DB) is swappable without collector changes.
- **Retention:** raw evidence retained per data-retention policy; normalization may be
  compacted but raw is the source of truth.
- **The collector writes only to the Evidence Store + OD intake (§16).** It never writes to
  Opportunity Discovery's internal state or Editorial Brain.

---

## 11. Retry policy

Strict separation of **transport** vs **interaction** failures (ADR-027 §13):

**Transport failures (retryable):**
- CDP `Runtime.evaluate` / `Input.dispatch*` transient errors (`code: -32601`, timeouts,
  target-detached-temporarily).
- Policy: exponential backoff, max 3 attempts (250ms → 1s → 2s); re-`attach` target if
  detached; then proceed. If still failing after 3 → FAILURE(TRANSPORT).

**Interaction failures (stop-and-report, NO auto-retry):**
- Auth lost (no `twid`/`auth_token`, redirected to login).
- Composer / send button not resolvable after N dynamic attempts.
- Prompt not submitted (textarea not cleared after click).
- Response incomplete past per-prompt timeout.
- Extraction boundary missing / suspected truncation.
- Quota exhausted / rate-limited.
- Storage write failure.

This honors the PO's "stop immediately, report exact state, do not retry automatically"
rule for *logic* failures, while tolerating *transport* flakiness that the runtime
validation proved benign.

---

## 12. Failure taxonomy

| Code | Class | Trigger | Disposition |
|------|-------|---------|-------------|
| TRANSPORT | tooling | CDP glitch / timeout / temp detach | retry (§11), then FAILURE |
| AUTH | interaction | X session lost, login wall | STOP, alert PO (no re-login) |
| INTERACTION | interaction | composer/button missing, submit fails, timeout | STOP, report |
| EXTRACTION | interaction | boundary missing, truncation suspected | STOP, report |
| QUOTA | quota | rate-limit UI / 429 / limit reached | SUSPEND (resumable) |
| STORAGE | infra | write fails | STOP, report, keep raw in memory for recovery |

---

## 13. Validation plan

- **Unit (no quota):** prompt-registry hashing; schema validation of raw + normalized;
  boundary-detection on captured fixtures (the §13 conversation dump).
- **Integration (minimal quota, once, post-approval):** one collection, Mode B, 1 prompt;
  assert raw + normalized written, `conversation_id` linked, automation tab closed, user
  tab untouched, quota decremented.
- **Replay/determinism:** re-run same `prompt_id`+`version`; compare `prompt_hash` (must
  match) and record `raw_response` variance (Grok may not be byte-deterministic — document
  observed variance; the *process* is deterministic even if the *model* varies).
- **Quota path:** simulate limit-hit → assert SUSPENDED (exit 0, resumable), no quota burn.
- **Invariant test:** assert user tab URL unchanged before/after every run.

---

## 14. Production metrics

- `collector_collections_total` (ok / suspended / failed)
- `collector_prompts_submitted_total`
- `collector_completion_seconds` (histogram, per prompt)
- `collector_transport_retries_total`
- `collector_failures_total{class}` (TRANSPORT/AUTH/INTERACTION/EXTRACTION/QUOTA/STORAGE)
- `collector_extraction_truncated_total`
- `collector_quota_remaining` (gauge)
- `collector_tab_open_total` / `collector_tab_close_total` (should match)
- `collector_user_tab_touches_total` (must stay 0 — invariant alarm)

---

## 15. Scheduler integration

- Triggered by the profile's cron system (e.g. `cronjob` with `no_agent` script or an
  LLM-driven prompt). Schedule defined per collection (e.g. daily trend scan).
- **Single-concurrency lock:** a runlock file prevents overlap; a stalled run is detected
  by heartbeat, not by blindly starting a second tab.
- Reads prompt registry + quota budget at start; respects quota windows (suspend if
  budget exhausted for the window).
- On completion, delivers normalized records to the OD intake path (§16) and exits with
  status `DONE` / `SUSPENDED` / `FAILED`.
- Post-run report delivered to the home channel (summary only, not raw dumps).

---

## 16. Interfaces with Opportunity Discovery

- **Contract:** collector emits normalized records (§9) to a defined **intake drop zone**
  (e.g. `data/opportunity-intake/<YYYY-MM-DD>.jsonl`). OD consumes from there.
- **The collector passes ALL collected findings** — it does NOT filter, rank, or curate.
  Selection/judgement is OD's job.
- Each record carries `raw_evidence_ref` so OD (and downstream EB) can audit the source.
- The collector **never writes OD's internal state** — only the intake file/queue.

---

## 17. Interfaces with Editorial Brain

- The collector has **no direct interface** with Editorial Brain.
- EB consumes **Opportunity Discovery output**, not collector output. The collector's
  responsibility ends at the Evidence Store + OD intake.
- This boundary is enforced by design: the collector cannot bypass or duplicate OD or EB.
- If EB needs richer context, it pulls from the raw evidence via `raw_evidence_ref`.

---

## 18. Operational runbook

| Symptom | Action |
|---------|--------|
| Auth lost (AUTH) | STOP. Alert PO. **Do not** attempt re-login (out of collector boundary, ADR-027). |
| Rate limit / quota (QUOTA) | Graceful SUSPEND: record quota state, exit 0 (suspended). Resume next window. **Do not** burn quota on diagnostics. |
| Target detached (TRANSPORT) | Retry per §11; if persistent, FAIL safe — close tab, alert. |
| Submit/extract fail (INTERACTION/EXTRACTION) | STOP, report exact browser+DOM state + screenshot if available. No auto-retry. |
| Tab leak suspected | `finally` block guarantees `closeTarget`; alert if tab count drifts. |
| User tab modified (invariant breach) | Halt immediately, alert PO — this must never happen. |
| Storage write fails (STORAGE) | Keep raw in memory, retry once, then FAILURE with raw attached for recovery. |

---

## 19. Observability

- **Structured JSON logs**, correlated by `collection_id` + `conversation_id`.
- **Tracing:** one trace per collection; spans for attach/open/auth/submit/wait/extract/
  preserve/normalize/store/close.
- **Metrics:** exported per §14.
- **Failure evidence:** on any FAILURE, capture browser state (targets), DOM snapshot
  (text), and screenshot; attach to the alert.
- **No routine screenshots** (non-deterministic, costly) — only on failure.

---

## 20. Production readiness checklist

- [x] ADR-027 FROZEN and accepted as canonical runtime record.
- [x] Browser Runtime VERIFIED (smoke test, ADR-027 §12).
- [x] Interaction Model VERIFIED (multi-interaction, ADR-027 §13).
- [x] Canonical endpoint `x.com/i/grok` mandated (no `grok.com`).
- [x] Dynamic-UI rule: resolve DOM before every interaction; coords fallback only.
- [x] Transport vs interaction failure separation defined (§11).
- [x] Completion detection = 4 verified conditions + timeout (§6).
- [x] Conversation strategy = Mode B default, justified (§4).
- [x] Quota awareness: accounting + rate-limit detection + SUSPEND + resumable (§5/§11/§18).
- [x] Raw-evidence schema preserves prompt version, text, hash, timestamps, browser meta,
      conversation id, model meta, extraction meta, quota (§8).
- [x] Normalization after raw preservation; no editorial judgement (§9).
- [x] Storage contract append-only + EvidenceStore abstraction (§10).
- [x] OD interface = intake drop zone, all findings passed, no bypass (§16).
- [x] EB interface = none (consumes OD output) (§17).
- [x] New-automation-tab invariant enforced in `finally` (§1).
- [ ] Prompt registry seeded with v1 prompts (pre-implementation).
- [ ] Evidence Store provisioned (path/DB) (pre-implementation).
- [ ] Quota budget defined per window (pre-implementation).
- [ ] Scheduler configured with single-concurrency lock (pre-implementation).
- [ ] Metrics exporter wired (pre-implementation).
- [ ] Runbook published + alert routes configured (pre-implementation).

**Unchecked items are pre-implementation provisioning, not design gaps.**

---

## Appendix A — Relationship to ADR-027

This design is wholly dependent on and bounded by ADR-027:
- Runtime = Local Chrome via CDP (§1/§2 of ADR-027). Collector never manages it.
- New-automation-tab invariant (§6) → enforced in lifecycle `finally`.
- Canonical endpoint `x.com/i/grok` (§6.1) → hardcoded as the only navigation target.
- Submission technique (§12.1) → encoded in §5.
- Multi-interaction findings (§13: dynamic coords, transient CDP, completion heuristic)
  → encoded in §5/§6/§11.

ADR-027 remains FROZEN. This design does not revisit any runtime decision; it consumes the
proven runtime as an infrastructure dependency exactly like a database or filesystem.
