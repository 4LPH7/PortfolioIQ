-- Create app_role if it doesn't exist (run as postgres superuser)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN
        CREATE ROLE app_role;
    END IF;
END
$$;

-- Grant necessary privileges
GRANT USAGE ON SCHEMA public TO app_role;
