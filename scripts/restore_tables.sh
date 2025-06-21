#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Database connection details from environment variables
DB_USER="${POSTGRES_USER}"
DB_NAME="${POSTGRES_DB}"
DB_HOST="db" # Service name in docker-compose
DB_PORT="5432"

# Export PGPASSWORD for pg_restore
export PGPASSWORD="${POSTGRES_PASSWORD}"

# Tables to restore
TABLES=("search_keywords" "user_locations" "users")

# Backup directory inside the container (mounted from db_backups volume)
BACKUP_DIR="/backups"

# Timestamp for the backup file
# If provided as an argument, use it. Otherwise, try to find the latest.
TIMESTAMP=${1}

if [ -z "$TIMESTAMP" ]; then
    echo "No timestamp provided. Attempting to find the latest backup timestamp..."
    # Find the latest timestamp from any of the backup files
    LATEST_FILE=$(ls -t ${BACKUP_DIR}/users_*.sql 2>/dev/null | head -n 1)
    if [ -z "$LATEST_FILE" ]; then
        echo "Error: No backup files found in ${BACKUP_DIR}. Please run backup_tables.sh first or provide a TIMESTAMP."
        exit 1
    fi
    # Extract timestamp from filename (e.g., users_YYYYMMDD_HHMMSS.sql)
    TIMESTAMP=$(basename "$LATEST_FILE" | sed -E 's/users_([0-9]{8}_[0-9]{6})\.sql/\1/')
    echo "Using latest timestamp: ${TIMESTAMP}"
fi

echo "Starting database restore for tables: ${TABLES[@]} from timestamp ${TIMESTAMP}"

for TABLE in "${TABLES[@]}"; do
    BACKUP_FILE="${BACKUP_DIR}/${TABLE}_${TIMESTAMP}.sql"
    echo "Restoring table ${TABLE} from ${BACKUP_FILE}..."
    if [ ! -f "$BACKUP_FILE" ]; then
        echo "Error: Backup file ${BACKUP_FILE} not found. Skipping table ${TABLE}."
        continue
    fi
    # Use --no-owner and --no-privileges to avoid issues with role mismatches
    pg_restore --clean --no-owner --no-privileges -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t "$TABLE" -Fc "$BACKUP_FILE"
    echo "Finished restoring table ${TABLE}."
done

echo "Database restore completed successfully."
