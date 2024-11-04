-- 006_add_failed_tests_column.sql

-- Add failed_tests column to runs table
ALTER TABLE runs
ADD COLUMN failed_tests TEXT[];

-- Create index for faster querying by failed_tests
CREATE INDEX IF NOT EXISTS idx_runs_failed_tests ON runs USING GIN (failed_tests);