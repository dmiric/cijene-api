import os
from dotenv import load_dotenv

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from service.db.base import Database

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    _db: "Database | None" = None
    _db_v2: "Database | None" = None # New instance for v2 database

    def __init__(self):
        self.version: str = os.getenv("VERSION", "0.1.0")
        self.archive_dir: str = os.getenv("ARCHIVE_DIR", "data")
        self.base_url: str = os.getenv("BASE_URL", "https://api.cijene.dev")
        self.host: str = os.getenv("HOST", "0.0.0.0")
        self.port: int = int(os.getenv("PORT", "8000"))
        self.debug: bool = os.getenv("DEBUG", "false").lower() == "true"
        self.timezone: str = os.getenv("TIMEZONE", "Europe/Zagreb")
        self.redirect_url: str = os.getenv("REDIRECT_URL", "https://cijene.dev")

        # Database configuration
        self.db_dsn: str = os.getenv(
            "DB_DSN",
            "postgresql://postgres:postgres@localhost/cijene",
        )
        self.db_min_connections: int = int(os.getenv("DB_MIN_CONNECTIONS", "5"))
        self.db_max_connections: int = int(os.getenv("DB_MAX_CONNECTIONS", "20"))

    def get_db(self) -> "Database":
        """
        Get the database instance based on the configured settings.

        This method initializes the singleton database connection
        if it hasn't been done yet, and returns the instance.

        Returns:
            An instance of the Database subclass based on the DSN.

        """
        from service.db.base import Database

        if self._db is None:
            self._db = Database.from_url(
                self.db_dsn,
                min_size=self.db_min_connections,
                max_size=self.db_max_connections,
            )

        return self._db

    def get_db_v2(self) -> "Database":
        """
        Get the v2 database instance based on the configured settings.
        """
        from service.db.psql_v2 import PostgresDatabaseV2 # Import the v2 database class

        if self._db_v2 is None:
            self._db_v2 = PostgresDatabaseV2(
                self.db_dsn,
                min_size=self.db_min_connections,
                max_size=self.db_max_connections,
            )
        return self._db_v2


settings = Settings()
