# Engines

An **engine** is a self-contained unit of autonomous work in Growth OS. Every engine reuses the
same EOS execution spine:

```
job source ──▶ content_engine_queue ──▶ Worker (claim → reason → persist) ──▶ content_engine_runs
                                   │                                            └─▶ content_engine_dlq (on exhaustion)
                                   └─ reason step calls the AI Runtime (runtime-contract.md)
```

The spine is **engine-agnostic**: the queue row is `(engine, stage, payload_json)`. Adding an
engine is adding a new `engine` id and (optionally) new `stage` values — no changes to
`engines/_shared/eos_queue.py` or the schema.

## Reference engine: `content`

`engines/content` is the proven reference implementation (validated live end-to-end). It has
**four stages** on the same engine — `score`, `generate`, `review`, and `publish` — plus the
**Selection** mechanism (a §8 operational mechanism, NOT a stage), all reusing the identical EOS
spine and the same `engine-content` Runtime Context. This proves one engine can host multiple
stages with zero EOS changes.

| Path | Role |
|---|---|
| `engines/_shared/eos_queue.py` | **Shared** queue/DLQ/runs client. Single source of truth. Do not duplicate. |
| `engines/content/lib/run_score_stage.py` | Worker driver for the `content`/`score` stage: `claim → reason_item → complete/fail`. |
| `engines/content/lib/run_generate_stage.py` | Worker driver for the `content`/`generate` stage (enqueues `review`, not `publish`). |
| `engines/content/lib/run_review_stage.py` | Worker driver for the `content`/`review` stage (ADR-026): pressure-tests the draft in the SAME Runtime Context; treats Generate output as untrusted; enforces the frozen review contract. |
| `engines/content/lib/selection.py` | **Pure** deterministic Selection algorithm (the §8 mechanism). No I/O, no AI. |
| `engines/content/lib/pool_client.py` | Engine-owned Approved Pool read/writes over `content_engine_runs` (logical view, no new table). |
| `engines/content/lib/run_select.py` | Selection driver (§8 mechanism): reads the pool, runs `selection.select()`, enqueues `publish` for winners. NOT a stage. |
| `engines/content/lib/run_publish_stage.py` | Worker driver for the `content`/`publish` stage (terminal; real Buffer). Created ONLY by Selection. |
| `engines/content/config/policy.json` | Engine Policy values (cadence, caps, TTL, ranking weights, starvation behaviour). |
| `engines/content/skills/score-content-source/SKILL.md` | Skill executed by the Worker (the `score` reasoning contract). |
| `engines/content/skills/generate-content-post/SKILL.md` | Skill executed by the Worker (the `generate` reasoning contract). |
| `engines/content/skills/review-content-post/SKILL.md` | Skill executed by the Worker (the `review` reasoning contract). |
| `engines/content/validate_m1.py` | Hermetic validation harness for the `score` stage. |
| `engines/content/validate_content_generate.py` | Hermetic validation harness for the `generate` stage. |
| `engines/content/validate_content_review.py` | Hermetic validation harness for the `review` stage (real Runtime pressure-test). |
| `engines/content/validate_content_select.py` | Hermetic harness for the pure Selection algorithm (deterministic, no network). |

**Content Engine chain (frozen architecture, ADR-026):**

```
Producer → Score → Generate → [review job: pending] → Review → Approved Pool → Selection → Publish
```

`Pending Review` is a `review` job in `pending` (no new status). `Review` and `Generate` share
one Runtime Context; they differ only by Stage/Skill/Prompt/Contract. `Selection` is deterministic
engine-internal Policy (§8), not a stage. `Publish` is created only by Selection.

## Adding a new engine (M3 and beyond)

1. **Create the engine directory**
   ```
   engines/<new>/lib/run_<stage>_stage.py
   engines/<new>/skills/<new>-<stage>/SKILL.md
   ```
2. **Reuse the shared client** — `import eos_queue` from `engines/_shared/` (add that dir to
   `sys.path`, as the reference driver does). Never write a second queue client.
3. **Define the engine + stage** strings (e.g. `ENGINE="geo"`, `STAGE="audit"`).
4. **Implement the driver** as `claim(ENGINE, STAGE) → reason_item(payload) → complete/fail`.
   - `reason_item` speaks ONLY the AI Runtime contract (`runtime-contract.md`, ADR-022). It must
     never name a model, provider, or transport.
   - The result must be strict JSON (one object). Persist via `eos_queue.complete`.
5. **Write a skill** (`SKILL.md`) describing the reasoning contract for that stage. Keep it
   provider-agnostic: the payload arrives via the queue, not fetched by the skill.
6. **Validate** by copying `validate_m1.py` as `validate_<engine>.py` and pointing it at your
   `ENGINE`/`STAGE`. Run it against a dev Supabase (or the contract mock — see `tests/contract`).

## Rules (enforced by ADR-022 / ADR-023)

- The EOS never owns model selection, provider auth, or runtime lifecycle.
- Engine code never references a specific product, user, org, or billing concept.
- Credentials come from the Worker environment (`SUPABASE_*`, `AI_RUNTIME_*`); never committed.
- One engine, one stage per unit; ship independently testable and revertible.
