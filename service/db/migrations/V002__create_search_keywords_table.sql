-- Search Keywords table to store EAN and keyword combinations
CREATE TABLE IF NOT EXISTS search_keywords (
    id SERIAL PRIMARY KEY,
    ean VARCHAR(50) NOT NULL REFERENCES products (ean),
    keyword VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ean, keyword)
);

CREATE INDEX IF NOT EXISTS idx_search_keywords_ean ON search_keywords (ean);
CREATE INDEX IF NOT EXISTS trgm_idx_search_keywords_keyword ON search_keywords USING GIN (keyword gin_trgm_ops);
CREATE INDEX IF NOT EXISTS trgm_idx_search_keywords_unaccent_keyword ON search_keywords USING GIN (f_unaccent(keyword) gin_trgm_ops);
