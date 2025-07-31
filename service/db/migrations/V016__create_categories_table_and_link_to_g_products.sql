-- Create the categories table
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

-- Add a unique case-insensitive index on the name column
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_name_lower ON categories (LOWER(name));

-- Add a temporary category_id column to g_products
ALTER TABLE g_products ADD COLUMN category_id INTEGER;

-- Populate the categories table with unique existing categories and update g_products
INSERT INTO categories (name)
SELECT DISTINCT LOWER(category) FROM g_products WHERE category IS NOT NULL;

-- Update the g_products table with the new category_id
UPDATE g_products
SET category_id = c.id
FROM categories c
WHERE LOWER(g_products.category) = LOWER(c.name);

-- Set category_id to NOT NULL and add foreign key constraint
ALTER TABLE g_products ALTER COLUMN category_id SET NOT NULL;
ALTER TABLE g_products ADD CONSTRAINT fk_category
FOREIGN KEY (category_id) REFERENCES categories(id);

-- Drop the old category column
ALTER TABLE g_products DROP COLUMN category;

-- Add index to the new category_id column
CREATE INDEX IF NOT EXISTS idx_g_products_category_id ON g_products (category_id);
