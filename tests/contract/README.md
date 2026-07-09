# tests/contract — AI Runtime contract compliance

This directory lets anyone validate the **AI Runtime contract** (docs/runtime-contract.md)
with **zero dependency on Hermes, the private product, or any paid service**.

## What's here

- `mock_runtime.py` — a stdlib-only mock that implements the OpenAI-compatible
  `POST /v1/chat/completions` contract and returns a valid, deterministic score result.
- `test_runtime_contract.py` — pytest suite that starts the mock and asserts the
  contract shape the EOS driver relies on.

## Run it

```bash
pip install pytest requests
python -m pytest tests/contract -q
```
This starts the mock runtime (launched by file path) on an ephemeral port, so you don't start it manually.
The test spins up `mock_runtime.py` on an ephemeral port, so you don't start it manually.

## Why this matters

The EOS driver (`engines/content/lib/run_score_stage.py`) only knows three
runtime-agnostic facts (`AI_RUNTIME_BASE_URL`, `AI_RUNTIME_API_KEY`, and a
telemetry-only `AI_RUNTIME_MODEL`). It never names a model or provider. Any future
runtime implementation (OpenCode, …) must satisfy the same contract — this suite is the
shared acceptance bar.

To point the real driver at a different runtime (e.g. Hermes API Server), set:

```bash
export AI_RUNTIME_BASE_URL=http://127.0.0.1:8642/v1
export AI_RUNTIME_API_KEY=<runtime token>
python -m engines.content.lib.run_score_stage
```
