# send-chat.ps1

# Define the parameters with default values and validation
param(
    [Parameter(Mandatory=$true)]
    [string]$Message,

    [Parameter(Mandatory=$true)]
    [string]$ApiKey,
    
    [Parameter(Mandatory=$true)]
    [int]$UserId,

    [string]$SessionId
)

# --- Script Start ---
Write-Host "Sending chat message to a local server..."
Write-Host "User ID: $UserId"
Write-Host "Message: $Message"

# Build the request body as a PowerShell object
$requestBody = @{
    user_id = $UserId
    message_text = $Message
}

# Conditionally add the session_id if it was provided
if ($PSBoundParameters.ContainsKey('SessionId')) {
    $requestBody.Add('session_id', $SessionId)
    Write-Host "Session ID: $SessionId"
}

# Convert the PowerShell object to a compact JSON string
$jsonPayload = $requestBody | ConvertTo-Json -Compress

# Display the payload for debugging
Write-Host "Sending JSON Payload: $jsonPayload"

# Define headers
$headers = @{
    "Authorization" = "Bearer $ApiKey"
    "Content-Type"  = "application/json"
    "Accept"        = "text/event-stream"
}

# Execute the curl command (using Invoke-RestMethod for better PowerShell integration)
try {
    Invoke-RestMethod -Uri "http://localhost:8000/v2/chat" -Method Post -Headers $headers -Body $jsonPayload -TimeoutSec 0
} catch {
    Write-Error "An error occurred during the API call: $_"
}
