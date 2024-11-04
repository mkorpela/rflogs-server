-- 007_add_timing_stats_tables.sql

-- Create table for storing unique names
CREATE TABLE IF NOT EXISTS execution_elements (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('suite', 'test', 'keyword')),
    UNIQUE (name, type)
);

-- Create index for faster name lookups
CREATE INDEX IF NOT EXISTS idx_execution_elements_name_type ON execution_elements (name, type);

-- Create table for all timing statistics
CREATE TABLE IF NOT EXISTS execution_times (
    run_id TEXT NOT NULL,
    element_id INTEGER NOT NULL,
    total_time FLOAT NOT NULL,
    call_count INTEGER NOT NULL,
    average_time FLOAT NOT NULL,
    median_time FLOAT NOT NULL,
    std_deviation FLOAT NOT NULL,
    PRIMARY KEY (run_id, element_id),
    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE,
    FOREIGN KEY (element_id) REFERENCES execution_elements (id)
);

-- Create indexes for faster querying
CREATE INDEX IF NOT EXISTS idx_execution_times_run_id ON execution_times (run_id);
CREATE INDEX IF NOT EXISTS idx_execution_times_element_id ON execution_times (element_id);