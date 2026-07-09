---
name: generate-content-post
description: >-
  Content Engine stage "generate". Consumes one approved, scored content item from the EOS
  content_engine_queue and drafts a publication-ready post via the AI Runtime. Persists the
  structured draft to Supabase. Use when the engine Worker is processing the 'generate' stage.
engine: content
stage: generate
---

# generate-content-post (Content Engine)

The SECOND stage of the Content Engine. Proves that one engine can host multiple independent
stages on the frozen Growth OS platform, reusing the exact same EOS execution spine as the
`score` stage (no EOS changes).

## Provider-agnostic contract

Do **not** hardcode any source provider. The item arrives as a queue `payload_json` (already
scored/approved by the `score` stage). The payload shape is:

```json
{
  "title": "string",
  "content": "string (excerpt/body)",
  "source_url": "string (the external item's URL)",
  "score": 0-10,
  "category": "product|industry|competitor|research|opinion|noise",
  "rationale": "string (the score stage's editorial reason)"
}
```

This stage only reasons over the payload — it fetches nothing.

## Execution (Worker runs this)

The Worker invokes a single driver that performs the full claim → reason → persist cycle
using the shared EOS queue client. From the terminal:

```
python -m engines.content.lib.run_generate_stage
```

The driver:
1. Calls `eos_queue.claim(engine="content", stage="generate")`.
2. If no job, exits 0 (queue empty — normal).
3. Otherwise, drafts the post via the AI Runtime and calls `eos_queue.complete(job_id, result, source_url=...)`,
   or `eos_queue.fail(job_id, error)` on exception.

## Generation reasoning (the skill logic)

Given the approved, scored item, produce a publication-ready post:

- `draft_title` (string): short, faithful headline.
- `draft_body` (string, markdown, <= 1200 chars): the post body, faithful to the source.
- `tone` (string): one of `professional` | `casual` | `thought_leadership`.
- `word_count` (int): length of `draft_body`.

Emit the structured result as:

```json
{
  "draft_title": "string",
  "draft_body": "string",
  "tone": "professional|casual|thought_leadership",
  "word_count": 0
}
```

The `model` field is added by the driver (runtime-published telemetry) — do not emit it.

## Constraints (discipline)

- Single stage, single reasoning step. No publishing, scheduling, scoring, or images.
- Never print or log the service-role key. The driver reads credentials from the environment.
- On any failure, call `queue.fail` so the job either retries or routes to the DLQ — never silently drop.
- The EOS never names a model/provider; the reasoning is produced via the AI Runtime contract.
