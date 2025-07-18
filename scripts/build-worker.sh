#!/bin/bash

# Stop and remove containers defined in docker-compose.worker.yml
docker compose -f docker-compose.worker.yml down --remove-orphans

# Build and restart services defined in docker-compose.worker.yml
docker compose -f docker-compose.worker.yml up -d --build --force-recreate
