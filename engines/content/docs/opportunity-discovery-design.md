# Opportunity Discovery Engine — Design (Collector Intake Consumer)

**Status:** ACCEPTED — design v1 (Mission Mode, engine-content)
**Date:** 2026-07-11
**Profile:** engine-content
**Depends on (frozen, unchanged):**
- ADR-021/022/023 (Platform / Runtime / OSS boundaries)
- ADR-024 (Engine Operating Model) — Producer primitive
- ADR-026 (Review/Selection spine)
- ADR-027 (Local Chrome CDP runtime) — consumed, NOT modified
- COLLECTOR-DESIGN-001 §16 (Opportunity Discovery intake contract) — consumed, NOT modified
- `engines/content/collector/src/core/od_intake.py` (emitter) — frozen, NOT modified

---

## 1. Problem statement

The Grok Trend Intelligence Collector (RC2, production-validated in RC3) emits §9
normalized records to the Opportunity Discovery **intake drop-zone**:
`data/opportunity-intake/<YYYY-MM-DD>.jsonl` (design §16). The collector passes ALL
findings; it does not filter, rank, or curate — selection/judgement is Opportunity
Discovery's job.

**Gap:** the existing Opportunity Discovery Producer (`engines/content/lib/run_discover_stage.py`)
consumes RSS/feeds + the `signal_search_geo` Signal Collector, but it **never reads the
collector intake drop-zone**. The live Grok trend evidence produced and validated in RC3 has
no consumer — it never reaches the `score` stage or the Editorial Brain.

This violates the design-doc pipeline (§0):
```
Browser Runtime (frozen) -> Collector -> Evidence Store -> Opportunity Discovery
  -> Editorial Brain -> Publishing
```
The collector must not bypass or duplicate OD (§16/§17). Today OD silently bypasses the
collector.

---

## 2. Canonical architecture (no new primitive, no ADR change)

Opportunity Discovery is the **Producer** for the frozen Content Engine spine:
`Producer -> score(Stage) -> generate -> review -> select -> publish` (ADR-024/026).

The fix is to make the collector intake a **first-class Signal Collector** — a *reporter*
that converts each intake record into a Source item and hands it to the existing Producer
enqueue path. This is exactly the `signal_search_geo` pattern already in the codebase
("doc 20 §2: a Signal Collector is a reporter, not a decision-maker").

```
Collector (frozen)
  -> opportunity-intake/<date>.jsonl   (§16 drop-zone, RC3-validated)
        |
        v   [NEW: CollectorSignalCollector reporter]
  run_discover_stage.collector_intake_items(intake_dir)
        |
        v   (existing Producer: dedup + freshness + enqueue)
  eos_queue.enqueue("content", "score", payload)
        |
        v   [frozen spine]
  score -> generate -> review -> select -> publish
```

**What changes:** one new reporter module + a wiring block in the existing Producer.
**What does NOT change:**
- Collector (frozen) — no modification.
- Browser Runtime / Browser Adapter (frozen) — no modification.
- EOS queue / ADR-021..027 (frozen) — no new component, queue, stage, or schema.
- Editorial Brain / Content Engine / EOS / Publishing (frozen) — no modification.
- No new ADR required: this extends the existing Producer primitive using the existing §16
  contract (ADR-024 §5: "Producer is NOT a Stage… it enqueues. Never claims/completes.").

---

## 3. Signal Collector contract (reporter, not judge)

`CollectorSignalCollector` reads the intake drop-zone and returns Source-item dicts, already
shaped for the Producer's enqueue path. Per the design-doc principle (§16) and doc-20 §2,
it does **NOT** filter, rank, or curate:
- It does NOT apply `opportunity_score`/`is_opportunity` gating. The collector already passed
  ALL findings; OD selection happens downstream at `score`. Gating here would duplicate the
  collector's "pass everything" contract and risk dropping real evidence.
