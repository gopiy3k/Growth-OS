# Implementation Roadmap — RC1 → RC2 (Increment 4)

**Frozen:** 2026-07-11. This is the accepted Increment 4 plan. Implementation and
review MUST stay within its scope and execution order. It is committed as the
first Increment 4 commit and is not amended without PO approval.

## 1. RC1 Baseline

Tag `rc1` → merge commit `6406c64` (PR #1 merged to `main`).

- **ADR-027** — frozen Browser Runtime; canonical endpoint `https://x.com/i/grok`.
- **COLLECTOR-DESIGN-001 v1** — frozen collector design.
- **Increment 1** — `Identity`, `ResumeState`, `PromptRegistry`, `EvidenceStore`
  (in-memory), 7/7 unit tests PASS.
- **Increment 2** — `BrowserAdapter` abstraction + `CdpBrowserAdapter` + `cdp_session`,
  `BrowserAdapter` is the SOLE browser abstraction, 11/11 unit tests PASS.
- **Increment 3** — `GrokCollector` orchestrator (depends ONLY on `BrowserAdapter`
  + exceptions, `PromptRegistry`, `Identity`, `ResumeState`), `CollectionResult`
  (in-memory), `CollectorConfig`, `CollectionStatus` enum, 12/12 unit tests PASS;
  gated live e2e with hardened cleanup (user tab intact, no orphan targets).
- Collector is **evidence-only**: raw evidence held IN MEMORY. No persistence,
  normalization, OD emission, scheduler, or quota enforcement implemented yet.

## 2. Permanent Boundaries (unchanged, enforced)

The collector MUST NOT modify, import, or depend on:

- Content Engine
- Discovery implementation
- Editorial Brain
- EOS
- Publishing

**"OD intake wiring" (Q5) = collector-side emission of a stable artifact/contract
ONLY.** Opportunity Discovery is a *consumer* of that artifact; the collector never
reaches into Discovery, imports it, or depends on its implementation.

## 3. Increment 4 Phases — Revised Execution Order

```
Q0 → Q1 → Q4 → Q3 → Q2 → Q5 → Q6
```

| Phase | Title | Summary |
|-------|-------|---------|
| **Q0** | Quality hardening (no behavior change) | The 7 accepted review findings. Hygiene only. |
| **Q1** | Evidence Store persistence | Persist each `RawEvidenceRecord` durably (on-disk JSON keyed by `RecordKey`); exactly-once. |
| **Q4** | Resume persistence hardening | Atomic marker writes; in-flight vs completed distinction; crash-resumable. |
| **Q3** | Quota enforcement (`SUSPENDED`) | Wire `quota_limit`/`transport_retry_limit`; exhausted quota → `SUSPENDED` (resumable), not `FAILED`. |
| **Q2** | Normalization (raw → canonical) | Pure `normalize(raw)` → §8 canonical schema; collector-local; operates on durable raw. |
| **Q5** | OD intake emission (contract only) | Emit stable artifact/contract for Opportunity Discovery to consume. No OD dependency. |
| **Q6** | Scheduler hook (external) | Clean entrypoint/CLI invoking one `run_collection()`; no scheduler logic inside collector. |

## 4. Dependencies Between Phases

- **Q0 → Q1** — hygiene first; no behavior change before new capability land.
- **Q1 → Q4** — durable store must exist before resume hardening references it.
- **Q4 → Q3** — reliable resume required before quota suspension is robust.
- **Q1 → Q2** — normalization operates on durable raw evidence, not transient in-memory objects.
- **Q2 → Q5** — OD consumes *normalized* evidence, not raw records.
- **Q1..Q5 → Q6** — scheduler orchestrates already-completed capabilities; therefore last.

## 5. Acceptance Criteria Per Phase

### Q0 — Quality hardening (no behavior change)
- Remove unused `from pathlib import Path` in `collector.py`.
- Provenance `endpoint` = `config.endpoint` (passed through `RawEvidenceRecord.build`), not only the `identity` constant.
- Single `_fail(result, exc, status)` helper replaces the duplicated error-format line.
- `CollectionStatus.SUCCESS` doc comment corrected (all-skipped yields `SKIPPED`, not `SUCCESS`).
- `finally`-block cleanup tolerates `asyncio.CancelledError` (automation tab closed/detached under cancellation).
- `transport_retry_limit` either documented as reserved-for-Inc4 or removed; no silent unused field.
- Gated e2e no longer hardcodes `KNOWN_USER_TAB` (resolved at runtime or via env).
- **Behavior unchanged**: full suite green (Inc1 7 / Inc2 11 / Inc3 12); gated e2e still passes.

### Q1 — Evidence Store persistence
- Each `RawEvidenceRecord` persisted to the collector store (on-disk JSON keyed by `RecordKey.to_filename()`) immediately on preserve.
- Exactly-once: re-run with same `collection_id`/`prompt` does not duplicate; idempotent.
- Store path derived from config (collector data dir); **no hardcoded machine paths**.
- No modification of forbidden modules.
- Unit tests for write + idempotency without Grok quota.

### Q4 — Resume persistence hardening
- Atomic marker writes (temp + rename) to avoid corruption on crash.
- Distinction: in-flight (`SUBMITTED`) vs completed (`COMPLETED`); a crash mid-collect leaves `SUBMITTED` and is resumable (not silently skipped, not double-counted).
- Crash-recovery test: simulate interrupt, re-run, confirm resume from `SUBMITTED`.
- Backed by the durable store from Q1.

### Q3 — Quota enforcement (`SUSPENDED`)
- `config.quota_limit` wired; per-prompt loop tracks consumed quota; on exhaustion emit `CollectionStatus.SUSPENDED`, stop, clean up, leave resume state so the next run resumes.
- `transport_retry_limit` applied at orchestrator level where relevant (or explicitly documented as adapter-owned).
- `SUSPENDED` path tested with a fake adapter (quota exhausted after N prompts).
- Quota exhaustion is **never** a `FAILED`; it is resumable.

### Q2 — Normalization (raw → canonical)
- `normalize(raw_record)` → §8 canonical evidence schema; pure transform; collector-local.
- Operates on durable raw evidence from Q1 (reads stored raw, writes normalized).
- Unit tests for the normalization mapping.
- No editorial/OD logic; no forbidden-module modification.

### Q5 — OD intake emission (contract only)
- Collector emits a stable artifact/contract (e.g. normalized-evidence manifest / interface) consumable by Opportunity Discovery.
- Collector does NOT import/modify/depend on OD implementation.
- Emission is a side-effect of collection (writes artifact to a known contract location); no OD runtime dependency.
- Test that the artifact is well-formed and present.

### Q6 — Scheduler hook (external)
- Clean entrypoint/CLI invoking one `run_collection()` with config; no scheduler logic inside the collector.
- External scheduler can drive it; invocation documented.
- Smoke test of the entrypoint (dry / fake adapter).

## 6. RC2 Exit Criteria

- All Q0–Q6 merged to `main` via PR; full suite green (Inc1 7 / Inc2 11 / Inc3 12 / Inc4 new).
- Gated live e2e passes (user tab intact, no orphan targets, automation tab destroyed).
- `grep` confirms no leakage of storage / normalization / OD / scheduler / EditorialBrain / Publishing into Inc1–3 modules; forbidden modules untouched.
- All phase acceptance criteria (§5) met.
- Tagged **`rc2`** at the merge commit.
- Standalone recovery repo retained until RC2 verified.
- Branch `collector/rc0-migrate` deleted only **after** `rc2` is tagged (branch policy).

## 7. Branch & Workflow Policy

- Increment 4 work on branch **`collector/rc1-inc4`** (off RC1 / `origin/main`).
- Keep `collector/rc0-migrate` until Inc4 merged & verified; delete after `rc2` tagged.
- Per-phase commits; PR + PO review cadence as established (commit → PR → review).
- No direct push to `main`; all changes via PR.
