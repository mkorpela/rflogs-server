-- Add bucket_name column to workspaces table
ALTER TABLE workspaces
ADD COLUMN bucket_name TEXT;

-- If you want to set NOT NULL constraint and default value, you can do:
-- ALTER TABLE workspaces
-- ADD COLUMN bucket_name TEXT NOT NULL DEFAULT '';

-- Create index on bucket_name if needed
-- CREATE INDEX idx_workspaces_bucket_name ON workspaces (bucket_name);
