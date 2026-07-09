# Getting Started

This guide gets you from clone to a fully working job **without any paid service, any private
infrastructure, or the Hermes runtime**. A built-in **mock runtime** satisfies the public AI
Runtime contract so the reference engine runs end-to-end against a local/dev Supabase.

## 1. Install

```bash
git clone <your-fork> growth-os && cd growth-os
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Configure

For the **no-credentials quickstart**, you need no edits — the mock runtime provides the AI side
and the contract test needs no Supabase. To run the engine end-to-end against a real Growth OS
Supabase project, copy the placeholder env and fill in a **Growth OS platform service-role key**
(platform operators only — external contributors should use the mock):

```bash
cp .env.example .env
# edit .env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (operators only)
```

## 3. Apply the schema

Run `migrations/0001_eos_queue.sql` against your Growth OS Supabase project (SQL editor or CLI).
This creates `content_engine_queue`, `content_engine_dlq`, `content_engine_runs`.

## 4. Run the contract suite (no Supabase needed)

```bash
python -m pytest tests/contract -q
```

This starts the mock runtime and asserts the AI Runtime contract shape the EOS driver depends on.

## 5. Run the reference engine end-to-end

Terminal 1 — mock runtime on :8643:

```bash
python tests/contract/mock_runtime.py --port 8643
```

Terminal 2 — enqueue + drive the Content `score` stage:

```bash
export AI_RUNTIME_BASE_URL=http://127.0.0.1:8643/v1
export AI_RUNTIME_API_KEY=demo
python -m engines.content.validate_m1
```

You should see a job enqueued, claimed, reasoned over by the mock, completed, and persisted to
`content_engine_runs` — plus a DLQ path exercised for the failure case.

## 6. Point at a real runtime (optional)

To use a real AI Runtime (e.g. Hermes API Server), set:

```bash
export AI_RUNTIME_BASE_URL=http://127.0.0.1:8642/v1
export AI_RUNTIME_API_KEY=<runtime token>
```

No EOS code changes — the contract is identical.
