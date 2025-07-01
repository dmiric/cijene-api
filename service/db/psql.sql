CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS user_personal_data (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    api_key VARCHAR(255) UNIQUE NOT NULL,
    last_login TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    ean VARCHAR(255) UNIQUE NOT NULL,
    brand VARCHAR(255),
    name TEXT,
    quantity DECIMAL(10, 4),
    unit VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS chains (
    id SERIAL PRIMARY KEY,
    code VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    chain_id INTEGER NOT NULL REFERENCES chains(id),
    code VARCHAR(255) NOT NULL,
    type VARCHAR(255),
    address TEXT,
    city VARCHAR(255),
    zipcode VARCHAR(20),
    lat DECIMAL(10, 7),
    lon DECIMAL(10, 7),
    phone VARCHAR(50),
    UNIQUE (chain_id, code)
);

CREATE TABLE IF NOT EXISTS chain_products (
    id SERIAL PRIMARY KEY,
    chain_id INTEGER NOT NULL REFERENCES chains(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    code VARCHAR(255) NOT NULL,
    name TEXT NOT NULL,
    brand VARCHAR(255),
    category VARCHAR(255),
    unit VARCHAR(50),
    quantity VARCHAR(255),
    UNIQUE (chain_id, code)
);

CREATE TABLE IF NOT EXISTS prices (
    chain_product_id INTEGER NOT NULL REFERENCES chain_products(id),
    store_id INTEGER NOT NULL REFERENCES stores(id),
    price_date DATE NOT NULL,
    regular_price DECIMAL(10, 2),
    special_price DECIMAL(10, 2),
    unit_price DECIMAL(10, 4),
    best_price_30 DECIMAL(10, 2),
    anchor_price DECIMAL(10, 2),
    PRIMARY KEY (chain_product_id, store_id, price_date)
);

CREATE TABLE IF NOT EXISTS chain_prices (
    chain_product_id INTEGER NOT NULL REFERENCES chain_products(id),
    price_date DATE NOT NULL,
    min_price DECIMAL(10, 2),
    max_price DECIMAL(10, 2),
    avg_price DECIMAL(10, 2),
    PRIMARY KEY (chain_product_id, price_date)
);

CREATE TABLE IF NOT EXISTS chain_stats (
    chain_id INTEGER NOT NULL REFERENCES chains(id),
    price_date DATE NOT NULL,
    price_count INTEGER NOT NULL,
    store_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chain_id, price_date)
);

ALTER TABLE stores
ADD COLUMN IF NOT EXISTS location GEOMETRY(Point, 4326) GENERATED ALWAYS AS (ST_SetSRID(ST_Point(lon, lat), 4326)) STORED;

CREATE INDEX IF NOT EXISTS idx_stores_location ON stores USING GIST (location);

CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users (deleted_at);

CREATE TABLE IF NOT EXISTS user_locations (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address VARCHAR(255),
    city VARCHAR(100),
    state VARCHAR(100),
    zip_code VARCHAR(20),
    country VARCHAR(100),
    latitude NUMERIC,
    longitude NUMERIC,
    location GEOMETRY(Point, 4326),
    location_name VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_locations_user_id ON user_locations (user_id);
CREATE INDEX IF NOT EXISTS idx_user_locations_location ON user_locations USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_user_locations_deleted_at ON user_locations (deleted_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    sender TEXT NOT NULL,
    message_text TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    tool_calls JSONB NULL,
    tool_outputs JSONB NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id ON chat_messages (user_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages (session_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    preference_key TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (user_id, preference_key)
);
CREATE INDEX IF NOT EXISTS idx_user_preferences_user_id ON user_preferences (user_id);

ALTER TABLE chain_products ADD COLUMN IF NOT EXISTS is_processed BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_chain_products_processed_status ON chain_products (is_processed);

-- Step 1: Create the ENUM type for standardization (run once).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'unit_type_enum') THEN
        CREATE TYPE unit_type_enum AS ENUM ('WEIGHT', 'VOLUME', 'COUNT');
    END IF;
END$$;

-- Phase 1: Database Schema Modifications
CREATE TYPE shopping_list_status_enum AS ENUM ('open', 'closed');
CREATE TYPE shopping_list_item_status_enum AS ENUM ('new', 'bought', 'unavailable', 'deleted');

CREATE TABLE IF NOT EXISTS shopping_lists (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    status shopping_list_status_enum NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_shopping_lists_user_id ON shopping_lists (user_id);
CREATE INDEX IF NOT EXISTS idx_shopping_lists_deleted_at ON shopping_lists (deleted_at);

-- Step 2: Create the new tables with the 'g_' prefix.

-- Table 2.1: g_products (The Canonical Product Record)
-- Stores the single, AI-cleaned source of truth for every product.
CREATE TABLE IF NOT EXISTS g_products (
    id SERIAL PRIMARY KEY,
    ean VARCHAR(255) UNIQUE NOT NULL,
    canonical_name TEXT NOT NULL,
    brand TEXT,
    category TEXT NOT NULL,
    base_unit_type unit_type_enum NOT NULL,
    variants JSONB, -- Stores an array of variant details (e.g., [{"weight_g": 270}, {"weight_g": 300}]).
    text_for_embedding TEXT,
    keywords TEXT[],
    is_generic_product BOOLEAN DEFAULT FALSE,
    seasonal_start_month INTEGER,
    seasonal_end_month INTEGER,
    embedding VECTOR(768),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS shopping_list_items (
    id SERIAL PRIMARY KEY,
    shopping_list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
    g_product_id INTEGER NOT NULL REFERENCES g_products(id) ON DELETE CASCADE,
    quantity DECIMAL(10, 4) NOT NULL,
    base_unit_type unit_type_enum NOT NULL,
    price_at_addition DECIMAL(10, 2),
    store_id_at_addition INTEGER REFERENCES stores(id),
    status shopping_list_item_status_enum NOT NULL DEFAULT 'new',
    notes TEXT,
    added_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    bought_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_shopping_list_items_list_id ON shopping_list_items (shopping_list_id);
CREATE INDEX IF NOT EXISTS idx_shopping_list_items_product_id ON shopping_list_items (g_product_id);
CREATE INDEX IF NOT EXISTS idx_shopping_list_items_deleted_at ON shopping_list_items (deleted_at);

-- Table 2.2: g_prices (Centralized, Time-Series Pricing Data)
-- Tracks the price of a product at a specific store over time.
CREATE TABLE IF NOT EXISTS g_prices (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES g_products(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL, -- This assumes a 'stores' table with integer IDs exists.
    price_date DATE NOT NULL,
    regular_price DECIMAL(10, 2),
    special_price DECIMAL(10, 2),
    price_per_kg DECIMAL(10, 4),
    price_per_l DECIMAL(10, 4),
    price_per_piece DECIMAL(10, 4),
    is_on_special_offer BOOLEAN DEFAULT FALSE,
    UNIQUE(product_id, store_id, price_date)
);

-- Table 2.3: g_product_best_offers (The "Best Value" Lookup Table)
-- Stores the absolute best unit price found anywhere in the system for fast sorting.
CREATE TABLE IF NOT EXISTS g_product_best_offers (
    product_id INTEGER PRIMARY KEY REFERENCES g_products(id) ON DELETE CASCADE,
    best_unit_price_per_kg DECIMAL(10, 4),
    best_unit_price_per_l DECIMAL(10, 4),
    best_unit_price_per_piece DECIMAL(10, 4),
    lowest_price_in_season DECIMAL(10, 4), -- New field for seasonal lowest price
    best_price_store_id INTEGER, -- Links to the store with the best offer.
    best_price_found_at TIMESTAMP WITH TIME ZONE
);

-- Step 3: Create essential indexes for performance.
CREATE INDEX IF NOT EXISTS idx_g_products_brand ON g_products (brand);
CREATE INDEX IF NOT EXISTS idx_g_products_category ON g_products (category);
CREATE INDEX IF NOT EXISTS idx_g_products_keywords ON g_products USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_g_prices_lookup ON g_prices (product_id, store_id, price_date DESC);
CREATE INDEX IF NOT EXISTS idx_g_product_best_offers_kg ON g_product_best_offers (best_unit_price_per_kg ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_g_product_best_offers_l ON g_product_best_offers (best_unit_price_per_l ASC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_g_product_best_offers_piece ON g_product_best_offers (best_unit_price_per_piece ASC NULLS LAST);
