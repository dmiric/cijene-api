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

# Backup directory inside the container (mounted from db_backups volume)
BACKUP_DIR="/backups"

# Timestamp for the backup file
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Temporary file path in /tmp, which is usually writable
TEMP_BACKUP_FILE="/tmp/full_db_${TIMESTAMP}.sql.gz"
FINAL_BACKUP_FILE="${BACKUP_DIR}/full_db_${TIMESTAMP}.sql.gz"

echo "Starting full database dump to ${TEMP_BACKUP_FILE}..."

# Dump to a temporary file in /tmp
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -Fc | gzip > "$TEMP_BACKUP_FILE"

echo "Full database dump completed successfully to ${TEMP_BACKUP_FILE}."
