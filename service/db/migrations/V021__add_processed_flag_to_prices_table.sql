ALTER TABLE prices ADD COLUMN IF NOT EXISTS processed BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_prices_processed ON prices (processed);
