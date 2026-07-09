"""Content Engine - Buffer transport (Publish stage integration).

IMPLEMENTATION NOTE (accepted transport decision, recorded in ADR-025):
Buffer retired the legacy REST API (api.bufferapp.com/1/...). This module now
talks to the SUPPORTED Buffer Developer API (GraphQL, https://api.buffer.com)
using `Authorization: Bearer <BUFFER_ACCESS_TOKEN>` (the Developer API key).

This module is the engine-owned TRANSPORT only. It performs the real HTTP call and
returns the real Buffer publication identifier(s). It does NOT decide WHAT to publish
- that is engine policy (Phase 4).

Environment (engine-owned, NOT platform):
  BUFFER_ACCESS_TOKEN          required - Buffer Developer API key (Bearer token)
  BUFFER_LINKEDIN_PROFILE_ID  optional - LinkedIn *channel* id (legacy called it "profile")
  BUFFER_X_PROFILE_ID         optional - X/Twitter *channel* id

NO NEW ENVIRONMENT VARIABLE is introduced: the organization id required by the
Developer API is discovered programmatically via `query { account { organizations } }`
(the key is account-based). See ADR-025.

No new dependency: uses `requests` (already in the worker runtime).
"""

from __future__ import annotations

import os

import requests

BUFFER_API = "https://api.buffer.com"


def _access_token() -> str:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("BUFFER_ACCESS_TOKEN not set")
    return token


def _targets() -> list[tuple[str, str]]:
    """Return [(channel_id, service), ...] from configured env vars.

    `service` is 'linkedin' or 'twitter', derived from the source env-var name
    (the new API returns a `channelId`, not a service label, on createPost).
    """
    out: list[tuple[str, str]] = []
    li = os.environ.get("BUFFER_LINKEDIN_PROFILE_ID")
    if li:
        out.append((li, "linkedin"))
    x = os.environ.get("BUFFER_X_PROFILE_ID")
    if x:
        out.append((x, "twitter"))
    return out


def _graphql(token: str, query: str) -> dict:
    """POST a GraphQL query/mutation to the Buffer Developer API.

    GraphQL ALWAYS returns HTTP 200; errors live in the body (errors[] and the
    MutationError union). Never use raise_for_status() here.
    """
    resp = requests.post(
        BUFFER_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"query": query},
        timeout=30,
    )
    body = resp.json()
    errors = body.get("errors") or []
    if errors:
        err = errors[0]
        code = (err.get("extensions") or {}).get("code")
        raise RuntimeError(f"Buffer API error ({code}): {err.get('message')}")
    return body


def _gql_str(s: str) -> str:
    """Escape a Python string into a GraphQL string literal safely."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _org_id(token: str) -> str:
    data = _graphql(token, "query { account { organizations { id name } } }")
    orgs = ((data.get("data") or {}).get("account") or {}).get("organizations") or []
    if not orgs:
        raise RuntimeError(f"Buffer account has no organizations: {data!r}")
    return orgs[0]["id"]


def validate_token() -> dict:
    """TRANSPORT VALIDATION: prove the Developer Key is valid against the REAL API.

    Replaces the retired legacy `/info/configuration.json` endpoint.
    Returns the `account` dict (id, email, organizations).
    """
    token = _access_token()
    data = _graphql(token, "query { account { id email organizations { id name } } }")
    acct = (data.get("data") or {}).get("account")
    if not acct:
        raise RuntimeError(f"Buffer token invalid (no account in response): {data!r}")
    return acct


def discover_channels(org_id: str | None = None) -> list[dict]:
    """Return connected channels [{id, name, service}, ...] for the account org.

    Used by the Transport Gate to confirm LinkedIn/X are present and to map IDs.
    """
    token = _access_token()
    oid = org_id or _org_id(token)
    q = f'query {{ channels(input: {{ organizationId: "{oid}" }}) {{ id name service }} }}'
    data = _graphql(token, q)
    return (data.get("data") or {}).get("channels") or []


def verify_publication(post_id: str, org_id: str | None = None) -> bool:
    """PUBLICATION VERIFICATION (soft): confirm a post id exists in the org's posts.

    The PRIMARY ADR-024 evidence is the real `post.id` returned by createPost (the
    Stage only completes when a real id is present). This is a best-effort secondary
    confirmation using the documented `posts` connection.
    """
    token = _access_token()
    oid = org_id or _org_id(token)
    q = (
        f'query {{ posts(first: 50, input: {{ organizationId: "{oid}" }}) {{'
        " edges { node { id status } } } } }"
    )
    data = _graphql(token, q)
    edges = ((data.get("data") or {}).get("posts") or {}).get("edges") or []
    return any((e.get("node") or {}).get("id") == post_id for e in edges)


def publish(post_text: str, channel_ids: list[str] | None = None) -> dict:
    """Publish `post_text` to the given Buffer channels (default: configured targets).

    Returns the real Buffer response:
        {"success": bool, "updates": [{"id", "service"}, ...]}

    Raises on missing token, missing channels, API error, or an empty post-id list -
    so the Publish Stage can NEVER silently report success without a REAL Buffer post
    id (ADR-024 External-System Validation invariant).
    """
    token = _access_token()
    if channel_ids is not None:
        known = {cid: svc for cid, svc in _targets()}
        targets = [(cid, known.get(cid)) for cid in channel_ids]
    else:
        targets = _targets()
    if not targets:
        raise RuntimeError(
            "no Buffer channel ids configured "
            "(set BUFFER_LINKEDIN_PROFILE_ID / BUFFER_X_PROFILE_ID)"
        )
    if not post_text or not post_text.strip():
        raise RuntimeError("empty post text - refusing to publish")

    # Per-channel character limits are a hard external-system constraint (real Buffer/X).
    # Truncate per channel rather than failing the whole publication. Reserve a small margin
    # (ellipsis + defensive headroom for how X counts trailing tokens) so the total NEVER
    # reaches the limit. LinkedIn's limit is large enough that this never triggers for it.
    _LIMITS = {"twitter": 280, "linkedin": 3000}

    created: list[dict] = []
    for ch_id, service in targets:
        limit = _LIMITS.get(service, 3000)
        if len(post_text) <= limit:
            text = post_text
        else:
            # keep total <= limit-1 (strictly under the platform maximum), ellipsis included
            text = post_text[: limit - 2].rstrip() + "…"
        q = (
            "mutation { createPost(input: {"
            f' text: "{_gql_str(text)}",'
            f' channelId: "{ch_id}",'
            " schedulingType: automatic,"
            " mode: addToQueue"
            " }) {"
            "   ... on PostActionSuccess { post { id } }"
            "   ... on MutationError { message }"
            " } }"
        )
        data = _graphql(token, q)
        res = (data.get("data") or {}).get("createPost")
        if res is None:
            raise RuntimeError(f"Buffer createPost returned no data: {data!r}")
        post = res.get("post")
        if post and post.get("id"):
            created.append({"id": post["id"], "service": service})
        else:
            raise RuntimeError(f"Buffer createPost failed: {res.get('message')}")
    if not created:
        raise RuntimeError(f"Buffer returned no post ids: {data!r}")
    return {"success": True, "updates": created}
