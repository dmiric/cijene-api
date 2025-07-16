-- V012__create_crawl_runs_table.sql

-- Create the ENUM type for CrawlStatus
DO $$ BEGIN
    CREATE TYPE crawl_status AS ENUM ('success', 'failed', 'started', 'skipped');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Create the crawl_runs table
CREATE TABLE IF NOT EXISTS crawl_runs (
    id SERIAL PRIMARY KEY,
    chain_name VARCHAR(255) NOT NULL,
    crawl_date DATE NOT NULL,
    status crawl_status DEFAULT 'started' NOT NULL,
    error_message TEXT,
    n_stores INTEGER DEFAULT 0 NOT NULL,
    n_products INTEGER DEFAULT 0 NOT NULL,
    n_prices INTEGER DEFAULT 0 NOT NULL,
    elapsed_time REAL DEFAULT 0.0 NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT _chain_date_uc UNIQUE (chain_name, crawl_date)
);

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_crawl_runs_chain_name ON crawl_runs (chain_name);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_crawl_date ON crawl_runs (crawl_date);
