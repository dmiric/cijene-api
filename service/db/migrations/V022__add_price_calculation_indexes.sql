CREATE INDEX IF NOT EXISTS idx_chain_products_product_id ON chain_products (product_id);
CREATE INDEX IF NOT EXISTS idx_products_ean ON products (ean);
CREATE INDEX IF NOT EXISTS idx_g_products_ean ON g_products (ean);
CREATE INDEX IF NOT EXISTS idx_prices_processed_chain_product_id ON prices (processed, chain_product_id);
