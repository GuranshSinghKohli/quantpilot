-- Disposable database for the integration test tier (TEST_DATABASE_URL).
-- The test session drops and recreates its public schema on every run.
CREATE DATABASE quantpilot_test OWNER quantpilot;
