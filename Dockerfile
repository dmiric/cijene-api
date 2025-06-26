# Use a slim Python image as the base
FROM python:3.10-slim-buster

# Install system dependencies in a single, efficient step
# This prevents issues with stale package lists from Docker's cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Set PYTHONPATH to include the application directory and the service directory
ENV PYTHONPATH=/app:/app/service

# Copy requirements.txt for dependency installation
COPY requirements.txt ./

# Install project dependencies using pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project into the container
COPY . .

# Expose the port the FastAPI application will run on
EXPOSE 8000

# Command to run the FastAPI application
CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8000"]