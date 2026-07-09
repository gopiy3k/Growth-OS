-- Migration 0001: Engineering OS — job queue spine (M1)
--
-- Platform-owned durable queue. This is the public Growth OS re-numbering of what
-- originated as private migration 044 (provenance: ADR-023 / CHANGELOG). The public
-- Growth OS database is a fresh project; sequence starts at 0001.
--
-- This is NOT a reuse of any product's legacy content_queue / publish_queue. Those
-- encode a domain-coupled, fail-fast, single-tenant design. The EOS requires a
-- generic, engine-agnostic job contract that every future engine (GEO, Prospect,
-- Analytics) inherits.
--
-- Scope of the spine:
--   job source -> Worker -> skill -> structured JSON -> Supabase (queue + DLQ + runs)
-- No publishing, scheduling, images, or extra reasoning stages.

-- ---------------------------------------------------------------------------
-- Job queue: one row per unit of work for any engine stage.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_engine_queue (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  engine       TEXT NOT NULL,                       -- 'content', 'geo', ...
  stage        TEXT NOT NULL,                       -- 'score', 'editorial', ...
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb, -- input the skill consumes
  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'processing', 'done', 'failed')),
  attempts     INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  last_error   TEXT,
  locked_at    TIMESTAMPTZ,                         -- single-Worker claim window
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_queue_claim
  ON content_engine_queue (engine, stage, status, locked_at)
  WHERE status IN ('pending', 'processing');

CREATE INDEX IF NOT EXISTS idx_ce_queue_created
  ON content_engine_queue (created_at DESC);

-- ---------------------------------------------------------------------------
-- Dead-letter queue: jobs that exhausted max_attempts land here, observable
-- and retryable. Failure model is first-class, not silently dropped.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_engine_dlq (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id       UUID NOT NULL REFERENCES content_engine_queue(id) ON DELETE CASCADE,
  engine       TEXT NOT NULL,
  stage        TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_error   TEXT,
  attempts     INTEGER NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_dlq_created
  ON content_engine_dlq (created_at DESC);

-- ---------------------------------------------------------------------------
-- Run records: one row per completed execution. The structured JSON the
-- skill produces is persisted here. This is the observability + output store.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_engine_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id       UUID NOT NULL REFERENCES content_engine_queue(id) ON DELETE CASCADE,
  engine       TEXT NOT NULL,
  stage        TEXT NOT NULL,
  source_url   TEXT,                                -- the external item processed
  result_json  JSONB NOT NULL,                      -- structured skill output
  status       TEXT NOT NULL CHECK (status IN ('success', 'error')),
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ce_runs_job
  ON content_engine_runs (job_id);
CREATE INDEX IF NOT EXISTS idx_ce_runs_created
  ON content_engine_runs (created_at DESC);
