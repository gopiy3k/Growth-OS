# Opportunity Discovery Engine — Consolidated Report

**Status:** Engine built & validated (Mission Mode, engine-content profile)
**Date:** 2026-07-11
**Profile:** engine-content
**Depends on (frozen, unchanged):** ADR-021..027, COLLECTOR-DESIGN-001 §16/§9, `engines/content/collector/src/core/od_intake.py` (emitter).

---

## 1. Mission

Own the **Opportunity Discovery Engine** end-to-end using RC3 collector artifacts, respecting
frozen module boundaries. Deliver a working, independently reviewable OD engine that consumes the
Grok Trend Intelligence Collector's `opportunity-intake` drop-zone (§16) and feeds the frozen
Content Engine verification spine (`score -> generate -> review -> select -> publish`) — closing
the pipeline gap:

```
Browser Runtime (frozen) -> Collector -> Evidence Store -> Opportunity Discovery
  -> Editorial Brain -> Publishing
```

Today (pre-fix) OD silently bypassed the collector; the live RC3 trend evidence had **no consumer**.

---

## 2. What was delivered (committed on `od/collector-intake`, PR #4)

| # | Commit | Artifact | Role |
|---|--------|----------|------|
| 1 | `3535166` | `docs/opportunity-discovery-design.md` | Canonical design — collector intake → score (ACCEPTED) |
| 2 | `f0f91bf` | `lib/collector_signal.py` | **CollectorSignalCollector** — read-only bridge to the FROZEN §16 drop-zone |
| 3 | `f4748d8` | `lib/run_opportunity_discovery.py` | Standalone ingestion driver (Producer, no frozen-module touch) |
| 4 | `691dd66` | `lib/tests/test_opportunity_discovery.py` | 6 unit tests vs **real RC3 fixture** |

**Design principle (no scope creep):** OD is a *reporter*, not a judge (doc-20 §2). It does NOT
filter/rank/curate — the collector already passes ALL findings; selection is the `score` stage's
job. Zero frozen modules modified. No new Stage / schema / queue.

### 2.1 CollectorSignalCollector (`collector_signal.py`)
- Reads **all** `<date>.jsonl` in the §16 drop-zone (`engines/content/collector/data/opportunity-intake`,
  overridable via `COLLECTOR_INTAKE_DIR`).
- Maps each §9 normalized record → Source item (`source_url = raw_evidence_ref`, `source_class = B`,
  `vendor = "grok:" + collection_id[:8]`, `source_kind = collector_intake`).
- Defensively skips structurally invalid lines (missing `record_key`/`provenance`/`raw_evidence_ref`)
  with a warning — these can't be audited/deduped.
- Carries `record_key` + `raw_evidence_ref` so the untransformed source stays auditable downstream.

### 2.2 Ingestion driver (`run_opportunity_discovery.py`)
- `discover_once(intake_dir)` → reads intake → dedups via the frozen `ce_queue.is_source_known`
  (same boundary as the Producer) → enqueues to `score` via `ce_queue.enqueue("content","score",payload)`.
- Opens one Editorial Memory cycle for observability parity (non-fatal).
- Starts an Editorial Memory cycle; records candidates.
- ** Evidence fidelity:** each enqueued payload carries the four provenance fields
  (`raw_evidence_ref`, `record_key`, `collector_version`, `endpoint`) so downstream stays auditable
  back to the raw collector artifact. (These now survive end-to-end — see §5.)

---

## 3. Validation evidence (real, not mocked)

- **Real RC3 fixture:** `engines/content/collector/data/opportunity-intake/2026-07-11.jsonl`
  — 11 records, 8978 bytes (genuine Grok trend output, handles `@aiseomastery`, `@Mayank_Msd`,
  `@auxten`; collection_id `55d63dd9…`; prompt `PROMPT-TREND-SCAN@1.2.0`).
- **Test suite:** `python -m pytest engines/content/lib/tests/test_opportunity_discovery.py`
  → **6 passed** (driver enqueue shape, idempotent dedup, real-fixture contract, empty-intake safety).
- **Driver smoke:** against the real drop-zone, `discover_once` enqueues 11 with 0 dup on first run,
  0 new / 11 dup on re-run (dedup boundary verified).

---

## 4. Production-readiness criteria (OD engine)

| Criterion | Status |
|-----------|--------|
| Collector intake consumed (no silent bypass) | ✅ |
| Flows through frozen Producer → score spine with correct payload | ✅ |
| Dedup/freshness reuse existing Producer logic | ✅ |
| No frozen subsystem modified | ✅ |
| Validated against real RC3 collector output | ✅ |
| Live end-to-end production run (enqueue → … → publish) | ⏸ deferred |

The deferred item is an **AGENTS.md §9 Production-Evidence gate**: a live run needs `AI_RUNTIME_*`
+ Supabase credentials and is an irreversible external action — explicit PO approval required.
This is a deliberate gate, **not** an OD defect.

---

## 5. Cross-cutting fix: ENGINE-009 (separate PR #5, NOT an OD feature)

The collector provenance contract (`raw_evidence_ref`, `record_key`, `collector_version`,
`endpoint`) was being **dropped at the Score stage boundary** (only `source_url` survived),
breaking lossless audit in the Approved Pool. This was discovered *through* OD (the OD egress
carried the fields; the frozen spine lost them).

- **Fix (contract-preserving, infrastructure):** ride the four fields through existing
  `result_json`/`payload_json` jsonb at each stage — Score result, Generate result + generate→review
  payload, Review `result_json` (the Approved Pool row). No new queue/stage/schema.
- **Branch:** `engine009/provenance-contract` (from `origin/main`), **standalone PR #5**, independent
  of `od/collector-intake`.
- **Test:** `lib/tests/test_engine009_provenance.py` drives the **real frozen** `score → generate →
  review` stages (stubbed `requests.post`) and asserts all four fields survive to the Approved Pool
  view, and are never fabricated. **2 passed.**

**Deliberate separation maintained:** infrastructure fix (PR #5) and feature development (PR #4) are
independent, mergeable on their own. The ENGINE-009 test lives only on its fix branch; it was
explicitly **removed** from the OD working tree to keep the two efforts cleanly separable.

---

## 6. Next milestones (autonomous continuation)

1. **Rebase/merge care:** if PR #5 merges first, rebase `od/collector-intake` onto `main` (the
   provenance fields will then also be auditable on the OD enqueue path with zero OD code change).
2. **OD → score hand-off verification:** once `AI_RUNTIME_*` + Supabase are available with PO
   approval, run one real `discover_once → score → …` chain to capture clean production evidence
   (AGENTS.md §8 three evidence levels).
3. **Editorial Brain parity:** confirm OD candidates surface in the Editorial Brain cycle view
   (observability, non-blocking).
4. **CI:** add the OD test + ENGINE-009 test to the pipeline gate (both are env-independent).

---

## 7. Files (canonical, on `od/collector-intake`)

- `engines/content/docs/opportunity-discovery-design.md` (design, ACCEPTED)
- `engines/content/lib/collector_signal.py`
- `engines/content/lib/run_opportunity_discovery.py`
- `engines/content/lib/tests/test_opportunity_discovery.py` (6 tests)

## 8. PRs

- **PR #4** — Opportunity Discovery Engine (feature): `od/collector-intake` → `main`, open, mergeable.
- **PR #5** — ENGINE-009 provenance fix (infrastructure): `engine009/provenance-contract` → `main`,
  open, mergeable, **independent**.
