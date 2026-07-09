# AGENTS.md — Operating Instructions for Growth OS Agents

This file is the **canonical operating instructions** for any coding agent working in this
repository. Read it before making architecture, documentation, or commit decisions. It is
designed so a future agent can work **without reconstructing months of conversation**.

> The repository (ADRs, `docs/`, `engines/`) is the **permanent source of truth**. Chat is
> ephemeral. Never leave an accepted decision only in conversation.

---

## 1. Repository purpose

**Growth OS** is the Engineering OS platform (EOS) — a frozen execution spine for autonomous
engines. The `engines/content` engine implements **Continuous Content Production**: it
generates, reviews, selects, and publishes posts via Buffer (LinkedIn + X) using an AI Runtime
(HY3 / Hermes `engine-content` profile).

Public repo: `gopiy3k/Growth-OS`, Apache-2.0.

---

## 2. Architecture hierarchy (read top-down)

```
ADR-021/022/023  — Platform architecture (EOS, AI Runtime boundary, OSS boundary)  [FROZEN]
        ↓
ADR-024          — Engine Operating Model (how engines are built/operated)          [FROZEN]
        ↓
ADR-025          — Buffer transport contract
ADR-026          — Review Stage + Approved Pool + Selection mechanism
        ↓
docs/engine-operating-model.md, docs/architecture.md, docs/runtime-contract.md
        ↓
Implementation  — engines/*/lib + skills + config/policy.json
```

ADRs are the decision record. `docs/` explains them. Code implements them. If they disagree,
**the ADR wins** — fix the doc/code, do not "reinterpret" the ADR.

---

## 3. Runtime boundary (FROZEN — ADR-022)

- EOS consumes an OpenAI-compatible `/v1/chat/completions` contract. It knows only
  `AI_RUNTIME_BASE_URL`, `AI_RUNTIME_API_KEY`, and `AI_RUNTIME_MODEL` (telemetry only).
- EOS **never** names, selects, authenticates, or bootstraps a model/provider/transport.
- One engine = exactly one Runtime Context (one Hermes profile). Multiple engines = multiple
  profiles; one engine never spans profiles.
- **Generate and Review use the SAME `engine-content` Runtime Context.** They differ ONLY by
  `Stage` / `Skill` / `Prompt` / `Output Contract`. A future swap (e.g. Review → OpenCode)
  changes only the profile behind the same context slot — **zero EOS/Queue/Runtime/EOM change**.

---

## 4. Documentation discipline (MANDATORY)

- Every **accepted decision** MUST be promoted to disk (ADR / `docs/` / `policy.json`).
- `engineering_log.md` is **LOCAL ONLY** (gitignored). It is a working-memory scratch artifact,
  never the source of truth, never committed.
- If you reach an architecture decision in chat, **write it to the repo and say so** before
  considering the task done.
- Build logs / summaries / reports go in `PRs` and `docs/`, not in chat-as-truth.

---

## 5. Architecture freeze rules

These are **FROZEN**. Do not redesign, simplify, reinterpret, or "improve" them without an
explicit, authenticated decision from the user (genuine architectural contradiction or
authenticated external action):

- ADR-021 (EOS), ADR-022 (Runtime boundary), ADR-023 (OSS boundary), ADR-024 (EOM).
- The EOS queue contract (`engines/_shared/eos_queue.py`) and `migrations/0001_eos_queue.sql`.
- ADR-026: Review = Stage; Selection = §8 operational mechanism (NOT a Stage/Queue/EOS comp);
  Pending Review = `review` job with `status=pending` (no new status); Approved Pool = logical
  view (no new table); Review hard-rejects ONLY fabrication/hallucination/legal/harmful/severe_brand.
- No new EOS component, scheduler, queue, or schema change for engine operational behaviour.

### Adding new Constructs (the only allowed extension paths)

| Add… | How |
|---|---|
| **New Stage** | Reuse `eos_queue.enqueue(engine, <stage>, payload)` + a `run_<stage>_stage.py` driver. Terminal stage declares the `Outcome`. No EOS change. |
| **New Skill** | Add `engines/<engine>/skills/<name>/SKILL.md`. The worker executes it; stage/prompt/contract differ only. |
| **New Policy** | Edit `engines/<engine>/config/policy.json` (engine-owned values). Mechanism stays in EOS. |
| **New ADR** | `docs/adr/ADR-0NN-<topic>.md` with `Status`, `Depends on (frozen, unchanged)`, and explicit compatibility section. Never amend a frozen ADR's decision — supersede with a new one. |

---

