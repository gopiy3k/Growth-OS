# Engine Operating Model

The **Engine Operating Model** is Layer 2 of the Growth OS architecture. Layer 1 is the
platform architecture (ADR-021 EOS, ADR-022 AI Runtime boundary, ADR-023 OSS boundary),
which is frozen and which engines never modify. This document is the living specification
for how an engine is built and operated on the frozen platform. It is recorded as a
decision in `docs/adr/ADR-024-engine-operating-model.md`.

> Scope: engine construction + operation only. It does not redefine platform primitives;
> it classifies and constrains their use by engines.

---

## 1. Two-layer model

| Layer | Source of truth | May engines change it? |
|---|---|---|
| Platform Architecture (ADR-021/022/023) | `docs/adr/ADR-02*.md` | No — frozen |
| Engine Operating Model (this doc + ADR-024) | `docs/engine-operating-model.md` | Yes — engine-owned rules |

Engines reuse the platform primitives exactly. No engine introduces a new platform
component, scheduler, or abstraction.

---

## 2. Three categories of engine construct

| Category | Owner | Members | Notes |
|---|---|---|---|
| **Primitives** | Platform (reused) | `Producer`, `Queue`, `Stage` | Single source of truth in `engines/_shared/eos_queue.py`. |
| **Declaration** | Engine | `Business Capability`, `Outcome` | The engine's stated intent and the terminal result. |
| **Policy** | Engine (config) | cadence, thresholds, sources, caps, retry, backlog, **Selection algorithm**, **Approved Pool TTL/freshness/ranking weights** | Engine-owned config; mechanism stays in the EOS. Lives in `engines/content/config/policy.json`. |

**Three-way ownership split (ADR-026 §8):** (a) **Growth OS platform (EOS)** owns the
*mechanism* — scheduler, workers, queue, runs, retry, DLQ, runtime contract. (b) **The
Content Engine** (in the Growth OS repo, engine-owned config) owns the *Policy values* — cadence,
caps, thresholds, TTL, freshness/ranking weights, duplicate window, and the Selection algorithm.
(c) **AIVIS** owns the *product intent* — content strategy, pillars, brand voice, trust rules,
audience, positioning, publishing philosophy — including the business rationale for caps/cadence.

Logic that belongs to the engine (e.g., dedup *decision*, Selection *algorithm*) lives in engine
code; the platform provides the *query* (e.g., `eos_queue.is_source_known`).

---

## 3. Engine identity = Business Capability

An engine's identity is its **Business Capability** (what autonomous outcome it delivers),
not its Outcome and not a capability tuple.

- Example: Content Engine identity = "Continuous Content Production."
- The **Outcome** is the *purpose* the terminal Stage achieves (e.g., a published post).
- Rejected in review: "Outcome defines identity" and "identity = capability tuple."

---

## 4. One engine = exactly one Runtime Context

- Runtime Implementation v1 = one Hermes Profile (`engine-content`, `engine-geo`, …)
  publishing the `AI_RUNTIME_*` contract (ADR-022).
- **One engine binds to exactly one Runtime Context.** Multiple engines = multiple
  profiles; one engine never spans profiles.
- Rejected in review: "one engine = one worker process" (a worker is a deployment
  detail, not an identity).

---

## 5. Construct semantics

| Construct | Rule |
|---|---|
| `Producer` | **NOT a Stage.** Discovers/creates work, then enqueues. Never claims, completes, or writes `runs`. |
| `Queue` | Platform transport (ADR-021). Engine decides dedup *policy*; platform owns the dedup *query*. |
| `Stage` | Transforms **one job type**. Only the **terminal Stage** may declare the `Outcome`. |
| `Review` (Content) | A **Stage** (ADR-026). Transforms one draft; pressure-tests it for safety/factual integrity. Runs in the SAME `engine-content` Runtime Context as Score/Generate — differs only by Stage/Skill/Prompt/Contract. Treats Generate output as **untrusted**. |
| `Selection` (Content) | **NOT a Stage.** The §8 operational mechanism. Deterministic, engine-internal, Policy-driven; owns NO AI reasoning. Reads the Approved Pool and enqueues `publish`. |
| `Pending Review` | A job with `stage="review"` and `status="pending"`. **Not** a new queue status. |
| `Approved Pool` | A **logical view** over `content_engine_runs` (review runs with `approved=true`, not yet published, not expired). No new table. |
| Division of ownership | `Platform` owns execution mechanism; `Engine` owns business behavior; `Runtime` owns reasoning (ADR-022). |

