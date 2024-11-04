-- 002_add_run_tags.sql

-- Create run_tags table with constraints
CREATE TABLE IF NOT EXISTS run_tags (
    run_id TEXT NOT NULL,
    key TEXT NOT NULL CHECK (char_length(key) BETWEEN 1 AND 50),
    value TEXT NOT NULL CHECK (char_length(value) BETWEEN 1 AND 100),
    PRIMARY KEY (run_id, key),  -- Enforce case-sensitive uniqueness
    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
);

-- Create unique index to enforce case-insensitive uniqueness on (run_id, lower(key))
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_tags_run_id_lower_key ON run_tags (run_id, lower(key));

-- Create index for faster querying by tag key and value
CREATE INDEX IF NOT EXISTS idx_run_tags_key_value ON run_tags (key, value);
