# Contributing to Growth OS

Thank you for considering a contribution. Growth OS is an open-source AI execution platform; we
welcome issues, capability requests, runtime implementations, and engine contributions.

## Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating you are expected
to uphold it.

## Getting started (no paid services required)

1. Fork and clone.
2. Create a virtualenv and install: `pip install -e ".[dev]"`.
3. Run the contract suite (needs no Supabase, no Hermes):
   ```bash
   python -m pytest tests/contract -q
   ```
4. Start the mock runtime and run the reference driver end-to-end (see `docs/getting-started.md`).

## How to add an engine

1. Copy `engines/content/` as `engines/<your-engine>/`.
2. Add a `lib/run_<stage>_stage.py` driver that uses `engines/_shared/eos_queue.py`.
3. Add a `skills/<name>/SKILL.md` (provider-agnostic; the item arrives as queue payload).
4. Register the engine in `platform-manifest.yaml`.
5. No product code, no direct database access from the engine — go through the queue + AI Runtime.

## How to add a capability (new AI Runtime verb)

1. Define the output schema in `runtime/contract/capabilities.schema.json` and bump the
   `contract` version there.
2. Document it in `docs/runtime-contract.md`.
3. Add a contract test under `tests/contract/`.
4. Update `ADR-022` only if the boundary itself changes.

## Pull requests

- Branch from `main`; keep PRs focused.
- PRs must pass CI (lint + unit + contract + SQL lint + secret scan).
- Use [Conventional Commits](https://www.conventionalcommits.org/).
- If your change is architectural, reference the relevant ADR (or open one under `docs/adr/`).
- Ensure your code contains **no** references to any external private product, brand, or
  infrastructure. The public repo must stand alone.

## License

By contributing, you agree your contributions are licensed under the [Apache License 2.0](LICENSE)
and that you have the right to submit them under those terms.
