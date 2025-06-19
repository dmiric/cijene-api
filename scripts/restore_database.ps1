# Exit immediately if a command exits with a non-zero status.
$ErrorActionPreference = "Stop"

# Database connection details from environment variables
$DB_USER = $env:POSTGRES_USER
$DB_NAME = $env:POSTGRES_DB
$DB_HOST = "db" # Service name in docker-compose
$DB_PORT = "5432"

# Export PGPASSWORD for pg_restore
$env:PGPASSWORD = $env:POSTGRES_PASSWORD

# Backup directory inside the container (mounted from db_backups volume)
$BACKUP_DIR = "/backups"

# Timestamp for the backup file
# If provided as an argument, use it. Otherwise, try to find the latest.
$TIMESTAMP = $args[0]

if ([string]::IsNullOrEmpty($TIMESTAMP)) {
    Write-Host "No timestamp provided. Attempting to find the latest full database backup timestamp..."
    # Find the latest timestamp from any of the backup files inside the container
    $LATEST_FILE_PATH_IN_CONTAINER = (docker compose exec backup ls -t "${BACKUP_DIR}/full_db_*.sql.gz" 2>$null | Select-Object -First 1).Trim()

    if ([string]::IsNullOrEmpty($LATEST_FILE_PATH_IN_CONTAINER)) {
        Write-Host "Error: No full database backup files found in ${BACKUP_DIR} inside the container. Please run 'make dump-database' first or provide a TIMESTAMP."
        exit 1
    }
    # Extract timestamp from filename (e.g., full_db_YYYYMMDD_HHMMSS.sql.gz)
    $FILENAME = [System.IO.Path]::GetFileName($LATEST_FILE_PATH_IN_CONTAINER)
    $TIMESTAMP = $FILENAME -replace "full_db_([0-9]{8}_[0-9]{6})\.sql\.gz", '$1'
    Write-Host "Using latest timestamp: ${TIMESTAMP}"
}

$BACKUP_FILE_IN_CONTAINER = "${BACKUP_DIR}/full_db_${TIMESTAMP}.sql.gz"

Write-Host "Starting full database restore from ${BACKUP_FILE_IN_CONTAINER}..."

# Restore the database using pg_restore directly on the gzipped custom format file
docker compose exec backup pg_restore --clean -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" "$BACKUP_FILE_IN_CONTAINER"

Write-Host "Full database restore completed successfully."
