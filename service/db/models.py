from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID # Import UUID type
from enum import Enum # Import Enum

from pydantic import BaseModel, Field # Added for ProductSearchItemV2
from dataclasses import dataclass, fields # Keep dataclass for other models


@dataclass(frozen=True, slots=True, kw_only=True)
class User:
    id: UUID # Changed to UUID
    is_active: bool
    created_at: datetime
    deleted_at: Optional[datetime] = None # Added for soft delete
    hashed_password: str
    is_verified: bool = False
    verification_token: Optional[UUID] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None

class UserRegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class UserLoginRequest(BaseModel):
    email: str
    password: str

class PasswordResetRequest(BaseModel):
    email: str

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPersonalData: # New dataclass for personal data
    user_id: UUID # References User.id
    name: str
    email: str
    api_key: str
    last_login: Optional[datetime] = None
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatMessage:
    id: UUID # Changed to UUID
    user_id: UUID # Changed to UUID
    session_id: UUID # Changed to UUID
    sender: str
    message_text: str
    timestamp: datetime
    tool_calls: Optional[dict] = None
    tool_outputs: Optional[dict] = None
    ai_response: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPreference:
    id: UUID # Changed to UUID
    user_id: UUID # Changed to UUID
    preference_key: str
    preference_value: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class UserLocation:
    id: int
    user_id: UUID # Changed to UUID
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    location_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None # Added for soft delete


@dataclass(frozen=True, slots=True, kw_only=True)
class Chain:
    code: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainWithId(Chain):
    id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainStats:
    chain_code: str
    price_date: date
    price_count: int
    store_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Store:
    chain_id: int
    code: str
    type: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    zipcode: Optional[str] = None
    lat: Optional[Decimal] = None
    lon: Optional[Decimal] = None
    phone: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StoreWithId(Store):
    id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Product:
    ean: str
    brand: Optional[str] = None
    name: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductWithId(Product):
    id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainProduct:
    chain_id: int
    product_id: int
    code: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    quantity: Optional[str] = None

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainProductWithId(ChainProduct):
    id: int


@dataclass(frozen=True, slots=True)
class Price:
    chain_product_id: int
    store_id: int
    price_date: date
    regular_price: Optional[Decimal] = None
    special_price: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    best_price_30: Optional[Decimal] = None
    anchor_price: Optional[Decimal] = None


@dataclass(frozen=True, slots=True)
class StorePrice:
    chain: str
    ean: str
    price_date: date
    regular_price: Optional[Decimal]
    special_price: Optional[Decimal]
    unit_price: Optional[Decimal]
    best_price_30: Optional[Decimal]
    anchor_price: Optional[Decimal]
    store: Store


# New G_ models for v2
@dataclass(frozen=True, slots=True, kw_only=True)
class GProduct:
    ean: str
    canonical_name: str
    brand: Optional[str] = None
    category: str
    base_unit_type: str # This should ideally be an Enum, but using str for simplicity based on SQL schema
    variants: Optional[List[dict]] = None # JSONB type in DB, expecting list of dicts
    text_for_embedding: Optional[str] = None
    keywords: Optional[List[str]] = None # TEXT[] type in DB
    is_generic_product: bool = False # Added for generic product identification
    seasonal_start_month: Optional[int] = None
    seasonal_end_month: Optional[int] = None
    embedding: Optional[List[float]] = None # VECTOR(768) type in DB
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True, slots=True, kw_only=True)
class GProductWithId(GProduct):
    id: int

@dataclass(frozen=True, slots=True, kw_only=True)
class GPrice:
    product_id: int
    store_id: int
    price_date: date
    regular_price: Optional[Decimal] = None
    special_price: Optional[Decimal] = None
    price_per_kg: Optional[Decimal] = None
    price_per_l: Optional[Decimal] = None
    price_per_piece: Optional[Decimal] = None
    is_on_special_offer: bool = False

@dataclass(frozen=True, slots=True, kw_only=True)
class GPriceWithId(GPrice):
    id: int

@dataclass(frozen=True, slots=True, kw_only=True)
class GProductBestOffer:
    product_id: int
    best_unit_price_per_kg: Optional[Decimal] = None
    best_unit_price_per_l: Optional[Decimal] = None
    best_unit_price_per_piece: Optional[Decimal] = None
    lowest_price_in_season: Optional[Decimal] = None # New field for seasonal lowest price
    best_price_store_id: Optional[int] = None
    best_price_found_at: Optional[datetime] = None

@dataclass(frozen=True, slots=True, kw_only=True)
class GProductBestOfferWithId(GProductBestOffer):
    # This model doesn't have an 'id' in the DB, but for consistency with other WithId models
    # and if we ever add a primary key to g_product_best_offers, it's good to have.
    # For now, product_id acts as the primary key.
    pass

@dataclass(frozen=True, slots=True, kw_only=True)
class GStore:
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    zipcode: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    chain_code: Optional[str] = None # Assuming chain_code is directly in g_stores

@dataclass(frozen=True, slots=True, kw_only=True)
class GStoreWithId(GStore):
    id: int

# Define Enums for statuses
class ShoppingListStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"

class ShoppingListItemStatus(str, Enum):
    NEW = "new"
    BOUGHT = "bought"
    UNAVAILABLE = "unavailable"
    DELETED = "deleted"

class ShoppingList(BaseModel):
    id: int
    user_id: UUID
    name: str
    status: ShoppingListStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

class ShoppingListItem(BaseModel):
    id: int
    shopping_list_id: int
    g_product_id: int
    product_name: Optional[str] = None
    ean: Optional[str] = None # From GProduct
    brand: Optional[str] = None # From GProduct
    category: Optional[str] = None # From GProduct
    variants: Optional[List[dict]] = None # From GProduct (JSONB), expecting list of dicts
    is_generic_product: Optional[bool] = None
    seasonal_start_month: Optional[int] = None # From GProduct
    seasonal_end_month: Optional[int] = None # From GProduct
    chain_code: Optional[str] = None
    quantity: Decimal
    base_unit_type: str
    price_at_addition: Optional[Decimal] = None
    store_id_at_addition: Optional[int] = None
    status: ShoppingListItemStatus
    notes: Optional[str] = None
    added_at: datetime
    bought_at: Optional[datetime] = None
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    
    # Current Price Information (from g_prices)
    current_price_date: Optional[date] = None
    current_regular_price: Optional[Decimal] = None
    current_special_price: Optional[Decimal] = None
    current_price_per_kg: Optional[Decimal] = None
    current_price_per_l: Optional[Decimal] = None
    current_price_per_piece: Optional[Decimal] = None
    current_is_on_special_offer: Optional[bool] = None

    # Best Offer Information (from g_product_best_offers)
    best_unit_price_per_kg: Optional[Decimal] = None
    best_unit_price_per_l: Optional[Decimal] = None
    best_unit_price_per_piece: Optional[Decimal] = None
    lowest_price_in_season: Optional[Decimal] = None
    best_price_store_id: Optional[int] = None
    best_price_found_at: Optional[datetime] = None

    # Store Information (from stores and chains, for store_id_at_addition)
    store_address: Optional[str] = None
    store_city: Optional[str] = None
    store_lat: Optional[Decimal] = None
    store_lon: Optional[Decimal] = None
    store_phone: Optional[str] = None
    chain_code: Optional[str] = None

class ProductSearchItemV2(BaseModel):
    id: int
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    unit_of_measure: Optional[str] = None
    quantity_value: Optional[str] = None
    embedding: Optional[List[float]] = None
    keywords: Optional[List[str]] = None
    rank: Optional[float] = None
