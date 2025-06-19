#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Get variables from environment (exported by Makefile)
ENVIRONMENT="${ENVIRONMENT}"
SSH_USER="${SSH_USER}"
TIMESTAMP="${1}" # Passed as argument from Makefile

# Export PostgreSQL credentials for sub-scripts
export POSTGRES_USER="${POSTGRES_USER}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"
export POSTGRES_DB="${POSTGRES_DB}"

# Determine OS for dispatching to PowerShell or Bash scripts
# Check if pwsh is available (indicates Windows/PowerShell environment)
if command -v pwsh &> /dev/null; then
    IS_WINDOWS=true
else
    IS_WINDOWS=false
fi

echo "Running universal restore script. Environment: ${ENVIRONMENT}, OS: $(if $IS_WINDOWS; then echo "Windows"; else echo "Linux"; fi)"

# --- Step 1: Copy dump file to container ---
if [ "$IS_WINDOWS" = true ]; then
    echo "Copying dump file (Windows path)..."
    pwsh -File ./scripts/copy_dump_to_container.ps1 "$TIMESTAMP"
else
    echo "Copying dump file (Linux path)..."
    # copy_dump_to_container.sh handles production/local logic internally
    bash ./scripts/copy_dump_to_container.sh "$ENVIRONMENT" "$SSH_USER" "$TIMESTAMP"
fi

echo "Starting full database restore..."

# --- Step 2: Perform database restore ---
if [ "$IS_WINDOWS" = true ]; then
    echo "Restoring database (Windows path)..."
    pwsh -File ./scripts/restore_database.ps1 "$TIMESTAMP"
else
    echo "Restoring database (Linux path)..."
    # restore_database.sh already has docker compose exec backup
    bash ./scripts/restore_database.sh "$TIMESTAMP"
fi

echo "Universal database restore process completed successfully."
