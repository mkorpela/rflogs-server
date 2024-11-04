# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy only requirements to cache them in docker layer
COPY pyproject.toml poetry.lock* /app/

# Install project dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi

# Create log directory and file, set permissions
RUN mkdir -p /var/log/rflogs && \
    touch /var/log/rflogs/rflogs.log && \
    chown -R www-data:www-data /var/log/rflogs && \
    chmod 755 /var/log/rflogs && \
    chmod 644 /var/log/rflogs/rflogs.log

# Copy project
COPY rflogs_server /app/rflogs_server

# Switch to non-root user
USER www-data

# Run the application
CMD ["uvicorn", "rflogs_server.main:app", "--host", "0.0.0.0", "--port", "8000"]