import os
from dotenv import load_dotenv

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from service.db.base import Database

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    _db: "Database | None" = None

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

        # JWT Authentication
        self.jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "super-secret-jwt-key")
        self.jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
        self.refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

        # Email Service
        self.smtp_server: str = os.getenv("SMTP_SERVER", "smtp.example.com")
        self.smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username: str = os.getenv("SMTP_USERNAME", "user@example.com")
        self.smtp_password: str = os.getenv("SMTP_PASSWORD", "password")
        self.sender_email: str = os.getenv("SENDER_EMAIL", "no-reply@example.com")
        self.email_verification_base_url: str = os.getenv("EMAIL_VERIFICATION_BASE_URL", "http://localhost:8000/auth/verify-email")

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

settings = Settings()
