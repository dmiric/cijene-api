from contextlib import asynccontextmanager
import asyncpg
from typing import (
    AsyncGenerator,
    AsyncIterator,
    List,
    Any,
    Optional,
)
from datetime import date, datetime
from decimal import Decimal
import sys
import structlog # Import structlog
from service.utils.timing import timing_decorator # Import the decorator

from service.db.base import BaseRepository
from service.db.models import (
    Store,
    StoreWithId,
)
from service.db.field_configs import STORE_AI_FIELDS # Import AI fields for stores


class StoreRepository(BaseRepository):
    """
    Contains all logic for interacting with the 'legacy' store-related tables
    (stores, chains).
    """

    def __init__(self):
        self.pool = None
        self.log = structlog.get_logger(self.__class__.__name__)

    async def connect(self, pool: asyncpg.Pool) -> None:
        """
        Initializes the repository with an existing connection pool.
        This repository does not create its own pool.
        """
        self.pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized for StoreRepository")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._get_conn() as conn:
            async with conn.transaction():
                yield conn

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)

    async def add_store(self, store: Store) -> int:
        return await self._fetchval(
            f"""
            INSERT INTO stores (chain_id, code, type, address, city, zipcode, lat, lon, phone)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (chain_id, code) DO UPDATE SET
                type = COALESCE($3, stores.type),
                address = COALESCE($4, stores.address),
                city = COALESCE($5, stores.city),
                zipcode = COALESCE($6, stores.zipcode),
                lat = COALESCE($7, stores.lat),
                lon = COALESCE($8, stores.lon),
                phone = COALESCE($9, stores.phone)
            RETURNING id
            """,
            store.chain_id,
            store.code,
            store.type,
            store.address or None,
            store.city or None,
            store.zipcode or None,
            store.lat,
            store.lon,
            store.phone or None,
        )

    async def update_store(
        self,
        chain_id: int,
        store_code: str,
        *,
        address: str | None = None,
        city: str | None = None,
        zipcode: str | None = None,
        lat: Decimal | None = None,
        lon: Decimal | None = None,
        phone: str | None = None,
    ) -> bool:
        """
        Update store information by chain_id and store code.
        Returns True if the store was updated, False if not found.
        """
        async with self._get_conn() as conn:
            result = await conn.execute(
                """
                UPDATE stores
                SET
                    address = COALESCE($3, stores.address),
                    city = COALESCE($4, stores.city),
                    zipcode = COALESCE($5, stores.zipcode),
                    lat = COALESCE($6, stores.lat),
                    lon = COALESCE($7, stores.lon),
                    phone = COALESCE($8, stores.phone)
                WHERE chain_id = $1 AND code = $2
                """,
                chain_id,
                store_code,
                address or None,
                city or None,
                zipcode or None,
                lat or None,
                lon or None,
                phone or None,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    
    async def list_stores(self, chain_code: str) -> list[StoreWithId]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    s.id, s.chain_id, s.code, s.type, s.address, s.city, s.zipcode,
                    s.lat, s.lon, s.phone
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE c.code = $1
                """,
                chain_code,
            )

            return [StoreWithId(**row) for row in rows]  # type: ignore

    
    async def filter_stores(
        self,
        chain_codes: list[str] | None = None,
        city: str | None = None,
        address: str | None = None,
        lat: Decimal | None = None,
        lon: Decimal | None = None,
        d: float = 10.0,
    ) -> list[StoreWithId]:
        # Validate lat/lon parameters
        if (lat is None) != (lon is None):
            raise ValueError(
                "Both lat and lon must be provided together, or both must be None"
            )

        async with self._get_conn() as conn:
            # Build the query dynamically based on provided filters
            where_conditions = []
            params = []
            param_counter = 1

            # Chain codes filter
            if chain_codes:
                where_conditions.append(f"c.code = ANY(${param_counter})")
                params.append(chain_codes)
                param_counter += 1

            # City filter (case-insensitive substring match)
            if city:
                where_conditions.append(f"s.city ILIKE ${param_counter}")
                params.append(f"%{city}%")
                param_counter += 1

            # Address filter (case-insensitive substring match)
            if address:
                where_conditions.append(f"s.address ILIKE ${param_counter}")
                params.append(f"%{address}%")
                param_counter += 1

            # Geolocation filter using computed earth_point column
            if lat is not None and lon is not None:
                where_conditions.append(
                    f"s.earth_point IS NOT NULL AND "
                    f"earth_distance(s.earth_point, ll_to_earth(${param_counter}, ${param_counter + 1})) <= ${param_counter + 2}"
                )
                params.extend([lat, lon, d * 1000])  # Convert km to meters
                param_counter += 3

            # Build the complete query
            base_query = """
                SELECT
                    s.id, s.chain_id, s.code, s.type, s.address, s.city, s.zipcode,
                    s.lat, s.lon, s.phone
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
            """

            if where_conditions:
                query = base_query + " WHERE " + " AND ".join(where_conditions)
            else:
                query = base_query

            query += " ORDER BY c.code, s.code"
            rows = await conn.fetch(query, *params)
            return [StoreWithId(**row) for row in rows]  # type: ignore

    
    async def get_ungeocoded_stores(self) -> list[StoreWithId]:
        """
        Fetches stores that have address information but are missing
        lat or lon.
        """
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id, chain_id, code, type, address, city, zipcode, lat, lon
                FROM stores
                WHERE
                    (lat IS NULL OR lon IS NULL) AND
                    (address IS NOT NULL OR city IS NOT NULL OR zipcode IS NOT NULL)
                """
            )
            return [StoreWithId(**row) for row in rows] # type: ignore

    
    async def get_stores_within_radius(
        self,
        lat: Decimal,
        lon: Decimal,
        radius_meters: int,
        chain_code: Optional[str] = None,
        fields: Optional[List[str]] = None # New parameter for selectable fields
    ) -> list[dict[str, Any]]:
        """
        Finds and lists stores within a specified radius of a given lat/lon, with selectable fields.
        Results include chain code and distance from the center point, ordered by distance.
        """
        self.log.debug("get_stores_within_radius received", lat=lat, lon=lon, radius_meters=radius_meters, chain_code=chain_code, fields=fields)

        if fields is None:
            fields_to_select = STORE_AI_FIELDS # Default to AI fields for this common AI tool
        else:
            fields_to_select = fields
        self.log.debug("Fields to select in get_stores_within_radius", fields_to_select=fields_to_select)

        # Basic validation for fields
        valid_fields = set(STORE_AI_FIELDS + ["distance_meters"]) # Include distance for sorting
        if not all(f in valid_fields for f in fields_to_select):
            raise ValueError("Invalid field requested for stores within radius.")

        # Construct SELECT clause dynamically
        select_parts = []
        for field in fields_to_select:
            if field == "chain_code":
                select_parts.append("c.code AS chain_code")
            elif field == "name": # Alias s.code as name
                select_parts.append("s.code AS name")
            else:
                select_parts.append(f"s.{field}") # Direct mapping for other fields
        
        # Always include distance_meters for nearby queries
        select_parts.append(f"ST_Distance(s.location::geography, ST_SetSRID(ST_Point({lon}, {lat}), 4326)::geography) AS distance_meters")

        select_clause = ", ".join(select_parts)

        async with self._get_conn() as conn:
            # Create a geometry point for the center of the search (matching DB column type)
            center_point = f"ST_SetSRID(ST_Point({lon}, {lat}), 4326)::geometry"
            self.log.debug("Generated center_point", center_point=center_point)

            query = f"""
                SELECT {select_clause}
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE ST_DWithin(s.location::geography, {center_point}::geography, $1)
            """
            params = [radius_meters]

            if chain_code:
                query += " AND c.code = $2"
                params.append(chain_code)

            query += f" ORDER BY ST_Distance(s.location, {center_point})"

            self.log.debug("get_stores_within_radius: Final Query", query=query)
            self.log.debug("get_stores_within_radius: Params", params=params)
            rows = await conn.fetch(query, *params)
            
            # Convert rows to dictionaries, ensuring all values are JSON-serializable.
            converted_rows = []
            for row in rows:
                converted_row = {}
                for key, value in row.items():
                    if isinstance(value, Decimal):
                        converted_row[key] = float(value)
                    elif isinstance(value, (datetime, date)):
                        converted_row[key] = value.isoformat()
                    else:
                        converted_row[key] = value
                converted_rows.append(converted_row)
            self.log.debug("get_stores_within_radius results", results=converted_rows) # Add logging
            return converted_rows
