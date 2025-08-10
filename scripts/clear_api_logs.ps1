# =============================================================================
# clear_api_logs.ps1
#
# Clears the JSON log file for the 'api' service container. This script is now
# corrected to use the true physical path of the log files as discovered via
# the 'find' command inside the 'docker-desktop' WSL instance.
# =============================================================================

# --- Configuration ---
$composeFiles = "-f docker-compose.yml -f docker-compose.local.yml"
$serviceName = "api"
$wslDistroName = "docker-desktop"


# --- Script Body ---
Write-Host "Attempting to clear logs for service: '$serviceName'..."

# Step 1: Get the running container ID. This part remains correct.
$dockerCommand = "docker compose $composeFiles ps -q $serviceName"
$containerId = Invoke-Expression $dockerCommand

# Step 2: Proceed only if a container ID was found
if ($containerId -and ($containerId.Length -gt 0)) {
    if ($containerId -is [array]) { $containerId = $containerId[0] }

    Write-Host "Found container ID: $containerId"

    # --- THIS IS THE KEY FIX ---
    # Step 3: Manually construct the TRUE physical path based on your 'find' command results.
    # We are no longer using the misleading path from 'docker inspect'.
    $realPathPrefix = "/mnt/docker-desktop-disk/data/docker/containers"
    $linuxLogPath = "$realPathPrefix/$containerId/$containerId-json.log"

    # Step 4: Convert the real Linux path to a Windows-compatible WSL network path
    $windowsCompatiblePathFragment = $linuxLogPath.Replace('/', '\')
    $windowsAccessPath = "\\wsl$\$wslDistroName" + $windowsCompatiblePathFragment

    Write-Host "Constructed REAL log file path: $windowsAccessPath"

    # Step 5: Verify the path exists and clear the content
    if (Test-Path -Path $windowsAccessPath -PathType Leaf) {
        try {
            Clear-Content -Path $windowsAccessPath -ErrorAction Stop
            Write-Host "SUCCESS: Log file cleared." -ForegroundColor Green
        } catch {
            Write-Host "ERROR: Failed to clear content. The file might be locked." -ForegroundColor Red
            Write-Host $_.Exception.Message -ForegroundColor Red
        }
    } else {
        Write-Host "ERROR: Could not find the log file at the constructed path." -ForegroundColor Red
        Write-Host "This path was built based on the 'find' command results. Please double-check the prefix if this fails." -ForegroundColor Yellow
    }

} else {
    Write-Host "ERROR: Could not find a running container for the '$serviceName' service." -ForegroundColor Red
    Write-Host "Please ensure the service is running with 'docker compose up'." -ForegroundColor Yellow
}