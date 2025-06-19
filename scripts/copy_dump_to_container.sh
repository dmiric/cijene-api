#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

TIMESTAMP="${1}"

# Get variables from environment
ENVIRONMENT="${ENVIRONMENT}"
SSH_USER="${SSH_USER}"
DB_USER="${POSTGRES_USER}"
DB_PASSWORD="${POSTGRES_PASSWORD}"
DB_NAME="${POSTGRES_DB}"
DB_HOST="db" # Service name in docker-compose
DB_PORT="5432"

BACKUP_DIR_IN_CONTAINER="/backups"

echo "Copying backup files to container's ${BACKUP_DIR_IN_CONTAINER}..."

if [ "$ENVIRONMENT" = "production" ]; then
    # On server, copy from server's local path to container
    LATEST_DUMP_FILE=$(ls -t /home/"$SSH_USER"/pricemice/backups/full_db_"${TIMESTAMP}"*.sql.gz 2>/dev/null | head -n 1)
    if [ -z "$LATEST_DUMP_FILE" ]; then
        echo "Error: No full database dump found on server host in /home/${SSH_USER}/pricemice/backups/. Please ensure the file is there."
        exit 1
    fi
    docker compose cp "$LATEST_DUMP_FILE" backup:"${BACKUP_DIR_IN_CONTAINER}"/
else
    # On local dev (Linux), copy from local host to container
    docker compose cp ./backups/. backup:"${BACKUP_DIR_IN_CONTAINER}"/
fi

echo "Backup file copied successfully."
