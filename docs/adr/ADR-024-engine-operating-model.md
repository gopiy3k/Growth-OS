# ADR-024: Engine Operating Model

**Status:** Accepted — engine-layer architecture decision (frozen)
**Date:** 2026-07-09
**Deciders:** Engineering (Lead Implementation Engineer, architecture review + freeze)
**Related:**
- `docs/adr/ADR-021-engineering-operating-system.md` — EOS (platform)
- `docs/adr/ADR-022-ai-runtime-boundary.md` — AI Runtime boundary
- `docs/adr/ADR-023-growth-os-open-source-boundary.md` — OSS boundary
- `docs/engine-operating-model.md` — the living specification this ADR records

---

## Context

The platform architecture (ADR-021/022/023) defines the EOS spine, the AI Runtime
boundary, and the open-source boundary. During the freeze review it became clear that a
**second architectural layer** had been reviewed to acceptance in conversation but never
written to the repository: the **Engine Operating Model** — the rules every engine
(Content, GEO, Prospect, …) must follow to be a complete, gate-passing engine on the
frozen platform.

A concrete gap exposed this. In Content Engine Phase 2 the Producer, Score, Generate,
Publish stages, the Queue, and the Runtime were all implemented and contract-tested, yet
Publish integrates Buffer (LinkedIn, X) and **no `BUFFER_ACCESS_TOKEN` was provisioned**,
so no real publication occurred. "Implementation complete" had been conflated with
"engine gate passed." This ADR records the Engine Operating Model — including the
mandatory three-state Completion Gate with its External-System Validation invariant — as a
permanent, repository-resident record.

---

## Decision

### 1. Two-layer architecture (accepted; now recorded)
- **Layer 1 — Platform Architecture:** ADR-021 (EOS), ADR-022 (AI Runtime boundary),
  ADR-023 (OSS boundary). Frozen. Engines never modify it.
- **Layer 2 — Engine Operating Model:** this ADR + `docs/engine-operating-model.md`.
  Governs how an engine is built and operated on the frozen platform.

### 2. Three categories of engine construct (accepted)
- **Primitives** (platform-owned, reused): `Producer`, `Queue`, `Stage`.
- **Declaration** (engine-owned intent): `Business Capability` + `Outcome`.
- **Policy** (engine-owned configuration): cadence, thresholds, sources, caps, retry,
  backlog policy.

### 3. Engine identity (accepted)
An **engine's identity is its Business Capability**, not its Outcome and not a capability
tuple. The Outcome is the *purpose* the terminal Stage achieves.

### 4. Runtime binding (accepted)
**One engine = exactly one Runtime Context.** Runtime Implementation v1 = one Hermes
Profile (`engine-content`, `engine-geo`, …) publishing the `AI_RUNTIME_*` contract
(ADR-022). "One engine = one worker process" was explicitly rejected in review.

### 5. Construct semantics (accepted)
- `Producer` is **NOT a Stage**: it discovers/creates work and enqueues; it never
  claims, completes, or writes `runs`.
- `Queue` is the platform transport (ADR-021); dedup *logic* is engine-owned, the
  dedup *query* is platform-owned.
- `Stage` transforms **one job type**; only the **terminal Stage** may declare the
  `Outcome`.
- `Platform` owns the execution mechanism; `Engine` owns business behavior; `Runtime`
  owns reasoning (ADR-022).

### 6. Completion Gate — three states (accepted, mandatory)
An engine is **Gate-Passed** only when **all three** hold, continuously:
1. **Implementation Complete** — all code exists and runs on the frozen platform;
   contract tests pass.
2. **Production Evidence Complete** — the declared Business Outcome has been produced
   against **REAL systems** (see §7).
3. **Engine Gate Passed** — (1) and (2) for every Stage in the chain, demonstrated
   continuously per the declared Business Capability.

### 7. INVARIANT — External-System Validation (accepted, this session)
A Stage whose declared Outcome depends on a side-effect in an **external business system
the engine does not own** (e.g., Buffer, LinkedIn, X, GitHub, GSC, CRM) is **NOT
Production-Evidence-Complete** until validated against that **REAL external system** using
**REAL credentials** — not a mock, not a contract-shaped simulation. Evidence = real
external-system IDs / responses, observed or persisted.

> A phase may implement 100% of its code and still **FAIL** its Production Gate,
> because real business evidence has not yet been produced. This holds for **EVERY**
> engine, not only Content. No Stage integrating an external business system is
> Gate-Passed on implementation alone.

### 8. Operational layer (accepted)
- The operational layer (discover/cadence/caps/dedup) is **engine-internal policy**
  built on the frozen primitives. **No new EOS component** is introduced.
- Mechanism (scheduling, retries, DLQ) is owned by the EOS; **policy** (what to
  schedule, caps, thresholds, sources) is owned by the engine.
- **Health metrics** (queue depth, retry rate, DLQ) are platform-owned; **business
  metrics** (publications, accepted scores) are engine-owned.

---

## Consequences
- Every future engine is judged by the same three-state gate; engines with external
  integrations cannot be declared complete until real credentials are provisioned and
  real evidence produced.
- The platform architecture is unchanged; this ADR adds the engine layer only.
- Accepted Engine Operating Model decisions previously residing only in conversation are
  now repository-resident (see `docs/engine-operating-model.md`).

## Supersedes / related
- Replaces the rejected "one engine = one worker process" framing with "one engine =
  exactly one Runtime Context."
- Replaces the rejected "Outcome defines engine identity" framing with "identity =
  Business Capability."
- Does NOT modify ADR-021/022/023; it is a new, additive layer.
