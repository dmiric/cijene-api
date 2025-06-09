ALTER TABLE stores
ADD COLUMN latitude DECIMAL(10, 7),
ADD COLUMN longitude DECIMAL(10, 7),
ADD COLUMN location GEOGRAPHY(Point, 4326); -- Directly add as GEOGRAPHY

-- Create a spatial index on the location column for performance
CREATE INDEX idx_stores_location ON stores USING GIST (location);

-- Update existing rows to populate the new location column (if lat/lon exist)
-- This assumes you might have lat/lon data in other columns or will import it.
-- If not, this can be skipped or modified.
UPDATE stores
SET location = ST_SetSRID(ST_Point(longitude, latitude), 4326)::geography
WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
