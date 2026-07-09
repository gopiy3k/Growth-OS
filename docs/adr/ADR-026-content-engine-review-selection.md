# ADR-026 — Content Engine: Review Stage, Approved Pool, and Selection Mechanism

- Status: Accepted
- Date: 2026-07-10
- Supersedes: none
- Amends: `docs/engine-operating-model.md` (§2, §5, §8 clarifications), `engines/README.md`
- Depends on (frozen, unchanged): ADR-021 (EOS), ADR-022 (AI Runtime boundary),
  ADR-023 (OSS boundary), ADR-024 (Engine Operating Model), ADR-025 (Buffer transport)

## Context

The Content Engine's accepted Business Capability is **Continuous Content Production**:
the engine should almost always publish at least one *good, safe* post per day, choosing
the highest-quality available draft rather than repeatedly rejecting everything against an
absolute threshold.

Prior to this decision the on-disk chain was:

```
Producer -> Score -> Generate -> Publish
```

Generate enqueued `publish` directly. There was no quality/safety pressure-test between
drafting and publication, and no mechanism to accumulate candidates and select the best.

## Decision

Adopt the following chain on the **frozen** platform, with **zero** changes to EOS, Queue,
DLQ, Runs, Runtime contract, or the platform ADRs:

```
Producer
  -> Score      (Stage, engine-content Runtime Context)
  -> Generate   (Stage, engine-content Runtime Context)
  -> [Review job: stage="review", status="pending"]   <- "Pending Review"
  -> Review     (Stage, engine-content Runtime Context)
  -> Approved Pool   (logical view over content_engine_runs)
  -> Selection  (engine-internal operational mechanism, EOM §8 — NOT a Stage)
  -> Publish    (terminal Stage)
```

### 1. Review is a Stage in the SAME Runtime Context

Review is an engine **Stage** (EOM §5: transforms exactly one job). It reuses the identical
`engine-content` Runtime Context that Score and Generate already use — the same
`AI_RUNTIME_BASE_URL` / `AI_RUNTIME_API_KEY` / `AI_RUNTIME_MODEL` and the same
`/v1/chat/completions` contract (ADR-022). It is **NOT** another AI, model, runtime, or
engine. Generate and Review differ ONLY by:

- **Stage** (`generate` vs `review`)
- **Skill** (`generate-content-post` vs `review-content-post`)
- **Prompt** (`_SYSTEM_PROMPT`)
- **Output Contract** (`_normalize` target schema)

This mirrors how Score and Generate already coexist on one context (proof:
`run_score_stage.py` and `run_generate_stage.py` read identical `AI_RUNTIME_*` env and POST
to the same endpoint).

### 2. Review treats Generate output as UNTRUSTED input (invariant)

Review NEVER assumes Generate is correct. The draft is untrusted input to be
pressure-tested. Review transforms ONE draft; it NEVER ranks the pool.

### 3. Review hard-rejects ONLY unsafe/invalid content

Hard rejection (`approved=false`) is reserved for:

- `fabrication`
- `hallucination`
- `legal` (legal/compliance)
- `harmful`
- `severe_brand` (severe brand violation)

Everything else receives `approved=true` plus `overall_score`, `confidence`, `issues[]`,
`reasoning`, `topic_hash`, `evergreen`, `category`. Review never lowers safety to satisfy
throughput; "almost never reject everything" is a **Selection** guarantee, not a Review one.

### 4. Frozen Review Output Contract (v1)

```json
{
  "approved": true,
  "overall_score": 0,
  "confidence": "HIGH|MEDIUM|LOW",
  "issues": [{"type": "string", "severity": "hard|soft", "detail": "string"}],
  "reasoning": "string (<= 500 chars)",
  "topic_hash": "string (engine-computed, deterministic)",
  "evergreen": false,
  "category": "product|industry|competitor|research|opinion|noise",
  "publish_after": null,
  "model_version": "string (runtime-published telemetry)",
  "review_contract_version": "1",
  "policy_version": "string",
  "selection_algorithm_version": "string"
}
```

This contract is **frozen**; it must never be changed silently. `topic_hash` is computed by
the driver (engine-side), never trusted from the model. `approved` is enforced by the driver:
`approved=false` iff at least one `issue` has `severity="hard"` and a `type` in the hard set.

### 5. Pending Review is NOT a new queue status

"Pending Review" is simply a job with `stage="review"` and `status="pending"`. No new status
value, no schema change, no queue redesign.

### 6. Approved Pool is a logical view — NO new table

The Approved Pool is the set of `content_engine_runs` rows where `stage="review"`,
`status="success"`, `result_json.approved=true`, that are **not yet published** (no successful
`publish` run for the same `source_url`) and **not expired** (age <= TTL, unless evergreen).
No new EOS component or table is introduced (EOM §8).

### 7. Selection is the EOM §8 operational mechanism — NOT a Stage

Selection is deterministic engine-internal code controlled by **Policy**. It is NOT a Stage,
Runtime, EOS component, Queue, or Worker, and owns NO AI reasoning. It reads the Approved
Pool and performs: ranking, diversity, freshness, evergreen, TTL, duplicate suppression,
publish caps. It enqueues `publish` for winners (idempotently) and writes an observability
run (`stage="select"`).

Determinism: running Selection twice over the same pool produces identical output. Tie-break
order: (1) composite score, (2) confidence, (3) freshness, (4) diversity contribution,
(5) created_at, (6) stable draft id (`source_url`).

### 8. Ownership split (clarifies EOM §2/§8)

- **Growth OS platform (EOS)** owns the *mechanism*: scheduler, workers, queue, runs, retry,
  DLQ, runtime contract.
- **The Content Engine** (in the Growth OS repo, engine-owned config) owns the *Policy
  values*: cadence, caps, thresholds, TTL, freshness/ranking weights, duplicate window, and
  the Selection algorithm.
- **AIVIS** owns the *product intent*: content strategy, pillars, brand voice, trust rules,
  audience, positioning, publishing philosophy — including the business rationale for caps
  and cadence.

### 9. Starvation is engine Policy (configurable, not hardcoded)

If no pooled draft meets `min_safe_score`, behaviour is governed by
`starvation_behaviour` Policy:

- `publish_best_safe` — publish the highest-scored **safe** draft (never unsafe), or
- `skip` — publish nothing that day.

Unsafe content is NEVER published under any policy. Default: `publish_best_safe`.

### 10. Future compatibility (guaranteed by ADR-022)

- Review runtime may later become OpenCode: swap the profile behind the same context slot;
  zero EOS/Queue/Runtime/Stage/EOM change (ADR-022 §"When OpenCode is proven…").
- Selection evolves via Policy only; no platform change.

## Consequences

- New engine artifacts: `run_review_stage.py`, `review-content-post` skill,
  `validate_content_review.py`, `selection.py` + `run_select.py`,
  `validate_content_select.py`, `config/policy.json`.
- Generate now enqueues `review` (not `publish`). Publish jobs are created ONLY by Selection.
- No platform code, schema, or ADR-021/022/023/024/025 change.

## Versioning

Persisted for explainability: `review_contract_version`, `policy_version`,
`selection_algorithm_version`, `prompt_version` (in run records).
