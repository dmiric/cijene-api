ALTER TABLE g_prices ALTER COLUMN id TYPE BIGINT;
ALTER SEQUENCE g_prices_id_seq OWNED BY g_prices.id;
ALTER SEQUENCE g_prices_id_seq AS BIGINT;
