# Exit immediately if a command exits with a non-zero status.
$ErrorActionPreference = "Stop"

$TIMESTAMP = $args[0]
$ENVIRONMENT = $env:ENVIRONMENT
$SSH_USER = $env:SSH_USER
$SSH_IP = $env:SSH_IP

Write-Host "Running universal restore script (PowerShell). Environment: $($ENVIRONMENT)"

if ($ENVIRONMENT -eq "production") {
    Write-Host "Executing remote restore on production server..."
    # SSH into the server and execute the universal_restore_database.sh script
    # Ensure ssh-agent is running and key is added for passwordless login
    # The remote command needs to be quoted properly for SSH
    $remoteCommand = "cd /home/$SSH_USER/pricemice && bash ./scripts/universal_restore_database.sh `"$TIMESTAMP`""
    ssh-add ~/.ssh/github_actions_deploy_key; ssh "$SSH_USER@$SSH_IP" "$remoteCommand"
} else {
    Write-Host "Executing local restore on development machine..."
    # On local dev (Windows), use PowerShell scripts for copying and restoring
    pwsh -File ./scripts/copy_dump_to_container.ps1 "$TIMESTAMP"
    pwsh -File ./scripts/restore_database.ps1 "$TIMESTAMP"
}

Write-Host "Universal database restore process completed successfully."
