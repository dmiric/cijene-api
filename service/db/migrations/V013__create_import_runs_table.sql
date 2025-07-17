-- V013__create_import_runs_table.sql

-- Create the ENUM type for ImportStatus
DO $$ BEGIN
    CREATE TYPE import_status AS ENUM ('success', 'failed', 'started', 'skipped');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Create the import_runs table
CREATE TABLE IF NOT EXISTS import_runs (
    id SERIAL PRIMARY KEY,
    crawl_run_id INTEGER REFERENCES crawl_runs(id),
    chain_name VARCHAR(255) NOT NULL,
    import_date DATE NOT NULL,
    status import_status DEFAULT 'started' NOT NULL,
    error_message TEXT,
    n_stores INTEGER DEFAULT 0 NOT NULL,
    n_products INTEGER DEFAULT 0 NOT NULL,
    n_prices INTEGER DEFAULT 0 NOT NULL,
    elapsed_time REAL DEFAULT 0.0 NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    unzipped_path TEXT,
    CONSTRAINT _import_chain_date_uc UNIQUE (chain_name, import_date)
);

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_import_runs_chain_name ON import_runs (chain_name);
CREATE INDEX IF NOT EXISTS idx_import_runs_import_date ON import_runs (import_date);
CREATE INDEX IF NOT EXISTS idx_import_runs_crawl_run_id ON import_runs (crawl_run_id);
