# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `v0.1.x` (initial public release line) | ✅ |

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Please report privately using GitHub's **private vulnerability reporting** for this repository
(Settings → Security → Private vulnerability reporting), or email the maintainers at the address
published in the repository's security contact.

We will acknowledge receipt within **3 business days** and aim to provide a remediation plan
within **14 days**, depending on severity.

## Disclosure Policy

- We follow a **90-day coordinated disclosure** timeline from the date a fix is available,
  unless an active exploit in the wild requires earlier action.
- We will credit reporters (with consent) in the release notes.

## Scope notes

Growth OS is an execution platform. Sensitive areas include:
- The queue/DLQ/runs database (Supabase) — access is governed by service-role keys, which must
  **never** appear in the repository.
- The AI Runtime endpoint and key (`AI_RUNTIME_*`) — treat as secrets.
- CI secrets for releases.

## Secret hygiene

- No credentials, tokens, or service-role keys are committed.
- `.env.example` contains placeholders only.
- All commits are scanned by the CI secret scanner; if you suspect a leak, report it immediately
  even if already removed from the working tree (git history retains it).
