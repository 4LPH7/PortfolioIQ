-- ============================================================
-- Migration 009: Audit Immutability Enforcement
-- Triggers that prevent UPDATE or DELETE on audit tables.
-- Financial audit logs must be append-only and tamper-proof.
-- ============================================================

-- Shared function: raises an exception on any mutation attempt
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'SECURITY VIOLATION: Table "%" is append-only. '
        'UPDATE and DELETE operations are permanently prohibited. '
        'Attempted operation: % by role: %',
        TG_TABLE_NAME,
        TG_OP,
        current_user;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
   SECURITY DEFINER;  -- Runs with definer's rights, not caller's rights

-- Lock down order_audit_trail
DROP TRIGGER IF EXISTS enforce_audit_trail_immutability ON order_audit_trail;
CREATE TRIGGER enforce_audit_trail_immutability
    BEFORE UPDATE OR DELETE ON order_audit_trail
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- Lock down order_validation_log
DROP TRIGGER IF EXISTS enforce_validation_log_immutability ON order_validation_log;
CREATE TRIGGER enforce_validation_log_immutability
    BEFORE UPDATE OR DELETE ON order_validation_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- ============================================================
-- Revoke direct table permissions
-- Application uses a service role that only has INSERT + SELECT.
-- This ensures even app bugs can't corrupt the audit trail.
-- NOTE: app_role must be created by a superuser BEFORE running this.
--       See db/temp_create_role.sql
-- ============================================================

-- Role creation is handled separately (requires superuser).
-- If app_role doesn't exist, the GRANTs below will fail gracefully.

DO $$
BEGIN
    -- Revoke dangerous permissions from PUBLIC
    REVOKE UPDATE, DELETE, TRUNCATE ON order_audit_trail FROM PUBLIC;
    REVOKE UPDATE, DELETE, TRUNCATE ON order_validation_log FROM PUBLIC;

    -- Grant minimal permissions to app_role
    GRANT INSERT, SELECT ON order_audit_trail TO app_role;
    GRANT INSERT, SELECT ON order_validation_log TO app_role;
    GRANT USAGE, SELECT ON SEQUENCE order_audit_trail_id_seq TO app_role;
    GRANT USAGE, SELECT ON SEQUENCE order_validation_log_id_seq TO app_role;
EXCEPTION WHEN undefined_object THEN
    RAISE WARNING 'app_role does not exist. Skipping GRANT statements. '
                  'Run db/temp_create_role.sql as postgres superuser first.';
END
$$;
