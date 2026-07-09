# Growth OS

> An open-source AI execution platform. Engineering OS (EOS) orchestrates work through a durable
> queue, workers, and a runtime-agnostic AI contract. Any product can consume it through public
> APIs and events — without sharing a database, runtime, or internal libraries.

Growth OS is a standalone execution platform. It owns the machinery that turns a unit of work
(a job) into a structured result persisted for observability:

```
job source ──▶ durable queue (jobs + DLQ) ──▶ Worker (claim → reason → persist) ──▶ runs store
```

It is **engine-agnostic**: the first engine is *Content* (editorial scoring of external items),
but the same spine serves any future engine (GEO, Prospect, Analytics, …). It is **runtime-agnostic**:
the reasoning step speaks only an OpenAI-compatible `/v1/chat/completions` contract and never names
a model, provider, or transport.

---

## Why

Building reliable AI workflows usually couples orchestration to a specific model provider or a
specific product database. Growth OS separates three concerns so each can evolve independently:

| Concern | Owner |
|---|---|
| Durable state: queue, DLQ, runs, persistence | **Growth OS** (Supabase) |
| Orchestration: scheduling, claim, retries, observability | **Engineering OS (EOS)** |
| Reasoning: model/provider selection, auth, lifecycle | **AI Runtime** (any implementation) |

A consumer (a product, a script, another service) enqueues a job and later reads the structured
result via the public API or subscribes to events. It never touches Growth OS tables directly.

---

## 30-second quickstart (no paid services, no private infra)

Clone, start the **mock runtime** (so you need neither Hermes, a Supabase project, nor any cloud account), and run a
real job end-to-end against a local/dev Supabase:

```bash
git clone <your-fork-url> growth-os
cd growth-os
cp .env.example .env            # for no-credentials mode you need NOT edit this; the mock runtime is used
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Terminal 1: mock AI runtime on :8643 (implements the contract; no Hermes needed)
python tests/contract/mock_runtime.py --port 8643

# Terminal 2: run the Content engine score driver against the mock
export AI_RUNTIME_BASE_URL=http://127.0.0.1:8643/v1
export AI_RUNTIME_API_KEY=demo
python -m engines.content.lib.run_score_stage
```

See [`docs/getting-started.md`](docs/getting-started.md) for the full walkthrough and a complete
no-credentials test mode.

---

## Repository layout

```
engines/
  _shared/eos_queue.py        # durable queue client (platform contract, engine-agnostic)
  content/                    # reference engine: score stage (demonstrates the pattern)
docs/
  adr/                        # platform architecture decisions (ADR-021/022/023)
  architecture.md
  runtime-contract.md         # the AI Runtime contract
  api/                        # public queue + event API
migrations/0001_eos_queue.sql # platform-owned tables (no product FKs)
runtime/contract/             # capability schemas + compliance spec
tests/contract/               # mock runtime + contract tests (no private infra needed)
examples/minimal-consumer/    # API + event consumption, zero product references
```

---

## Documentation

- [Architecture](docs/architecture.md)
- [AI Runtime contract](docs/runtime-contract.md)
- [Public API](docs/api/queue-api.md)
- [Getting started](docs/getting-started.md)
- [ADRs](docs/adr/) — `ADR-021` (EOS), `ADR-022` (AI Runtime boundary), `ADR-023` (open-source boundary)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)

## License

[Apache License 2.0](LICENSE).
