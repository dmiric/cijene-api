#!/bin/bash
# This script configures Croatian Full-Text Search.
# It will be executed automatically by the postgres container on first startup.
set -e

-- Ensure the pgvector extension is created
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

echo "--- CONFIGURING CROATIAN FULL-TEXT SEARCH ---"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- 1. Create the Text Search Dictionary using the installed hunspell files.
    -- This dictionary knows about Croatian word stems.
    CREATE TEXT SEARCH DICTIONARY croatian_hunspell (
        TEMPLATE = ispell,
        DictFile = hr_hr,
        AffFile = hr_hr,
        StopWords = croatian
    );

    -- 2. Create the main Text Search Configuration for Croatian ('hr').
    -- We copy the 'simple' configuration as a base.
    CREATE TEXT SEARCH CONFIGURATION hr (COPY = simple);

    -- 3. Tell the 'hr' configuration to use our new Croatian dictionary
    -- for various types of words. This enables proper stemming.
    ALTER TEXT SEARCH CONFIGURATION hr
        ALTER MAPPING FOR asciiword, hword_asciipart, hword_part, hword, hword_numpart, word
        WITH croatian_hunspell, simple;

    SELECT 'Successfully created Croatian FTS configuration "hr".' AS status;
EOSQL

echo "--- CROATIAN FULL-TEXT SEARCH CONFIGURED ---"
