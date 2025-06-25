#!/bin/bash

DATE="$1"

if docker compose exec -T crawler test -f /app/output/"$DATE".zip && [ ! -d "./output/"$DATE"_unzipped" ]; then
    echo "Existing zip found and not unzipped. Unzipping..."
    make unzip-crawler-output DATE="$DATE"
elif ! docker compose exec -T crawler test -f /app/output/"$DATE".zip; then
    echo "No existing zip found. Running sample crawl for lidl, kaufland, spar..."
    make crawl-sample-dev DATE="$DATE"
    make unzip-crawler-output DATE="$DATE"
else
    echo "Existing zip found and already unzipped. Skipping crawl/unzip."
fi
