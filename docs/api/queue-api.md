# Public Queue API

Growth OS exposes its durable queue through Supabase's PostgREST interface at
`{SUPABASE_URL}/rest/v1`. The EOS internal client (`engines/_shared/eos_queue.py`) is the reference
implementation. External consumers interact with the same tables via their **own role-scoped
credentials (never the platform service-role key)**, or — preferred — through a thin API/event
layer you build on top.

> **Rule:** consumers never share the platform's service-role key, and the platform never exposes
> product tables. A consumer should read results via the `content_engine_runs` it owns (scoped by
> `engine`/`stage`/`source_url`) and enqueue via `content_engine_queue`.

## Tables

### `content_engine_queue`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | primary key |
| `engine` | text | `'content'`, `'geo'`, … |
| `stage` | text | `'score'`, `'editorial'`, … |
| `payload_json` | jsonb | input the skill consumes |
| `status` | text | `pending` \| `processing` \| `done` \| `failed` |
| `attempts` | int | incremented on each claim |
| `max_attempts` | int | default 3 |
| `last_error` | text | |
| `locked_at` | timestamptz | claim window |
| `created_at` / `updated_at` | timestamptz | |

### `content_engine_dlq` — dead-letter store (FK to queue `id`)
### `content_engine_runs` — one row per completed execution (structured `result_json`)

## Operations (reference)

- **Enqueue:** `POST /rest/v1/content_engine_queue` with `Prefer: return=representation`.
- **Claim:** `GET` pending ordered by `created_at asc limit 1`, then `PATCH` to `processing`
  (set `locked_at=now()`, `attempts+1`).
- **Complete:** `POST /rest/v1/content_engine_runs`, then `PATCH` queue row to `done`.
- **Fail:** `PATCH` queue row; if `attempts >= max_attempts`, also `POST` to DLQ + an error run.

See `engines/_shared/eos_queue.py` for the authoritative implementation.

## Versioning

The queue schema and these operations are part of the platform's **MAJOR** contract. Breaking
changes (column removal, status value changes) bump the platform MAJOR version and are announced in
the CHANGELOG with a deprecation window.
