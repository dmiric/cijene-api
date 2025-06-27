# send-chat.ps1
# This script sends a chat message to the v2 chat endpoint using httpie,
# logs the output, and clears the log file before each run.

param(
    [Parameter(Mandatory=$true)]
    [string]$Message,

    [string]$SessionId = $null,

    [string]$UserId = "1",

    [string]$ApiKey = "ec7cc315-c434-4c1f-aab7-3dba3545d113"
)

$LogFilePath = ".\logs\test-output.log"
$BASE_URL = "http://localhost:8000/v2/chat"

Write-Host "--- Sending Chat Message ---"

# Clear the log file before each run
Clear-Content -Path $LogFilePath

$command = "http -v POST $BASE_URL Authorization:`"Bearer $ApiKey`" user_id=$UserId message_text=`"$Message`""

if ($SessionId) {
    $command += " session_id=$SessionId"
}

Write-Host "Executing command: $command"

# Execute the httpie command and redirect output to the log file
Invoke-Expression "$command > $LogFilePath 2>&1"

# Read the log file to display output
$logContent = Get-Content -Path $LogFilePath -Raw
Write-Host "Output from log file:"
Write-Host $logContent

Write-Host "Chat message sent and output logged."
