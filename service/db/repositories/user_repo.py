from contextlib import asynccontextmanager
import asyncpg
from typing import (
    AsyncGenerator,
    AsyncIterator,
    List,
    Any,
    Optional,
)
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
import sys
import json
import pgvector.asyncpg
from service.utils.timing import timing_decorator # Import the decorator

from service.db.base import BaseRepository
from service.db.models import (
    User,
    UserLocation,
    UserPreference,
)
from service.db.field_configs import USER_LOCATION_FULL_FIELDS, USER_LOCATION_AI_FIELDS


class UserRepository(BaseRepository):
    """
    Contains all logic for interacting with user-related tables
    (users, user_locations, user_preferences).
    """

    def __init__(self):
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG user_repo]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

    async def connect(self, pool: asyncpg.Pool) -> None:
        """
        Initializes the repository with an existing connection pool.
        This repository does not create its own pool.
        """
        self.pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized for UserRepository")
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

    
    async def get_user_by_api_key(self, api_key: str) -> User | None:
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, api_key, is_active, created_at
                FROM users
                WHERE
                    api_key = $1 AND
                    is_active = TRUE
                """,
                api_key,
            )

            if row:
                return User(**row)  # type: ignore
            return None

    
    async def get_user_by_id(self, user_id: int) -> User | None:
        """
        Retrieve a user by their ID.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, api_key, is_active, created_at
                FROM users
                WHERE id = $1
                """,
                user_id,
            )
            if row:
                return User(**row)  # type: ignore
            return None

    
    async def add_user(self, name: str) -> User:
        """
        Add a new user with a randomly generated API key.

        Args:
            name: The name of the user.

        Returns:
            The created User object.
        """
        api_key = str(uuid4())
        created_at = datetime.now()
        is_active = True

        async with self._atomic() as conn:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (name, api_key, is_active, created_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                name,
                api_key,
                is_active,
                created_at,
            )
            if user_id is None:
                raise RuntimeError(f"Failed to insert user {name}")

            return User(
                id=user_id,
                name=name,
                api_key=api_key,
                is_active=is_active,
                created_at=created_at,
            )

    
    async def add_user_location(self, user_id: int, location_data: dict) -> UserLocation:
        """
        Add a new location for a user.
        """
        lat = location_data.get("lat")
        lon = location_data.get("lon")
        location_geom = None
        if lat is not None and lon is not None:
            location_geom = f"ST_SetSRID(ST_Point({lon}, {lat}), 4326)::geometry"

        async with self._atomic() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO user_locations (
                    user_id, address, city, state, zip_code, country,
                    lat, lon, location_name, location, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, {location_geom if location_geom else 'NULL'}, NOW(), NOW())
                RETURNING id, user_id, address, city, state, zip_code, country, lat, lon, location_name, created_at, updated_at
                """,
                user_id,
                location_data.get("address"),
                location_data.get("city"),
                location_data.get("state"),
                location_data.get("zip_code"),
                location_data.get("country"),
                lat,
                lon,
                location_data.get("location_name"),
            )
            if row is None:
                raise RuntimeError(f"Failed to insert user location for user {user_id}")

            return UserLocation(**row)

    
    async def get_user_locations_by_user_id(
        self,
        user_id: int,
        fields: Optional[List[str]] = None
    ) -> list[UserLocation]:
        """
        Get all locations for a specific user, with selectable fields.
        """
        if fields is None:
            fields_to_select = USER_LOCATION_FULL_FIELDS
        else:
            fields_to_select = fields

        # Basic validation to prevent SQL injection and ensure fields exist
        valid_fields = set(USER_LOCATION_FULL_FIELDS)
        if not all(f in valid_fields for f in fields_to_select):
            raise ValueError("Invalid field requested for user locations.")

        fields_str = ", ".join(fields_to_select)

        async with self._get_conn() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {fields_str}
                FROM user_locations
                WHERE user_id = $1
                ORDER BY created_at
                """,
                user_id,
            )
            # Convert rows to dictionaries, as UserLocation(**row) might fail with partial data
            # Convert rows to dictionaries, ensuring all values are JSON-serializable.
            # This handles potential complex types returned by asyncpg (e.g., geometry objects).
            converted_rows = []
            for row in rows:
                converted_row = {}
                for key, value in row.items():
                    if isinstance(value, Decimal):
                        converted_row[key] = float(value)
                    elif isinstance(value, (datetime, date)):
                        converted_row[key] = value.isoformat()
                    # Add more type conversions here if other non-serializable types appear
                    else:
                        converted_row[key] = value
                converted_rows.append(converted_row)
            return converted_rows

    
    async def get_user_location_by_id(self, user_id: int, location_id: int) -> UserLocation | None:
        """
        Get a specific user location by its ID and user ID.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id, user_id, address, city, state, zip_code, country,
                    latitude, longitude, location_name, created_at, updated_at
                FROM user_locations
                WHERE id = $1 AND user_id = $2
                """,
                location_id,
                user_id,
            )
            if row:
                return UserLocation(**row) # type: ignore
            return None

    
    async def update_user_location(
        self,
        location_id: int,
        user_id: int,
        address: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        country: Optional[str] = None,
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        location_name: Optional[str] = None,
    ) -> bool:
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                UPDATE user_locations
                SET
                    address = COALESCE($3, address),
                    city = COALESCE($4, city),
                    state = COALESCE($5, state),
                    zip_code = COALESCE($6, zip_code),
                    country = COALESCE($7, country),
                    latitude = COALESCE($8, latitude),
                    longitude = COALESCE($9, longitude),
                    location_name = COALESCE($10, location_name),
                    updated_at = NOW()
                WHERE id = $1 AND user_id = $2
                RETURNING id, user_id, address, city, state, zip_code, country, latitude, longitude, location_name, created_at, updated_at
                """,
                location_id,
                user_id,
                address,
                city,
                state,
                zip_code,
                country,
                latitude,
                longitude,
                location_name,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    
    async def delete_user_location(self, user_id: int, location_id: int) -> bool:
        """
        Delete a user location.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                DELETE FROM user_locations
                WHERE id = $1 AND user_id = $2
                """,
                location_id,
                user_id,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    
    async def add_many_users(self, users: List[User]) -> int:
        """
        Bulk insert users into the database.
        On conflict (id), do nothing.
        """
        self.debug_print(f"add_many_users: Adding {len(users)} users.")
        if not users:
            return 0

        records = [
            (
                u.id,
                u.name,
                u.api_key,
                u.is_active,
                u.created_at,
            )
            for u in users
        ]
        async with self._get_conn() as conn:
            # Use copy_records_to_table for efficient bulk insert
            # Ensure the order of columns matches the order in records
            result = await conn.copy_records_to_table(
                'users',
                records=records,
                columns=[
                    'id', 'name', 'api_key', 'is_active', 'created_at'
                ]
            )
            self.debug_print(f"add_many_users: Inserted {result} rows.")
            return result

    
    async def add_many_user_locations(self, locations: List[UserLocation]) -> int:
        """
        Bulk insert user locations into the database.
        On conflict (id), do nothing.
        """
        async with self._atomic() as conn:
            await conn.execute(
                """
                CREATE TEMP TABLE temp_user_locations (
                    id INTEGER,
                    user_id INTEGER,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    country TEXT,
                    latitude DECIMAL(10, 7),
                    longitude DECIMAL(10, 7),
                    location_name TEXT,
                    created_at TIMESTAMP WITH TIME ZONE,
                    updated_at TIMESTAMP WITH TIME ZONE
                )
                """
            )
            await conn.copy_records_to_table(
                "temp_user_locations",
                records=(
                    (
                        loc.id,
                        loc.user_id,
                        loc.address,
                        loc.city,
                        loc.state,
                        loc.zip_code,
                        loc.country,
                        loc.latitude,
                        loc.longitude,
                        loc.location_name,
                        loc.created_at,
                        loc.updated_at,
                    )
                    for loc in locations
                ),
            )
            result = await conn.execute(
                """
                INSERT INTO user_locations(
                    id, user_id, address, city, state, zip_code, country,
                    latitude, longitude, location_name, created_at, updated_at
                )
                SELECT * from temp_user_locations
                ON CONFLICT (id) DO NOTHING
                """
            )
            await conn.execute("DROP TABLE temp_user_locations")
            _, _, rowcount = result.split(" ")
            rowcount = int(rowcount)
            return rowcount

    
    async def save_user_preference(self, user_id: int, preference_key: str, preference_value: str) -> UserPreference:
        """
        Saves or updates a user's shopping preference.
        """
        async with self._atomic() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_preferences (user_id, preference_key, preference_value, created_at, updated_at)
                VALUES ($1, $2, $3, NOW(), NOW())
                ON CONFLICT (user_id, preference_key) DO UPDATE SET
                    preference_value = EXCLUDED.preference_value,
                    updated_at = NOW()
                RETURNING id, user_id, preference_key, preference_value, created_at, updated_at
                """,
                user_id,
                preference_key,
                preference_value,
            )
            if row is None:
                raise RuntimeError(f"Failed to save user preference for user {user_id}, key {preference_key}")
            return UserPreference(**row)

    
    async def get_user_preference(self, user_id: int, preference_key: str) -> UserPreference | None:
        """
        Retrieves a specific user preference.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, preference_key, preference_value, created_at, updated_at
                FROM user_preferences
                WHERE user_id = $1 AND preference_key = $2
                """,
                user_id,
                preference_key,
            )
            if row:
                return UserPreference(**row)
            return None
