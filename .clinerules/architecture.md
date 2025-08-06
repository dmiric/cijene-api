# Project Architecture Overview: Database Refactoring

This document outlines the current architecture of the database interaction layer, focusing on the recent refactoring efforts to introduce a more modular and maintainable structure using the Facade and Repository patterns.

## 1. New Directory Structure

The `service/db/repositories` directory now clearly distinguishes between different data domains:

```
service/
└── db/
    ├── ...
    └── repositories/
        ├── __init__.py
        ├── base.py                 # NEW: Defines BaseRepository (common connection logic)
        ├── product_repo.py         # Handles LEGACY `products` and `chain_products` tables.
        ├── golden_product_repo.py  # Handles ALL `g_*` (golden record) tables.
        ├── store_repo.py           # Handles LEGACY `stores` and `chains`.
        ├── user_repo.py            # Handles `users`, `user_locations`, `user_preferences`.
        ├── chat_repo.py            # Handles `chat_messages`.
        └── stats_repo.py           # Handles `chain_stats`.
```

## 2. Database Layer Architecture

The refactored database layer now adheres to the Facade and Repository patterns, providing a clean separation of concerns.

### Key Components:

*   **`service/db/base.py`**:
    *   **`BaseRepository` (Abstract Class)**: This new abstract class serves as the base for all concrete repository implementations. It defines the common methods for database connection management (`connect`, `close`, `_get_conn`, `_atomic`, `_fetchval`) that are shared across all repositories. This prevents code duplication and ensures consistent connection handling.
    *   **`Database` (Abstract Class)**: This remains the top-level abstract interface for the *entire* database interaction layer. It declares all the high-level data access operations that the application expects, without specifying how they are implemented.

*   **`service/db/repositories/*.py` (Concrete Repositories)**:
    *   Each file in this directory (`product_repo.py`, `golden_product_repo.py`, `store_repo.py`, ``user_repo.py`, `chat_repo.py`, `stats_repo.py`) now represents a specific data domain.
    *   Each concrete repository inherits from `BaseRepository`, implementing only the connection management methods and the data access logic relevant to its specific domain (e.g., `ProductRepository` only deals with products, not users or stores).
    *   The `pgvector.asyncpg.register_vector(conn)` initialization is no longer in individual repositories; it's handled once at the main `PostgresDatabase` connection.

*   **`service/db/psql.py` (`PostgresDatabase` - The V1 Facade)**:
    *   This class acts as the primary facade for the legacy (V1) database interactions.
    *   It inherits from the `Database` abstract class, meaning it is responsible for implementing *all* the abstract methods defined in `Database`.
    *   It *composes* instances of all the concrete repository classes (e.g., `self.products = ProductRepository(...)`, `self.stores = StoreRepository(...)`).
    *   All calls to its implemented methods are *delegated* to the appropriate repository instance (e.g., `db.add_store(...)` now calls `self.stores.add_store(...)`).
    *   It manages the central connection pool and ensures all composed repositories share this single pool.

This architecture provides a robust and scalable foundation for the application's data interactions.
