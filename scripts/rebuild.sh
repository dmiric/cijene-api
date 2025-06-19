#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

read -p "WARNING: This will stop and remove all Docker containers and volumes, and rebuild the services. Are you sure you want to proceed? (y/N) " confirm
if [ "$confirm" = "y" ]; then
    echo "Stopping and removing all Docker containers..."
    docker stop $(docker ps -aq) || true
    docker rm $(docker ps -aq) || true

    echo "Removing all Docker volumes..."
    docker volume rm $(docker volume ls -q) || true

    echo "Docker Desktop Service restart might be needed manually if issues arise."

    echo "Rebuilding and restarting Docker services..."
    docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate

    echo "Applying database migrations..."
    make migrate-db

    echo "Operation completed."
else
    echo "Operation cancelled."
fi
