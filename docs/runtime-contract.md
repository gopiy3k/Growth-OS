# AI Runtime Contract

**Status:** frozen contract (ADR-022). **Implementation note:** the wire shape below matches the
shipped `engines/content/lib/run_score_stage.py` driver; the *capability-verb* model is the
documented evolution (see §4).

---

## 1. The boundary

```
Engineering OS (EOS)                │   AI Runtime (implementation)
------------------------------------│------------------------------------------
queue orchestration                 │   model execution
retries / backoff                   │   model + provider ROUTING
dead-letter queue                   │   provider AUTHENTICATION
persistence (Supabase)              │   RUNTIME LIFECYCLE / bootstrap
observability                       │   model SELECTION
job contract (payload → JSON)       │   transport (API Server, …)
```

The EOS consumes an **OpenAI-compatible `/v1/chat/completions`** contract. It knows only three
runtime-agnostic facts, published by the runtime:

| Fact | Example | Purpose |
|---|---|---|
| `AI_RUNTIME_BASE_URL` | `http://127.0.0.1:8642/v1` | endpoint root |
| `AI_RUNTIME_API_KEY` | bearer token | auth to the runtime's API server |
| `AI_RUNTIME_MODEL` | (runtime-published) | **telemetry only** — EOS never sets it; runtime publishes its model for observability |

The EOS **never** names, selects, authenticates, or bootstraps a model/provider/transport.

---

## 2. Wire contract (current, as shipped)

Request (POST `{AI_RUNTIME_BASE_URL}/chat/completions`):

```json
{
  "model": "",                       // empty in M2; evolution → capability verb (§4)
  "messages": [
    {"role": "system", "content": "<skill instruction enforcing the output schema>"},
    {"role": "user",   "content": "Title: …\nContent: …"}
  ],
  "response_format": {"type": "json_object"},
  "temperature": 0.2
}
```

Response (OpenAI-compatible):

```json
{
  "model": "runtime-published-model-id",   // telemetry; echoed into the run record
  "choices": [
    {"message": {"role": "assistant", "content": "{\"score\": 7, \"category\": \"industry\", \"decision\": \"approve\", \"rationale\": \"…\"}"}}
  ]
}
```

The EOS parses `choices[0].message.content` as JSON and normalizes it to the frozen engine contract.

---

## 3. Guarantees the EOS relies on

- The runtime advertises `structured_output: json_schema` (or `json_object`) capability.
- The runtime returns well-formed JSON in `choices[0].message.content`.
- The runtime publishes its model id in the `model` field (for observability, never for selection).

If a runtime cannot meet these, it is **not** contract-compliant and must not be wired to EOS.

---

## 4. Evolution: business-capability model

The intended contract evolution (not yet in shipped code) replaces the empty `model` field with a
**business capability verb**:

```json
{ "model": "score_content", "messages": [ … ], "response_format": {"type": "json_schema", "schema": <capability schema>} }
```

EOS requests a *capability* (`score_content`, `generate_article`, `analyze_visibility`,
`classify_prospect`); the runtime internally decides model/provider/tools/routing. The
`runtime/contract/capabilities.schema.json` file is the source of truth for each capability's
output contract. A capability schema change bumps the **contract version**, not necessarily the
platform MAJOR version.

**Compatibility rule:** EOS pins the contract version it requires; a runtime must satisfy it. We
never remove a capability without a one-minor-version deprecation window.

---

## 5. Compliance test

`tests/contract/` ships a **mock runtime** implementing this contract so the full EOS suite runs
with **zero dependency on Hermes, a real Supabase project, or any paid service**. Every runtime implementation
(future OpenCode, etc.) must pass the same contract test against its own endpoint.

See [`tests/contract/README`](../../tests/contract) for how to point the driver at your runtime.
