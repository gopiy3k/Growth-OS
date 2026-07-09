# ADR-025: Content Engine Buffer Transport Contract (Developer API)

- Status: Accepted
- Date: 2026-07-09
- Supersedes: legacy REST usage in `engines/content/lib/buffer_client.py` (pre-patch)

## Context

Buffer retired its legacy REST API (`api.bufferapp.com/1/...`). The Content Engine
Publish Stage's transport (`buffer_client.py`) was implemented against that retired API
and could not authenticate with the Buffer **Developer API** key (Bearer token), causing
the Phase 2 Transport Gate to fail with HTTP 401 on the `/info/configuration.json` call.

## Decision

The engine-owned transport is replaced with the supported **Buffer Developer API**
(GraphQL). Scope is strictly limited to `engines/content/lib/buffer_client.py` and its
contract test in `validate_content_publish.py`.

- **Base URL:** `https://api.buffer.com` (single GraphQL endpoint, always POST)
- **Auth:** `Authorization: Bearer <BUFFER_ACCESS_TOKEN>` (the Developer API key)
- **Transport validation:** `query { account { id email organizations { id name } } }`
  (replaces the removed `/info/configuration.json`)
- **Publish:** `mutation createPost(input: { text, channelId, schedulingType: automatic,
  mode: addToQueue }) { ... on PostActionSuccess { post { id } } ... on MutationError { message } }`
- **Publication verification:** best-effort `posts` connection query for the returned id
- **Channels** (legacy called them "profiles"): `query { channels(input: { organizationId }) { id name service } }`
- **Error model:** GraphQL returns HTTP 200 always; errors live in the body
  (`errors[]` with `extensions.code`, and the `MutationError` union on mutations).
  `raise_for_status()` is NOT used; the body is inspected.

**Environment:** the existing `BUFFER_ACCESS_TOKEN` / `BUFFER_LINKEDIN_PROFILE_ID` /
`BUFFER_X_PROFILE_ID` names are **reused**. The legacy "profile" concept is now
"channel"; the configured IDs are channel IDs. The organization ID required by the API
is discovered programmatically via the `account` query (the key is account-based), so
**NO NEW ENVIRONMENT VARIABLE** is introduced.

## Invariants preserved

- **ADR-024 External-System Validation:** Publish completes only after a **REAL** Buffer
  post id is returned; the transport raises (no silent success) on missing token / API
  error / empty post-id list. The `fail() -> retry -> DLQ` path is unchanged.
- **No platform/architecture change:** EOS, Queue, DLQ, Runs, Runtime contract, Producer,
  Stage model, Engine Operating Model, and all ADRs (021/022/023/024) are untouched.
- `run_publish_stage.py` required **no change**: `publish()` still returns
  `{"success", "updates": [{"id", "service"}]}`.

## Consequences

- The Phase 2 Transport Gate can now pass against the real Buffer Developer API.
- Text-only posts are supported (Phase 2 scope). Media/`assets` attachment is deferred
  to a later phase if required.
