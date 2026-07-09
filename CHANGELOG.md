# Changelog

All notable changes to Growth OS are documented here. The project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — initial public release (TBD)

### Added
- Engineering OS (EOS) execution spine: durable queue, dead-letter queue, runs store
  (`migrations/0001_eos_queue.sql`).
- Engine-agnostic job-queue client (`engines/_shared/eos_queue.py`).
- Reference engine: Content `score` stage (`engines/content/`) with its Worker driver and
  `score-content-source` skill.
- AI Runtime contract: OpenAI-compatible `/v1/chat/completions`, runtime-agnostic
  (`docs/runtime-contract.md`, `ADR-022`).
- Mock AI Runtime + contract tests so the full suite runs with **no Hermes, no paid service**
  (`tests/contract/`).
- Platform architecture records: `ADR-021` (EOS), `ADR-022` (AI Runtime boundary),
  `ADR-023` (open-source boundary).

### Provenance note
The EOS queue spine originated as migration `044` inside a private product repository. The public
Growth OS database is a **new, independent Supabase project**, so the public migration sequence
starts at `0001` (see `ADR-023` §5). No product code, data, migrations, or branding were carried
over.
