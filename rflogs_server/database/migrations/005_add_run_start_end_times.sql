-- 005_add_run_start_end_times.sql

-- Add start_time and end_time columns to runs table
ALTER TABLE runs
ADD COLUMN start_time TIMESTAMP,
ADD COLUMN end_time TIMESTAMP;

-- Create index for faster querying by start_time and end_time
CREATE INDEX IF NOT EXISTS idx_runs_start_end_times ON runs (start_time, end_time);