-- First add retention_days column to projects table
ALTER TABLE projects ADD COLUMN retention_days INTEGER;

-- Update existing projects to inherit retention_days from their workspace
UPDATE projects p 
SET retention_days = w.retention_days 
FROM workspaces w 
WHERE p.workspace_id = w.id;

-- Add NOT NULL constraint after setting initial values
ALTER TABLE projects ALTER COLUMN retention_days SET NOT NULL;

-- Add check constraint to ensure retention_days is positive and within workspace plan limit
ALTER TABLE projects ADD CONSTRAINT check_retention_days_positive 
    CHECK (retention_days >= 0);

-- Now that we've migrated the data, we can drop the column from workspaces
ALTER TABLE workspaces DROP COLUMN retention_days;