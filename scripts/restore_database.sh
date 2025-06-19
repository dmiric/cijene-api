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

# Backup directory inside the container (mounted from db_backups volume)
BACKUP_DIR="/backups"

# Timestamp for the backup file
# If provided as an argument, use it. Otherwise, try to find the latest.
TIMESTAMP=${1}

if [ -z "$TIMESTAMP" ]; then
    echo "No timestamp provided. Attempting to find the latest full database backup timestamp..."
    # Find the latest timestamp from any of the backup files
    LATEST_FILE=$(ls -t ${BACKUP_DIR}/full_db_*.sql.gz 2>/dev/null | head -n 1)
    if [ -z "$LATEST_FILE" ]; then
        echo "Error: No full database backup files found in ${BACKUP_DIR}. Please run dump_database.sh first or provide a TIMESTAMP."
        exit 1
    fi
    # Extract timestamp from filename (e.g., full_db_YYYYMMDD_HHMMSS.sql.gz)
    TIMESTAMP=$(basename "$LATEST_FILE" | sed -E 's/full_db_([0-9]{8}_[0-9]{6})\.sql\.gz/\1/')
    echo "Using latest timestamp: ${TIMESTAMP}"
fi

BACKUP_FILE="${BACKUP_DIR}/full_db_${TIMESTAMP}.sql.gz"

echo "Starting full database restore from ${BACKUP_FILE}..."

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file ${BACKUP_FILE} not found."
    exit 1
fi

# Decompress and restore the database
gzip -dc "$BACKUP_FILE" | pg_restore --clean -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"

echo "Full database restore completed successfully."
