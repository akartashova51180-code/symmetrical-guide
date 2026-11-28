-- 06-AUTO-003. Review and apply with a privileged migration role only.
-- Private schema, no anon/authenticated access. Existing business tables are untouched.
BEGIN;
CREATE SCHEMA IF NOT EXISTS brain_ops;
REVOKE ALL ON SCHEMA brain_ops FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS brain_ops.assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id text NOT NULL,
  role_code text NOT NULL,
  instruction text NOT NULL CHECK (length(instruction) BETWEEN 5 AND 16000),
  source_key text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN
    ('queued','running','awaiting_review','completed','rejected','blocked')),
  response_id text,
  result text,
  review text,
  review_response_id text,
  error_code text,
  attempts integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_brain_ops_queue ON brain_ops.assignments(status,created_at);
CREATE TABLE IF NOT EXISTS brain_ops.budget_ledger (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 assignment_id uuid NOT NULL REFERENCES brain_ops.assignments(id),
 purpose text NOT NULL CHECK (purpose IN ('execute','review')),
 day date NOT NULL,
 reserved_usd numeric(12,6) NOT NULL CHECK(reserved_usd >= 0),
 -- Actual usage reported by OpenAI is stored, but pricing is configured externally;
 -- actual_usd is only populated when pricing configuration is provided.
 actual_usd numeric(12,6),
 response_id text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(assignment_id,purpose)
);
CREATE INDEX IF NOT EXISTS idx_brain_ops_budget_day ON brain_ops.budget_ledger(day);
ALTER TABLE brain_ops.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain_ops.budget_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA brain_ops FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA brain_ops FROM PUBLIC, anon, authenticated;
COMMIT;
