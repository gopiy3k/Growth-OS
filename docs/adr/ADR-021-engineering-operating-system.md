# ADR-021: Engineering Operating System (EOS)

**Status:** Accepted — platform architecture decision (frozen)
**Date:** 2026-07-09
**Deciders:** Engineering (architecture review, multiple review rounds, frozen)

---

## Context

Growth OS is intended to become an independent, open-source AI execution platform that any product
can adopt. Across the architecture review it became clear that the engineering knowledge — why
decisions were made, what was accepted/modified/rejected, and how engines should be built — existed
only in conversation history. That is unacceptable for a long-lived platform. This ADR converts that
knowledge into a permanent, repository-resident record and defines the platform architecture:
**an Engineering Operating System (EOS)** provides the intelligence, skills, scheduling, dispatch,
memory, and secrets surface, while **Supabase remains the durable system of record**.

The driver is the native-first principle: use each platform's own built-in capabilities rather than
rebuilding them. EOS provides the agent/worker surface; Supabase provides state. The two are
complementary, not competing.

---

## Decision

### 1. Role split (single source of truth)

| Concern | Owner | Notes |
|---------|-------|-------|
| Durable state: database, storage, queue + DLQ, publishing, audit, analytics | **Supabase** | System of record. Survives worker restarts, crash, and version changes. |
| Intelligence: LLM routing, skills, scheduling, dispatch, memory, browser, image generation, secrets access | **EOS** | Engineering Operating System. Stateless with respect to business data. |
| External AI model calls | **AI Runtime** (see ADR-022) | Replaces any hardcoded provider in engine code. |

**EOS does not rebuild Supabase.** EOS persists results *to* Supabase. Supabase does not run agent logic.

### 2. Canonical engine execution model

```
Scheduler / Webhook ─┐
                     ├─ enqueue job ─▶ Supabase durable queue (jobs + DLQ)
                     │
                     ▼
              Worker  (headless EOS process, bound to an engine Profile + Project)
                     │  runs Skills chain
                     ▼
                Supabase  (persist results)
```

- The **Supabase queue is the source of truth** for work; the Worker is a consumer.
- The Worker is a long-lived headless EOS process scoped to one engine (Profile + Project).
- Skills are plain markdown + scripts, composed at runtime by the worker's prompt — no typed inter-skill call layer.

### 3. Profile strategy

- **One Profile per engine** (e.g., `engine-content`, `engine-geo`, `engine-prospect`), plus **one `governor` Profile** for global shared skills and cross-engine governance.
- Profiles give native isolation of config, sessions, skills, and **memory**.
- Global/shared skills are installed once and inherited; engine-specific skills live under the engine profile.
- This replaces an earlier proposal to use two shared general-purpose profiles. Per-engine profiles scale to many engines without cross-contamination of memory or configuration.

### 4. Skill strategy

- Skills are **file-based** (`SKILL.md` + `references/`, `scripts/`, `templates/`), git-versionable.
- They are **composable via prompt** (the worker reads and follows a skill; it does not call it like a typed function).
- JSON I/O is enforced at the **run level** (`response_format: json_object`), not per skill.
- Logic that needs testing lives in `scripts/` and is exercised in CI.

### 5. Memory strategy

- EOS memory is **native, profile-scoped**, cross-session.
- Cross-engine learning flows through the **`governor`** profile memory and is persisted to Supabase where it must outlive the worker.
- **No external memory backend is used.** Native memory is sufficient.

### 6. Provider / model strategy

- External model calls are delegated to the **AI Runtime** (ADR-022). EOS itself never selects or authenticates a provider/model.

### 7. Documentation / registry strategy

- A minimal **`platform-manifest.yaml`** (implementation phase) carries only non-discoverable facts: CI ownership, provider tiers, engine registry.
- The skill/engine **registry and search index are generated from frontmatter in CI** — no hand-edited master INDEX.

### 8. Governance

- Governance documents state *what*; the worker's operating agreement makes compliance the default behavior.
- Self-improvement is **propose-only**: the platform may recommend changes but **never auto-modifies** the repository. Humans approve.

### 9. Environment boundary

- **Windows is the development host only.** Production workers target **Linux**, not a Windows service.
- A disaster-recovery runbook for the EOS home (profiles, skills, sessions, config) is required.

---

## Constraints / non-goals

- No custom memory backend. No custom skill registry. No reactive file/db watchers.
- EOS is not a reimplementation of Supabase.
- EOS does not dictate product features; it governs how engines are built and operated.

---

## Consequences

**Positive**
- Architecture decisions become permanent, searchable, repository-resident records.
- Native-first: leverages built-ins; minimal custom infrastructure.
- Clear role boundary prevents the "EOS does everything" failure mode.
- Per-engine profiles + native memory scale to many engines without cross-contamination.

**Negative / costs**
- Operational surface to run and back up EOS (profiles, skills, worker process).
- Skill logic needs CI tests (no native skill test harness).

**What becomes a permanent record**
- This ADR (platform architecture) and the supersession notes on legacy docs.

---

## Supersedes / related (design evolution)

This ADR supersedes three earlier proposals that were considered and rejected during review:
1. A **two-shared-profile** model (replaced by profile-per-engine + governor — scales better).
2. Using an **external memory backend** for cross-engine memory (withdrawn — native memory is sufficient).
3. Treating a **proxy** as a core hot-path component (demoted to optional transition adapter).
