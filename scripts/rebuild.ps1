$confirm = Read-Host "WARNING: This will stop and remove all Docker containers and volumes, and rebuild the services. Are you sure you want to proceed? (y/N)"
if ($confirm -eq "y") {
    Write-Host "Stopping and removing all Docker containers..."
    docker stop (docker ps -aq)
    docker rm (docker ps -aq)

    Write-Host "Removing Docker volumes (excluding crawler_data)..."
    docker volume ls -q | Where-Object { $_ -notlike "*cijene-api-clone_crawler_data*" } | ForEach-Object { docker volume rm $_ }

    Write-Host "Restarting Docker Desktop Service..."
    # This command might fail if Docker Desktop is not running with admin privileges or if it's managed differently.
    # The user might need to manually restart Docker Desktop if this fails.
    try {
        Restart-Service -Name com.docker.service -ErrorAction Stop
        Write-Host "Docker Desktop Service restarted successfully."
    } catch {
        Write-Host "Failed to restart Docker Desktop Service programmatically. Please restart it manually if needed."
        Write-Host $_.Exception.Message
    }

    Write-Host "Rebuilding and restarting Docker services. Output redirected to logs/docker-build.log..."
    # Ensure the directory exists
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
    docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate >> logs/docker-build.log 2>&1
    Write-Host "Docker services rebuilt and restarted. Check logs/docker-build.log for details."

    docker compose ps
    
    Write-Host "Applying database migrations..."
    make migrate-db

} else {
    Write-Host "Operation cancelled."
}
