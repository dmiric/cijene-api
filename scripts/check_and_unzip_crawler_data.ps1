param(
    [string]$Date = ""
)

$zip_exists_cmd = "docker compose exec -T crawler powershell -Command `"if (Test-Path /app/output/$Date.zip) { Write-Host 'true' } else { Write-Host 'false' }`""
$zip_exists = (Invoke-Expression $zip_exists_cmd).Trim()

$unzipped_dir_exists = (Test-Path -Path "./output/$Date_unzipped")

if ($zip_exists -eq "true" -and -not $unzipped_dir_exists) {
    Write-Host "Existing zip found and not unzipped. Unzipping..."
    make unzip-crawler-output DATE=$Date
} elseif ($zip_exists -ne "true") {
    Write-Host "No existing zip found. Running sample crawl for lidl, kaufland, spar..."
    make crawl-sample-dev DATE=$Date
    make unzip-crawler-output DATE=$Date
} else {
    Write-Host "Existing zip found and already unzipped. Skipping crawl/unzip."
}
