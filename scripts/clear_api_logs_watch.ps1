# =============================================================================
# watch-and-clear-logs.ps1 (Version 8.1 - Polling Method with Command Fix)
#
# This version fixes a small typo in the docker compose command string.
# =============================================================================

# --- Configuration ---
$config = @{
    serviceName        = "api"
    # The list of docker-compose files to use.
    composeFiles       = "-f docker-compose.yml -f docker-compose.local.yml"
    pathToWatch        = ".\service"
    wslDistroName      = "docker-desktop"
    wslLogPathPrefix   = "/mnt/docker-desktop-disk/data/docker/containers"
}


# --- Log Clearing Function ---
function Clear-DockerLogs {
    param($config)
    try {
        Write-Host "--------------------------------------------------------"
        Write-Host "Python file change detected. Triggering log clear action..." -ForegroundColor Cyan

        # --- THIS IS THE CORRECTED LINE ---
        # The command is now constructed correctly without extra spaces inside quotes.
        $dockerCommand = "docker compose $($config.composeFiles) ps -q $($config.serviceName)"
        $containerId = Invoke-Expression $dockerCommand

        if (-not $containerId) { throw "Could not find a running container for service '$($config.serviceName)'." }
        if ($containerId -is [array]) { $containerId = $containerId[0] }
        Write-Host "Found container ID: $containerId"

        $linuxLogPath = "$($config.wslLogPathPrefix)/$containerId/$containerId-json.log"
        $windowsAccessPath = "\\wsl$\$($config.wslDistroName)" + ($linuxLogPath.Replace('/', '\'))
        Write-Host "Attempting to access log at: $windowsAccessPath"

        if (Test-Path -Path $windowsAccessPath -PathType Leaf) {
            Clear-Content -Path $windowsAccessPath
            Write-Host "SUCCESS: Log file cleared." -ForegroundColor Green
        } else {
            throw "Log file does not exist at the constructed path."
        }
    }
    catch {
        Write-Host "ERROR: An error occurred during the log clearing process." -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
}


# --- Main Polling Loop ---

Write-Host "✅ Starting watcher using reliable polling method..."
Write-Host "Monitoring for .py file changes in '$($config.pathToWatch)'."
Write-Host "Press 'Ctrl+C' to stop."

$fileStates = Get-ChildItem -Path $config.pathToWatch -Filter "*.py" -Recurse | Select-Object FullName, LastWriteTime

while ($true) {
    Start-Sleep -Seconds 1
    $currentFiles = Get-ChildItem -Path $config.pathToWatch -Filter "*.py" -Recurse | Select-Object FullName, LastWriteTime
    $comparison = Compare-Object -ReferenceObject $fileStates -DifferenceObject $currentFiles -Property FullName, LastWriteTime -PassThru

    if ($comparison) {
        Clear-DockerLogs -config $config
        $fileStates = $currentFiles
    }
}