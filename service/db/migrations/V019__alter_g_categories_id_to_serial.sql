BEGIN;

-- Drop the existing primary key constraint if it exists
ALTER TABLE g_categories DROP CONSTRAINT IF EXISTS g_categories_pkey CASCADE;

-- Create a new sequence for the id column
CREATE SEQUENCE IF NOT EXISTS g_categories_id_seq;

-- Alter the id column to use the new sequence and set it as NOT NULL
ALTER TABLE g_categories ALTER COLUMN id SET DEFAULT nextval('g_categories_id_seq');
ALTER TABLE g_categories ALTER COLUMN id SET NOT NULL;

-- Set the sequence to be owned by the id column
ALTER SEQUENCE g_categories_id_seq OWNED BY g_categories.id;

-- Add the primary key constraint back, using the sequence-generated id
ALTER TABLE g_categories ADD PRIMARY KEY (id);

COMMIT;
