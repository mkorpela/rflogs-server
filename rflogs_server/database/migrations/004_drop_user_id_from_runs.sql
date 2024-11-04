-- 004_drop_user_id_from_runs.sql

-- Drop the foreign key constraint
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_user_id_fkey;

-- Drop the user_id column
ALTER TABLE runs DROP COLUMN IF EXISTS user_id;