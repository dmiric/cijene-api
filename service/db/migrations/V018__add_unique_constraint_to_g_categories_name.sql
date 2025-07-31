BEGIN;

ALTER TABLE g_categories
ADD CONSTRAINT g_categories_name_unique UNIQUE (name);

COMMIT;
