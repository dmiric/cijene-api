ALTER TABLE stores
ADD COLUMN IF NOT EXISTS earth_point earth GENERATED ALWAYS AS (ll_to_earth (lat, lon)) STORED;

CREATE INDEX IF NOT EXISTS idx_stores_earth_point ON stores USING GIST (earth_point);
