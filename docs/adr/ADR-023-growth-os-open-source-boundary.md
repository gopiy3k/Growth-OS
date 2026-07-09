# ADR-023: Growth OS Open-Source Boundary

**Status:** Accepted — platform architecture decision (frozen)
**Date:** 2026-07-09
**Deciders:** Engineering (Principal Architect review, multiple rounds, frozen)
**Related:**
- `docs/adr/ADR-021-engineering-operating-system.md` — EOS
- `docs/adr/ADR-022-ai-runtime-boundary.md` — AI Runtime boundary
- `docs/architecture.md` — bounded contexts

---

## Context

Growth OS began its life inside a private product repository (where the first engine, *Content*,
was validated). As the platform matured it became clear that Growth OS is **not** a product feature
but an independent execution platform that multiple products should consume identically. This ADR
records the boundary decision for the public open-source repository.

The execution spine (`content_engine_queue` / `_dlq` / `_runs`) originated as migration `044` in the
private product's Supabase project. That provenance is preserved **here**, in metadata, not in the
public migration numbering (see §5).

---

## Decision

### 1. Repository boundary

- Growth OS is published as a **public, standalone repository**.
- The private product repository remains **private** and becomes the **first consumer** of Growth OS.
- The two never share code, a database, a runtime, or internal libraries. The product depends on
  Growth OS only through its **public API and events**.

### 2. What Growth OS owns (platform)

- Engineering OS (EOS)
- Workers, durable queue, dead-letter queue, runs
- Scheduling
- AI Runtime Contract + runtime metadata
- Prompts / skills
- Analytics emissions
- Future engines (Content, GEO, Prospect, GTM, Experiments)

### 3. What Growth OS does NOT own (product)

Users, organizations, billing, brands, reports, and product-specific business logic remain inside
the consuming product. They are never referenced by platform code, migrations, or docs.

### 4. Database ownership

- Growth OS has its **own Supabase project**. Only platform-owned migrations live there.
- Today that is the EOS queue spine (`content_engine_queue` / `_dlq` / `_runs`).
- Legacy product marketing tables are **never migrated**. They remain private until independently
  replaced; then deleted.
- No migration in the public repo references a product table. CI rejects any `references (...)` to a
  non-platform table.

### 5. Migration history

- The public repo **begins a fresh migration sequence** (`0001_eos_queue.sql`). It does **not**
  preserve the private `044` number.
- Provenance is recorded in this ADR + `CHANGELOG.md` ("originated as private migration 044").
- Rationale: the public Growth OS database is a new project starting empty; old numbers imply a false
  coupling to the private project's migration history.

### 6. Identity hygiene

- No product name, brand, customer data, or private infrastructure reference appears in the public
  repo.
- `grep -ri '<product>'` over the repo must return zero hits (except a deliberate provenance note in
  `CHANGELOG.md` / this ADR).
- Engine/skill names (`engine-content`, `score-content-source`) and the term `EOS` are platform
  concepts, not product branding, and are retained.

### 7. Runtime

- EOS orchestrates; the AI Runtime reasons (ADR-022).
- Hermes is **Runtime Implementation v1** — named accurately, but the platform code is runtime-agnostic.
- Future runtimes must be replaceable without changing EOS.

---

## Consequences

- Growth OS stands on its own as a professional open-source project.
- The private product integrates exactly like any future external consumer.
- No architectural change to the validated spine; only packaging, naming, and ownership separation.
- A second consumer (or a company) can adopt Growth OS with zero access to the original product repo.
