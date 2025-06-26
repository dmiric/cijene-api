from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
import uuid

from dataclasses import dataclass, fields
from pydantic import BaseModel, Field # Added for ProductSearchItemV2


@dataclass(frozen=True, slots=True, kw_only=True)
class User:
    id: int
    name: str
    api_key: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatMessage:
    id: str # UUID
    user_id: int
    session_id: str # UUID
    sender: str
    message_text: str
    timestamp: datetime
    tool_calls: Optional[dict] = None
    tool_outputs: Optional[dict] = None
    ai_response: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPreference:
    id: str # UUID
    user_id: int
    preference_key: str
    preference_value: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class UserLocation:
    id: int
    user_id: int
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


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchKeyword:
    id: int
    ean: str
    keyword: str
    created_at: datetime

# New G_ models for v2
@dataclass(frozen=True, slots=True, kw_only=True)
class GProduct:
    ean: str
    canonical_name: str
    brand: Optional[str] = None
    category: str
    base_unit_type: str # This should ideally be an Enum, but using str for simplicity based on SQL schema
    variants: Optional[dict] = None # JSONB type in DB
    text_for_embedding: Optional[str] = None
    keywords: Optional[List[str]] = None # TEXT[] type in DB
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
