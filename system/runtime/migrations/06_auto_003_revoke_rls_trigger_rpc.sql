-- Existing event trigger ensure_rls continues to run as configured.
-- Remove unintended public RPC permissions on SECURITY DEFINER function.
-- Checked function body: only acts on CREATE TABLE in schema public.
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable()
  FROM PUBLIC, anon, authenticated;
