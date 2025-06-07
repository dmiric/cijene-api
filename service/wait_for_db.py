import os
import sys
import time
import asyncpg

async def wait_for_db():
    db_url = os.getenv("DB_DSN")
    if not db_url:
        print("DB_DSN environment variable not set.", file=sys.stderr)
        sys.exit(1)

    print("Waiting for database to be ready...", file=sys.stderr)
    retries = 10
    for i in range(retries):
        try:
            conn = await asyncpg.connect(db_url)
            await conn.close()
            print("Database is ready!", file=sys.stderr)
            return
        except Exception as e:
            print(f"Attempt {i+1}/{retries}: Database connection failed: {e}", file=sys.stderr)
            time.sleep(5) # Wait for 5 seconds before retrying
    print("Failed to connect to database after multiple retries.", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    import asyncio
    asyncio.run(wait_for_db())

    # Once database is ready, start the FastAPI application
    # This assumes uvicorn is installed and on PATH (or run via python -m)
    os.execvpe("python", ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8000"], os.environ)
