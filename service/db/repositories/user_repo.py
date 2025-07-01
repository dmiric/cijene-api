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
    UserPersonalData, # New import
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

    
    async def get_user_by_api_key(self, api_key: str) -> UserPersonalData | None: # Return UserPersonalData
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    upd.user_id, upd.name, upd.email, upd.api_key, upd.last_login, upd.updated_at
                FROM user_personal_data upd
                JOIN users u ON upd.user_id = u.id
                WHERE
                    upd.api_key = $1 AND
                    u.is_active = TRUE AND
                    u.deleted_at IS NULL
                """,
                api_key,
            )

            if row:
                return UserPersonalData(**row) # type: ignore
            return None

    
    async def get_user_by_id(self, user_id: UUID) -> tuple[User | None, UserPersonalData | None]: # Return both User and UserPersonalData
        """
        Retrieve a user by their ID, including personal data.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    u.id, u.is_active, u.created_at, u.deleted_at, u.hashed_password, u.is_verified, u.verification_token,
                    upd.name, upd.email, upd.api_key, upd.last_login, upd.updated_at AS personal_updated_at
                FROM users u
                LEFT JOIN user_personal_data upd ON u.id = upd.user_id
                WHERE u.id = $1 AND u.deleted_at IS NULL
                """,
                user_id,
            )
            if row:
                user_data = {
                    "id": row["id"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                    "deleted_at": row["deleted_at"],
                    "hashed_password": row["hashed_password"],
                    "is_verified": row["is_verified"],
                    "verification_token": row["verification_token"],
                }
                personal_data = {
                    "user_id": row["id"],
                    "name": row["name"],
                    "email": row["email"],
                    "api_key": row["api_key"],
                    "last_login": row["last_login"],
                    "updated_at": row["personal_updated_at"]
                }
                return User(**user_data), UserPersonalData(**personal_data) # type: ignore
            return None, None

    
    async def add_user(self, name: str, email: str) -> tuple[User, UserPersonalData]: # Return both User and UserPersonalData
        """
        Add a new user with a randomly generated API key and personal data.

        Args:
            name: The name of the user.
            email: The email of the user.

        Returns:
            A tuple containing the created User and UserPersonalData objects.
        """
        new_user_uuid = uuid4()
        api_key = str(uuid4())
        created_at = datetime.now()
        is_active = True

        async with self._atomic() as conn:
            # Insert into users table
            user_record = await conn.fetchrow(
                """
                INSERT INTO users (id, is_active, created_at)
                VALUES ($1, $2, $3)
                RETURNING id, is_active, created_at, deleted_at
                """,
                new_user_uuid,
                is_active,
                created_at,
            )
            if user_record is None:
                raise RuntimeError(f"Failed to insert user {name}")

            # Insert into user_personal_data table
            personal_data_record = await conn.fetchrow(
                """
                INSERT INTO user_personal_data (user_id, name, email, api_key, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING user_id, name, email, api_key, last_login, updated_at
                """,
                new_user_uuid,
                name,
                email,
                api_key,
                datetime.now(),
            )
            if personal_data_record is None:
                raise RuntimeError(f"Failed to insert personal data for user {name}")

            return User(**user_record), UserPersonalData(**personal_data_record) # type: ignore

    async def add_user_with_password(self, name: str, email: str, hashed_password: str, verification_token: UUID) -> tuple[User, UserPersonalData]:
        """
        Add a new user with a hashed password, verification token, and personal data.
        """
        new_user_uuid = uuid4()
        api_key = str(uuid4()) # Still generate an API key for existing API key auth
        created_at = datetime.now()
        is_active = True # User is active but not yet email verified

        async with self._atomic() as conn:
            user_record = await conn.fetchrow(
                """
                INSERT INTO users (id, is_active, created_at, hashed_password, is_verified, verification_token)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, is_active, created_at, deleted_at, hashed_password, is_verified, verification_token
                """,
                new_user_uuid,
                is_active,
                created_at,
                hashed_password,
                False, # Not verified initially
                verification_token,
            )
            if user_record is None:
                raise RuntimeError(f"Failed to insert user {name}")

            personal_data_record = await conn.fetchrow(
                """
                INSERT INTO user_personal_data (user_id, name, email, api_key, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING user_id, name, email, api_key, last_login, updated_at
                """,
                new_user_uuid,
                name,
                email,
                api_key,
                datetime.now(),
            )
            if personal_data_record is None:
                raise RuntimeError(f"Failed to insert personal data for user {name}")

            return User(**user_record), UserPersonalData(**personal_data_record) # type: ignore

    async def get_user_by_email(self, email: str) -> tuple[User | None, UserPersonalData | None]:
        """
        Retrieve a user by their email, including personal data.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    u.id, u.is_active, u.created_at, u.deleted_at, u.hashed_password, u.is_verified, u.verification_token,
                    upd.name, upd.email, upd.api_key, upd.last_login, upd.updated_at AS personal_updated_at
                FROM users u
                LEFT JOIN user_personal_data upd ON u.id = upd.user_id
                WHERE upd.email = $1 AND u.deleted_at IS NULL
                """,
                email,
            )
            if row:
                user_data = {k: v for k, v in row.items() if k in User.__annotations__}
                personal_data = {
                    "user_id": row["id"],
                    "name": row["name"],
                    "email": row["email"],
                    "api_key": row["api_key"],
                    "last_login": row["last_login"],
                    "updated_at": row["personal_updated_at"]
                }
                return User(**user_data), UserPersonalData(**personal_data) # type: ignore
            return None, None

    async def verify_user_email(self, verification_token: UUID) -> bool:
        """
        Marks a user's email as verified.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                UPDATE users
                SET is_verified = TRUE, verification_token = NULL
                WHERE verification_token = $1 AND is_verified = FALSE
                """,
                verification_token,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    async def get_user_by_verification_token(self, verification_token: UUID) -> User | None:
        """
        Retrieve a user by their verification token.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, is_active, created_at, deleted_at, hashed_password, is_verified, verification_token
                FROM users
                WHERE verification_token = $1 AND deleted_at IS NULL
                """,
                verification_token,
            )
            return User(**row) if row else None

    async def add_refresh_token(self, user_id: UUID, token: str, expires_at: datetime) -> None:
        """
        Adds a new refresh token for a user.
        """
        async with self._atomic() as conn:
            await conn.execute(
                """
                INSERT INTO refresh_tokens (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                user_id,
                token,
                expires_at,
            )

    async def get_refresh_token(self, token: str) -> dict | None:
        """
        Retrieves a refresh token and its associated user_id.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, token, expires_at
                FROM refresh_tokens
                WHERE token = $1 AND expires_at > NOW()
                """,
                token,
            )
            return dict(row) if row else None

    async def delete_refresh_token(self, token: str) -> bool:
        """
        Deletes a refresh token.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                DELETE FROM refresh_tokens
                WHERE token = $1
                """,
                token,
            )
            return result == "DELETE 1"

    async def add_password_reset_token(self, user_id: UUID, token: str, expires_at: datetime) -> None:
        """
        Adds a new password reset token for a user.
        """
        async with self._atomic() as conn:
            await conn.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                user_id,
                token,
                expires_at,
            )

    async def get_password_reset_token(self, token: str) -> dict | None:
        """
        Retrieves a password reset token.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, token, expires_at, used
                FROM password_reset_tokens
                WHERE token = $1 AND expires_at > NOW() AND used = FALSE
                """,
                token,
            )
            return dict(row) if row else None

    async def mark_password_reset_token_used(self, token_id: int) -> bool:
        """
        Marks a password reset token as used.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                UPDATE password_reset_tokens
                SET used = TRUE, expires_at = NOW() -- Expire immediately after use
                WHERE id = $1 AND used = FALSE
                """,
                token_id,
            )
            return result == "UPDATE 1"

    async def update_user_password(self, user_id: UUID, hashed_password: str) -> bool:
        """
        Updates a user's hashed password.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                UPDATE users
                SET hashed_password = $1
                WHERE id = $2
                """,
                hashed_password,
                user_id,
            )
            return result == "UPDATE 1"
    
    async def add_user_location(self, user_id: UUID, location_data: dict) -> UserLocation:
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
                    latitude, longitude, location_name, location, created_at, updated_at, deleted_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, {location_geom if location_geom else 'NULL'}, NOW(), NOW(), NULL)
                RETURNING id, user_id, address, city, state, zip_code, country, latitude, longitude, location_name, created_at, updated_at, deleted_at
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
        user_id: UUID,
        fields: Optional[List[str]] = None
    ) -> list[UserLocation]:
        """
        Get all active locations for a specific user, with selectable fields.
        """
        if fields is None:
            fields_to_select = USER_LOCATION_FULL_FIELDS
        else:
            fields_to_select = fields

        # Basic validation to prevent SQL injection and ensure fields exist
        valid_fields = set(USER_LOCATION_FULL_FIELDS + ['deleted_at']) # Include deleted_at for validation
        if not all(f in valid_fields for f in fields_to_select):
            raise ValueError("Invalid field requested for user locations.")

        fields_str = ", ".join(fields_to_select)

        async with self._get_conn() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {fields_str}
                FROM user_locations
                WHERE user_id = $1 AND deleted_at IS NULL
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

    
    async def get_user_location_by_id(self, user_id: UUID, location_id: int) -> UserLocation | None:
        """
        Get a specific active user location by its ID and user ID.
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id, user_id, address, city, state, zip_code, country,
                    latitude, longitude, location_name, created_at, updated_at, deleted_at
                FROM user_locations
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
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
        user_id: UUID,
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
            # Update location geometry if lat/lon are provided
            location_geom_update = ""
            params_offset = 0
            if latitude is not None and longitude is not None:
                location_geom_update = ", location = ST_SetSRID(ST_Point($11, $12), 4326)::geometry"
                params_offset = 2

            query = f"""
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
                    {location_geom_update}
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                """
            
            # Prepare arguments dynamically based on whether location_geom_update is used
            args = [
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
            ]
            if params_offset > 0:
                args.extend([longitude, latitude]) # Order for ST_Point is (lon, lat)

            result = await conn.execute(query, *args)
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    
    async def delete_user_location(self, user_id: UUID, location_id: int) -> bool:
        """
        Soft-delete a user location by setting deleted_at timestamp.
        """
        async with self._atomic() as conn:
            result = await conn.execute(
                """
                UPDATE user_locations
                SET deleted_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                """,
                location_id,
                user_id,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    
    async def add_many_users(self, users_data: List[tuple]) -> int:
        """
        Bulk insert users and their personal data into the database.
        users_data is expected to be a list of tuples:
        (user_uuid, is_active, created_at, name, email, api_key)
        """
        self.debug_print(f"add_many_users: Adding {len(users_data)} users.")
        if not users_data:
            return 0

        users_records = []
        personal_data_records = []

        for user_uuid, is_active, created_at, name, email, api_key in users_data:
            users_records.append((user_uuid, is_active, created_at, None)) # deleted_at defaults to NULL
            personal_data_records.append((user_uuid, name, email, api_key, None, datetime.now())) # last_login, updated_at

        async with self._atomic() as conn:
            # Insert into 'users' table
            users_inserted = await conn.copy_records_to_table(
                'users',
                records=users_records,
                columns=['id', 'is_active', 'created_at', 'deleted_at']
            )
            self.debug_print(f"add_many_users: Inserted {users_inserted} user core records.")

            # Insert into 'user_personal_data' table
            personal_data_inserted = await conn.copy_records_to_table(
                'user_personal_data',
                records=personal_data_records,
                columns=['user_id', 'name', 'email', 'api_key', 'last_login', 'updated_at']
            )
            self.debug_print(f"add_many_users: Inserted {personal_data_inserted} user personal data records.")
            return users_inserted # Return count of core user records inserted

    
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
                    user_id UUID,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    country TEXT,
                    latitude DECIMAL(10, 7),
                    longitude DECIMAL(10, 7),
                    location_name TEXT,
                    created_at TIMESTAMP WITH TIME ZONE,
                    updated_at TIMESTAMP WITH TIME ZONE,
                    deleted_at TIMESTAMP WITH TIME ZONE
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
                        loc.deleted_at,
                    )
                    for loc in locations
                ),
            )
            result = await conn.execute(
                """
                INSERT INTO user_locations(
                    id, user_id, address, city, state, zip_code, country,
                    latitude, longitude, location_name, created_at, updated_at, deleted_at
                )
                SELECT * from temp_user_locations
                ON CONFLICT (id) DO NOTHING
                """
            )
            await conn.execute("DROP TABLE temp_user_locations")
            _, _, rowcount = result.split(" ")
            rowcount = int(rowcount)
            return rowcount

    
    async def save_user_preference(self, user_id: UUID, preference_key: str, preference_value: str) -> UserPreference:
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

    
    async def get_user_preference(self, user_id: UUID, preference_key: str) -> UserPreference | None:
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
