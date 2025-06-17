-- Create a spatial index on the location column for performance
CREATE INDEX idx_stores_location ON stores USING GIST (location);

-- Update existing rows to populate the new location column (if lat/lon exist)
-- This assumes you might have lat/lon data in other columns or will import it.
-- If not, this can be skipped or modified.
UPDATE stores
SET location = ST_SetSRID(ST_Point(lon, lat), 4326)::geography
WHERE lat IS NOT NULL AND lon IS NOT NULL;
