#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Database connection details from environment variables
# Database connection details from environment variables
DB_USER="${POSTGRES_USER}"
DB_PASSWORD="${POSTGRES_PASSWORD}"
DB_NAME="${POSTGRES_DB}"
DB_HOST="db" # Service name in docker-compose
DB_PORT="5432"

# Export PGPASSWORD for pg_restore
export PGPASSWORD="${DB_PASSWORD}"

# Backup directory inside the container (mounted from db_backups volume)
BACKUP_DIR="/backups"

# Timestamp for the backup file
# If provided as an argument, use it. Otherwise, try to find the latest.
TIMESTAMP=${1}

if [ -z "$TIMESTAMP" ]; then
    echo "No timestamp provided. Attempting to find the latest full database backup timestamp from host's ./backups/ directory..."
    # Find the latest timestamp from any of the backup files on the host
    LATEST_FILE=$(ls -t ./backups/full_db_*.sql.gz 2>/dev/null | head -n 1)
    if [ -z "$LATEST_FILE" ]; then
        echo "Error: No full database backup files found in ./backups/ on the host. Please run 'make dump-database' first or provide a TIMESTAMP."
        exit 1
    fi
    # Extract timestamp from filename (e.g., full_db_YYYYMMDD_HHMMSS.sql.gz)
    TIMESTAMP=$(basename "$LATEST_FILE" | sed -E 's/full_db_([0-9]{8}_[0-9]{6})\.sql\.gz/\1/')
    echo "Using latest timestamp: ${TIMESTAMP}"
fi

BACKUP_FILE_ON_HOST="./backups/full_db_${TIMESTAMP}.sql.gz"
BACKUP_FILE_IN_CONTAINER="${BACKUP_DIR}/full_db_${TIMESTAMP}.sql.gz"

echo "Starting full database restore from ${BACKUP_FILE_IN_CONTAINER}..."

# Decompress and restore the database using a cleaner, more robust command.
# We use `docker compose exec --env` to pass the password securely without quoting issues.
# First, explicitly drop and recreate the database to ensure a clean slate
echo "Dropping and recreating database '${DB_NAME}' (connecting to 'postgres' database)..."
docker compose exec -T --env "PGPASSWORD=${DB_PASSWORD}" db psql -U "${DB_USER}" -h "${DB_HOST}" -p "${DB_PORT}" -d postgres -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
docker compose exec -T --env "PGPASSWORD=${DB_PASSWORD}" db psql -U "${DB_USER}" -h "${DB_HOST}" -p "${DB_PORT}" -d postgres -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
echo "Database '${DB_NAME}' recreated successfully."

# Now, proceed with pg_restore
docker compose exec backup gzip -dc "$BACKUP_FILE_IN_CONTAINER" | \
docker compose exec -T --env "PGPASSWORD=${DB_PASSWORD}" db \
  pg_restore --clean --no-owner --no-privileges \
             --username "${DB_USER}" \
             --dbname "${DB_NAME}" \
             --host "${DB_HOST}" \
             --port "${DB_PORT}"

echo "Full database restore completed successfully."
