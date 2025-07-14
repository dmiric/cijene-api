# service/db/field_configs.py

# --- Users ---
# Fields typically needed for the main application UI or full data representation
USER_LOCATION_FULL_FIELDS = [
    "id", "user_id", "address", "city", "state", "zip_code", "country",
    "latitude", "longitude", "location_name", "created_at", "updated_at"
]

# Fields optimized for AI tools (e.g., excluding timestamps if not needed for AI reasoning)
USER_LOCATION_AI_FIELDS = [
    "address", "city", "country", "latitude", "longitude", "location_name"
]

# --- Stores ---
# Fields for AI store information
STORE_AI_FIELDS = [
    "id", "name", "code", "type", "address", "city", "zipcode", "lat", "lon", "chain_code"
]

# --- Products ---
# Full fields for product details in the app
PRODUCT_FULL_FIELDS = [
    "id", "name", "description", "brand", "category", "image_url",
    "regular_price", "special_price", "best_unit_price_per_kg",
    "best_unit_price_per_l", "best_unit_price_per_piece",
    "base_unit_type", "created_at", "updated_at"
]


# Fields for AI product details (excluding embedding)
PRODUCT_AI_DETAILS_FIELDS = [
    "ean", "canonical_name", "brand", "category", "base_unit_type",
    "variants", "text_for_embedding", "keywords", "is_generic_product",
    "seasonal_start_month", "seasonal_end_month"
]

# Fields for AI product prices
PRODUCT_PRICE_AI_FIELDS = [
    "chain_code", "product_id", "store_id", "price_date", "regular_price", 
    "special_price", "unit_price", "best_price_30", "anchor_price"
]

# Fields necessary for database search and ranking operations
PRODUCT_DB_SEARCH_FIELDS = [
    "id", "ean", "canonical_name", "brand", "category",
    "base_unit_type", "variants", "text_for_embedding", "keywords",
    "best_unit_price_per_kg", "best_unit_price_per_l", "best_unit_price_per_piece"
]

# Fields for AI product search results (excluding sensitive/large fields like embedding)
PRODUCT_AI_SEARCH_FIELDS = [
    "id", "ean", "canonical_name", "brand", "category",
    "base_unit_type", "variants", "text_for_embedding", "keywords", "is_generic_product",
    "seasonal_start_month", "seasonal_end_month", 
    "prices_in_stores"
]
