# Exit immediately if a command exits with a non-zero status.
$ErrorActionPreference = "Stop"

# --- Main Script Logic ---
Write-Host "Running universal restore script. Environment: development"
Write-Host "Executing local restore on development machine..."

# Database connection details from arguments
$DB_USER = $args[1]
$DB_PASSWORD = $args[2]
$DB_NAME = $args[3]
$DB_HOST = $args[4]
$DB_PORT = $args[5]

# Ensure the password environment variable is available for the script
$PGPassword = $DB_PASSWORD
if ([string]::IsNullOrEmpty($PGPassword)) {
    Write-Error "POSTGRES_PASSWORD argument is not set."
    exit 1
}

# Backup directory inside the container
$BACKUP_DIR = "/backups"

# Timestamp for the backup file
# If provided as an argument, use it. Otherwise, find the latest.
$TIMESTAMP = $args[0]

if ([string]::IsNullOrEmpty($TIMESTAMP)) {
    Write-Host "No timestamp provided. Attempting to find the latest full database backup timestamp from host's ./backups/ directory..."
    # Find the latest timestamp from any of the backup files on the host
    $LATEST_FILE_ON_HOST = Get-ChildItem -Path ".\backups\full_db_*.sql.gz" | Sort-Object LastWriteTime -Descending | Select-Object -First 1

    if (-not $LATEST_FILE_ON_HOST) {
        Write-Error "No full database backup files found in ./backups/ on the host."
        exit 1
    }
    # Extract timestamp from filename (e.g., full_db_YYYYMMDD_HHMMSS.sql.gz)
    $FILENAME = $LATEST_FILE_ON_HOST.Name
    $TIMESTAMP = $FILENAME -replace "full_db_([0-9]{8}_[0-9]{6})\.sql\.gz", '$1'
    Write-Host "Using latest timestamp: $TIMESTAMP"
}

$BACKUP_FILE_IN_CONTAINER = "${BACKUP_DIR}/full_db_${TIMESTAMP}.sql.gz"

Write-Host "Starting full database restore from $BACKUP_FILE_IN_CONTAINER..."

# Decompress and restore the database using a cleaner, more robust command.
# We use `docker compose exec --env` to pass the password securely without quoting issues.
docker compose exec backup gzip -dc "$BACKUP_FILE_IN_CONTAINER" | docker compose exec -T --env "PGPASSWORD=$PGPassword" db pg_restore --username "$DB_USER" --dbname "$DB_NAME" --host "$DB_HOST" --port "$DB_PORT"

Write-Host "Full database restore completed successfully."
