---
name: review-content-post
description: >-
  Content Engine stage "review". Consumes one generated draft from the EOS
  content_engine_queue (stage=review) and pressure-tests it via the AI Runtime for factual
  accuracy, hallucination, safety, and brand alignment. Emits a frozen review contract
  (approved/overall_score/confidence/issues/reasoning/topic_hash/evergreen/category).
  Use when the engine Worker is processing the 'review' stage.
engine: content
stage: review
---

# review-content-post (Content Engine)

The THIRD stage of the Content Engine. Reuses the EXACT same EOS execution spine and the
SAME `engine-content` Runtime Context as `score` and `generate` — it is NOT another AI,
model, runtime, or engine. It differs from `generate` ONLY by Stage, Skill, Prompt, and
Output Contract.

## Core invariant: Generate output is UNTRUSTED

Review NEVER assumes the draft is correct. Treat the draft as untrusted input to be
adversarially pressure-tested. Review transforms ONE draft; it NEVER ranks the pool
(ranking is the Selection mechanism's job, EOM §8).

## Input (queue payload_json, produced by the generate stage)

```json
{
  "source_url": "string",
  "draft_title": "string",
  "draft_body": "string (<= 1200 chars)",
  "tone": "professional|casual|thought_leadership",
  "generated_from": { "title": "...", "content": "...", "score": 0, "category": "...", "rationale": "..." }
}
```

## Execution (Worker runs this)

```
python -m engines.content.lib.run_review_stage
```

The driver: `claim(engine="content", stage="review")` -> pressure-test via AI Runtime ->
`complete(job_id, result, source_url=...)` (which becomes an Approved Pool entry when
`approved=true`) or `fail(job_id, error)`. Review does NOT enqueue publish — Selection does.

## Review reasoning (the skill logic)

Pressure-test the draft across: factual accuracy, hallucination detection, evidence
quality, brand alignment, usefulness, originality, engagement potential, safety.

**Hard-reject ONLY** (set an issue with `"severity":"hard"` and one of these `type`s):

- `fabrication` — unsourced/unsupported factual claim
- `hallucination` — claim contradicted by its own source or internally inconsistent
- `legal` — legal/compliance/IP/ToS risk
- `harmful` — harmful/unsafe content
- `severe_brand` — brand violation severe enough to damage trust

Everything else: `approved=true` with a score and soft issues.

## Output contract (frozen — do NOT widen; see ADR-026 §4)

Emit STRICT JSON:

```json
{
  "approved": true,
  "overall_score": 0,
  "confidence": "HIGH|MEDIUM|LOW",
  "issues": [{"type": "string", "severity": "hard|soft", "detail": "string"}],
  "reasoning": "string (<= 500 chars)",
  "evergreen": false,
  "category": "product|industry|competitor|research|opinion|noise"
}
```

The driver adds/enforces: `topic_hash` (computed engine-side, never trusted from you),
`publish_after`, `model_version`, `review_contract_version`, `policy_version`,
`selection_algorithm_version`, and re-derives `approved` from the presence of a hard issue.

## Constraints (discipline)

- Single stage, single reasoning step. No publishing, scheduling, ranking, or selection.
- Never lower safety to increase throughput.
- Never print or log the service-role key.
- On any failure call `queue.fail` so the job retries or routes to the DLQ.
- The EOS never names a model/provider; reasoning is via the AI Runtime contract (ADR-022).