---

## 6. Engine Lifecycle

1. **Design** — declare `Business Capability` + `Outcome`; select primitives; define
   `Policy`.
2. **Implement** — build `Producer` + `Stage`(s) on the frozen platform, reusing
   `eos_queue`. Zero platform changes.
3. **Validate (Implementation Complete)** — contract tests + harness pass; staging runs
   succeed.
4. **Production Evidence** — produce the declared `Outcome` against **REAL systems**,
   including real external-system validation where applicable (§7).
5. **Gate Passed** — continuous delivery of the `Outcome` per the declared
   `Business Capability`.
6. **Operate** — apply `Policy` (cadence, caps, backlog); supervise the runtime.

An engine is "complete" only at step 5 reached **continuously**, not at step 2 or 3.

---

## 7. Completion Gate

An engine is **Gate-Passed** only when **all three** states hold, continuously:

### 7.1 Implementation Complete
All required code exists and executes on the frozen platform (Producer / Queue / Stage /
Runtime / DLQ / runs) with zero platform changes. Contract tests pass: requests and
responses are shaped correctly per the stage's frozen contract.

### 7.2 Production Evidence Complete
The engine has produced its declared `Business Outcome` against **REAL systems**.
- A Stage whose declared Outcome depends on a side-effect in an **external business
  system the engine does not own** (e.g., Buffer, LinkedIn, X, GitHub, GSC, CRM) is
  **NOT Production-Evidence-Complete** until validated against that **REAL external
  system** using **REAL credentials** — not a mock, not a contract-shaped simulation.
  Evidence = real external-system IDs / responses, observed or persisted.
- A Stage with no external-system dependency is Production-Evidence-Complete when it has
  produced its declared Outcome via a **REAL platform run** (real Queue claim, real
  Runtime reasoning, real `runs` row).

### 7.3 Engine Gate Passed
`Implementation Complete` AND `Production Evidence Complete` for **every Stage** in the
engine's chain, demonstrated continuously per the declared `Business Capability`.

### 7.4 INVARIANT — External-System Validation

> A phase may implement **100%** of its code and still **FAIL** its Production Gate,
> because real business evidence has not yet been produced. This holds for **EVERY**
> engine, not only Content. No Stage integrating an external business system is
> Gate-Passed on implementation alone.

This invariant is mandatory for all future Growth OS engines.

---

## 8. Operational layer (engine-internal, no new EOS component)

- The operational layer (discover / cadence / caps / dedup / **Selection** / **Approved
  Pool**) is **engine-internal policy** built on the frozen primitives. **No new EOS
  component** is introduced.
- **Mechanism** (scheduling, retries, DLQ, queue, runs) is owned by the EOS. **Policy**
  (what to schedule, caps, thresholds, sources, TTL, freshness/ranking weights, duplicate
  window, Selection algorithm) is owned by the engine (see `engines/content/config/policy.json`).
- **Selection** is the canonical §8 mechanism: deterministic, engine-internal, Policy-driven,
  owning NO AI reasoning. It reads the Approved Pool and enqueues `publish` for winners. It is
  **NOT** a Stage, Runtime, EOS component, Queue, or Worker (ADR-026 §7).
- **Health metrics** (queue depth, retry rate, DLQ count) are platform-owned.
  **Business metrics** (publications, accepted scores, published count, pool depth) are
  engine-owned.

---

## 9. What is explicitly out of scope for this model

- Any modification to ADR-021/022/023 or the EOS spine.
- New platform-level schedulers, queues, or abstractions.
- Product concepts (users, orgs, billing) — excluded per ADR-023.

---

## References
- `docs/adr/ADR-021-engineering-operating-system.md`
- `docs/adr/ADR-022-ai-runtime-boundary.md`
- `docs/adr/ADR-023-growth-os-open-source-boundary.md`
- `docs/adr/ADR-024-engine-operating-model.md`
- `docs/adr/ADR-026-content-engine-review-selection.md`
- `docs/architecture.md`
- `engines/content/config/policy.json` (engine Policy values)
- `engines/content/lib/selection.py` (§8 Selection algorithm, pure)
- `engines/README.md` (extension points)