## 6. Review Stage vs Selection (do not confuse)

| Concept | Nature | Owns AI reasoning? |
|---|---|---|
| **Review** | A **Stage** (`stage="review"`) | Yes — HY3 pressure-tests the draft |
| **Selection** | **§8 operational mechanism** (`run_select.py` → `selection.py`) | No — deterministic, Policy-driven |
| **Pending Review** | `review` job with `status="pending"` (NOT a new status) | — |
| **Approved Pool** | logical view over `content_engine_runs` (no new table) | — |

Publish jobs are created **ONLY by Selection** (`run_select.py`). `run_generate_stage.py`
enqueues `review`, never `publish`.

---

## 7. Ownership split (ADR-026 §8) — consistent across all docs

- **Growth OS platform (EOS)** owns the *mechanism*: scheduler, workers, queue, runs, retry, DLQ, runtime contract.
- **Content Engine** (in this repo) owns the *Policy values*: cadence, caps, thresholds, TTL,
  freshness/ranking weights, duplicate window, Selection algorithm (`policy.json`, `selection.py`).
- **AIVIS** (separate repo) owns the *product intent*: content strategy, pillars, brand voice,
  trust rules, audience, positioning, publishing philosophy. AIVIS `docs/content-intelligence-engine/17-publishing-policy.md` and `18-trust-rules.md` are the product-side mirror.

---

## 8. Validation discipline

Run harnesses before claiming completion:

```bash
python engines/content/validate_content_review.py     # Review: harmful→reject, clean→approve, R10 DLQ
python engines/content/validate_content_select.py     # Selection: S1–S10 deterministic
python engines/content/validate_content_publish.py    # Publish: real Buffer contract
python engines/content/validate_e2e_production.py     # End-to-end: real Runtime + real Buffer
```

Distinguish three evidence levels (do NOT conflate them):
- **Transport Validation** — contract/credentials/channels correct.
- **Real External Interaction** — real API calls happened (even a rejection proves this).
- **Clean Production Evidence** — one clean reproducible run with real external IDs captured.

Production Evidence is complete **only** at the third level, continuously.

---

## 9. Production Gate discipline

- A Stage whose Outcome depends on an external business system the engine does **not** own
  (Buffer, LinkedIn, X, …) is **NOT** Gate-Passed on implementation alone. It must be validated
  against the **real** system with **real** credentials.
- Live production rollout (recurring autonomous publishing) is an **irreversible external
  action** — requires explicit user approval. Agents must STOP and ask before any live publish
  run, and must never auto-declare "Production Complete" without the three evidence levels green.
- Per-channel character limits are a hard external constraint: X = 280 (truncate to ≤279 + "…"),
  LinkedIn = 3000. Enforce at the client layer; never fail the whole publication.

---

## 10. Commit discipline

- Never commit `.env`, `engineering_log.md`, or local scratch artifacts (`*.txt`, `.buffer_discovered.json`).
- Scope commits by milestone. Reference the driving ADR in the message.
- Do not commit unvalidated code. Run the harnesses first.
- Credentials stay in `.env` (gitignored). `.env.example` holds placeholders only.

---

## 11. Cross-repository discipline

Growth OS (platform/impl/runtime/EOS) and AIVIS (product/strategy/trust/brand) are **two repos**.
When an AIVIS product decision changes engine behaviour:
1. Update the AIVIS product doc (`17-publishing-policy.md`, `18-trust-rules.md`, etc.).
2. Update `policy.json` / engine code in Growth OS.
3. Keep both sides referencing each other. Add a supersession banner to any AIVIS doc whose
   human-review assumption was replaced by the automated Review Stage.

---

## 12. Future Runtime migration / OpenCode compatibility

- The Review reviewer may move to OpenCode and Selection may grow via richer Policy. Both are
  **purely engine-owned** swaps behind the frozen ADR-022 boundary — no platform change.
- Any future runtime MUST pass `tests/contract/` (mock runtime contract test).

---

## 13. Rules preventing architecture drift

1. `engines/_shared/eos_queue.py` and `migrations/0001_eos_queue.sql` are frozen — never edit
   for engine behaviour.
2. New queue *status* values are forbidden; reuse `pending`/`success`/`failed`.
3. New *tables* are forbidden for engine behaviour; use logical views over `content_engine_runs`.
4. Review and Generate MUST read identical `AI_RUNTIME_*` env and POST to the same endpoint.
5. Every accepted decision is on disk. If it's only in chat, it does not exist yet — promote it.

---

*This file is the contract between the user and every future agent. Honor it literally.*