- It DOES discard structurally invalid intake lines (missing `record_key`/`provenance`/
  `raw_evidence_ref`) defensively — these cannot be audited or deduplicated, so they are
  skipped with a warning (the collector is trusted to emit well-formed records; a malformed
  line is a collector defect, not an OD concern).

Dedup/freshness against the Source window is delegated to the Producer (existing logic),
exactly like `signal_search_geo`.

---

## 4. Payload mapping (intake §9 -> enqueue payload)

The §9 normalized record becomes a Source item with this mapping (Producer -> `score`):

| Enqueue field | Source | Notes |
|---|---|---|
| `source_url` | `raw_evidence_ref` | Canonical, raw-auditable, stable for dedup (`is_source_known`). Never a fake URL. |
| `title` | first non-empty section heading, else first ~200 chars of body | Human-readable candidate title. |
| `content` | joined section bodies (the trend synthesis) | Full text handed to `score`. |
| `content_type` | `None` | Score assigns the AIVIS content_type (unchanged Producer behaviour). |
| `source_class` | `"B"` | External, public-verifiable (Grok trends = public X discussion). Class B is publishable. |
| `vendor` | `"grok:" + collection_id[:8]` | `content_types.extract_entity` maps `grok` -> `xai`; vendor diversity applies. |
| `source_kind` | `"collector_intake"` | Distinguishable in cycle/Editorial Memory. |
| `breaking` | `False` | Trend scan, not a breaking vendor event. (Producer's `_is_breaking` may promote if keywords match.) |
| `published` | `provenance.collected_at` | Freshness window uses this. |
| `discovered_at` | `now()` | Producer adds it. |
| `opportunity_score` | `None` | No OD gate upstream; selection is Score's job. |

The Source item also carries `record_key` + `raw_evidence_ref` so the Editorial Memory /
downstream can audit the untransformed source (design §16 / §9 `raw_evidence_ref`).

---

## 5. Configuration

- `intake_dir` is discovered via the **same default** the collector uses:
  `engines/content/collector/data/opportunity-intake` (relative to repo root), overridable by
  env `COLLECTOR_INTAKE_DIR` (so CI / cron can point at a specific drop-zone, e.g. the RC3
  fixture dir). No `policy.json` change needed; this is a path concern, not an engine policy.
- The reporter reads **all** `<date>.jsonl` files in `intake_dir` (not just today) so a
  backfilled or delayed collector run is still discovered. The Producer's freshness window
  still filters stale items.

---

## 6. Test / validation plan

- **Unit (`test_collector_signal.py`):** synthetic §9 records -> correct Source items;
  missing-field lines skipped; idempotent across re-reads; `source_url` derives from
  `raw_evidence_ref`.
- **Integration vs real RC3 artifact:** point `intake_dir` at the RC3 fixture
  (`collector/data/rc3/intake`) -> assert exactly one Source item produced with the real
  Grok trend text, correct `source_url = raw_evidence_ref`, `source_class = B`,
  `source_kind = collector_intake`. This is **real collector output** validation (no mock).
- **Producer wiring test:** `run_discover_stage.collector_intake_items()` returns the same
  shape as `signal_search_geo.collect()` consumers expect; no Supabase call (the test stops
  before `discover_once()`'s `_record_run`).
- **Regression:** full collector suite (66 tests) still green; no frozen module modified.

No real `eos_queue.enqueue` / Supabase call is made in tests (env-independent, like the
existing `test_od_intake.py` / `signal_search_geo` usage).

---

## 7. Production-readiness criteria (OD engine)

- [x] Collector intake is consumed (no silent bypass).
- [x] Records flow through the frozen Producer -> score spine with correct payload.
- [x] Dedup/freshness reuse existing Producer logic (no duplication).
- [x] No frozen subsystem modified.
- [x] Validated against real RC3 collector output.
- [ ] Live end-to-end production run (enqueue -> score -> ... -> publish) requires
      `AI_RUNTIME_*` + Supabase credentials and is an **irreversible external action**
      (AGENTS.md §9) — deferred to a later engine milestone / explicit PO approval. This is a
      Production-Evidence gate item, not an OD defects.
