# Governance

Growth OS is maintained by a small team under a lightweight, transparent governance model.

## Roles

| Role | Responsibility |
|---|---|
| **Maintainers (Owners)** | Final say on merges, releases, and architectural direction (ADRs). |
| **Contributors** | Anyone who opens a PR or files an issue. |

## How decisions are made

- **Architecture:** recorded as ADRs under `docs/adr/`. Proposals are discussed in issues/PRs and
  merged by a Maintainer. ADRs are immutable once `Accepted`; supersession is recorded, not edited.
- **Releases:** cut by Maintainers (signed tags, GitHub Releases, auto-CHANGELOG). See `SECURITY.md`.
- **Day-to-day:** PR review by any Maintainer; at least one approving review required on `main`.

## Becoming a Maintainer

Consistent, high-quality contributions (engines, capability contracts, runtime implementations,
docs, reviews) lead to an invitation. There is no formal application — it is earned through work.

## Maintainer list

- See `CODEOWNERS` for the authoritative review group.

## Project status

- **Current:** `v0.1.x` — public launch line. Single primary maintainer; community welcome.
- **Future:** multi-engine platform (GEO, Prospect, Analytics) and additional AI Runtime
  implementations, all under the same contract and governance.
