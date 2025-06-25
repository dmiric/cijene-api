#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Parse arguments
EXCLUDE_VOLUMES=""
for i in "$@"; do
    case $i in
        --exclude=*)
        EXCLUDE_VOLUMES="${i#*=}"
        shift # past argument=value
        ;;
        *)
        # unknown option
        ;;
    esac
done

confirm="n"
read -p "WARNING: This will stop and remove all Docker containers and volumes, and rebuild the services. Are you sure you want to proceed? (y/N) " confirm

if [ "$confirm" = "y" ]; then
    echo "Stopping and removing all Docker containers..."
    docker stop $(docker ps -aq) || true
    docker rm $(docker ps -aq) || true

    default_exclude="cijene-api-clone_crawler_data"
    volumes_to_exclude=()

    if [ -n "$EXCLUDE_VOLUMES" ]; then
        IFS=',' read -ra ADDR <<< "$EXCLUDE_VOLUMES"
        for i in "${ADDR[@]}"; do
            volumes_to_exclude+=("$i")
        done
        echo "Excluding specified volumes: ${volumes_to_exclude[@]}..."
    else
        volumes_to_exclude+=("$default_exclude")
        echo "Excluding default volume: ${default_exclude}..."
    fi

    echo "Removing Docker volumes (excluding specified ones)..."
    for volume in $(docker volume ls -q); do
        should_exclude=false
        for exclude_pattern in "${volumes_to_exclude[@]}"; do
            if [[ "$volume" == *"$exclude_pattern"* ]]; then
                should_exclude=true
                break
            fi
        done
        if [ "$should_exclude" = false ]; then
            docker volume rm "$volume" || true
        fi
    done

    echo "Docker Desktop Service restart might be needed manually if issues arise."

    echo "Rebuilding and restarting Docker services. Output redirected to logs/docker-build.log..."
    mkdir -p logs # Ensure directory exists
    docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build --force-recreate > logs/docker-build.log 2>&1
    echo "Docker services rebuilt and restarted. Check logs/docker-build.log for details."

    docker compose ps

    echo "Operation completed."
else
    echo "Operation cancelled."
fi
