param(
    [string]$ExcludeVolumes = ""
)

$confirm = Read-Host "WARNING: This will stop and remove all Docker containers and volumes, and rebuild the services. Are you sure you want to proceed? (y/N)"
if ($confirm -eq "y") {
    Write-Host "Stopping and removing all Docker containers..."
    $allContainers = docker ps -aq
    if ($allContainers) {
        docker stop $allContainers
        docker rm $allContainers
    } else {
        Write-Host "No Docker containers found to stop or remove."
    }

    $defaultExclude = "cijene-api-clone_crawler_data"
    $volumesToExclude = @()

    if (-not [string]::IsNullOrEmpty($ExcludeVolumes)) {
        $volumesToExclude = $ExcludeVolumes.Split(',') | ForEach-Object { $_.Trim() }
        Write-Host "Excluding specified volumes: $($volumesToExclude -join ', ')..."
    } else {
        $volumesToExclude = @($defaultExclude)
        Write-Host "Excluding default volume: $($defaultExclude)..."
    }

    Write-Host "Removing Docker volumes (excluding specified ones)..."
    docker volume ls -q | Where-Object {
        $volumeName = $_
        $shouldExclude = $false
        foreach ($excludePattern in $volumesToExclude) {
            if ($volumeName -like "*$excludePattern*") {
                $shouldExclude = $true
                break
            }
        }
        -not $shouldExclude
    } | ForEach-Object { docker volume rm -f $_ }

    Write-Host "Rebuilding and restarting Docker services. Output redirected to logs/docker-build.log..."
    # Ensure the directory exists
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
    docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate >> logs/docker-build.log 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Docker compose up failed. Check logs/docker-build.log for details."
        exit 1
    }
    Write-Host "Docker services rebuilt and restarted. Check logs/docker-build.log for details."

    docker compose ps

} else {
    Write-Host "Operation cancelled."
}
