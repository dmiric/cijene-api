# Exit immediately if a command exits with a non-zero status.
$ErrorActionPreference = "Stop"

$TIMESTAMP = $args[0]

$BACKUP_DIR_IN_CONTAINER = "/backups"

Write-Host "Copying backup files to container's ${BACKUP_DIR_IN_CONTAINER}..."

# On local dev (Windows), copy from local host to container
# Note: docker cp requires the container name, not service name for host-to-container copy
# Assuming 'cijene-api-clone-backup-1' is the container name
docker compose cp ".\backups\." "backup:${BACKUP_DIR_IN_CONTAINER}"

Write-Host "Backup file copied successfully."
