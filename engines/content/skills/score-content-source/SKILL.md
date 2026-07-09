---
name: score-content-source
description: >-
  Content Engine stage "score". Consumes one job from the EOS content_engine_queue,
  performs a lightweight editorial scoring of an external content item (provider-agnostic —
  the item arrives as queue payload, not fetched by this skill), and persists the structured
  result to Supabase. Use when the engine Worker is processing the 'score' stage.
engine: content
stage: score
---

# score-content-source (Content Engine)

Proves the Engineering OS execution spine for one reasoning stage:
**job source → Worker → Skill → structured JSON → Supabase.**

This skill is intentionally simple. The "reasoning" it performs is a single editorial
classification of one content item. The point being validated is the *autonomous loop*,
not the quality of the scoring.

## Provider-agnostic contract

Do **not** hardcode any RSS/Reddit/YouTube/GitHub provider here. The content item is
delivered to this skill as a queue `payload_json` by the Worker. The payload shape is:

```json
{
  "title": "string",
  "content": "string (optional excerpt/body)",
  "source_url": "string (the external item's URL)",
  "source_type": "string (optional: rss|reddit|youtube|github|other)"
}
```

The Worker is responsible for sourcing the item (from a configured source, an RSS
fetch, or any future provider) and enqueuing it. This skill only reasons over the payload.

## Execution (Worker runs this)

The Worker invokes a single driver that performs the full claim → reason → persist cycle
using the shared EOS queue client. From the terminal:

```
python -m engines.content.lib.run_score_stage
```

The driver:
1. Calls `eos_queue.claim(engine="content", stage="score")`.
2. If no job, exits 0 (queue empty — normal).
3. Otherwise, performs the scoring reasoning and calls `eos_queue.complete(job_id, result, source_url=...)`,
   or `eos_queue.fail(job_id, error)` on exception.

## Scoring reasoning (the simple skill logic)

Given the payload item, decide:
- `score` (integer 0–10): editorial relevance/quality for a general B2B audience.
- `category` (string): one of `product|industry|competitor|research|opinion|noise`.
- `decision` (string): `approve` or `reject`.
- `rationale` (string, <= 280 chars): one-line reason.

Emit the structured result as:

```json
{
  "score": 0,
  "category": "string",
  "decision": "approve|reject",
  "rationale": "string"
}
```

## Constraints (discipline)

- One queue, one worker, one skill, one reasoning stage, one structured output, one persistence step.
- No publishing, scheduling, images, optimization, or additional reasoning stages.
- Never print or log the service-role key. The driver reads credentials from the environment.
- On any failure, call `queue.fail` so the job either retries or routes to the DLQ — never silently drop.
