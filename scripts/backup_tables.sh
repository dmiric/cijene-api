#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Database connection details from environment variables
DB_USER="${POSTGRES_USER}"
DB_NAME="${POSTGRES_DB}"
DB_HOST="db" # Service name in docker-compose
DB_PORT="5432"

# Export PGPASSWORD for pg_dump
export PGPASSWORD="${POSTGRES_PASSWORD}"

# Tables to backup
TABLES=("stores" "user_locations" "users")

# Backup directory inside the container (mounted from db_backups volume)
BACKUP_DIR="/backups"

# Timestamp for the backup file
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "Starting database backup for tables: ${TABLES[@]} at ${TIMESTAMP}"

for TABLE in "${TABLES[@]}"; do
BACKUP_FILE="${BACKUP_DIR}/${TABLE}_${TIMESTAMP}.sql"
echo "Dumping table ${TABLE} to ${BACKUP_FILE}..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t "$TABLE" -Fc > "$BACKUP_FILE"
echo "Finished dumping table ${TABLE}."
done

echo "Database backup completed successfully."
