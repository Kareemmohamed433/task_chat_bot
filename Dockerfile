# Production-conscious multi-service Dockerfile for AI Task Assistant
FROM python:3.11-slim-bookworm

# Python and environment configuration
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# Install system dependencies (curl for container health checks)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user and group
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /bin/bash appuser

# Set working directory
WORKDIR /app

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and baseline assets
COPY app/ /app/app/
COPY streamlit_app.py /app/streamlit_app.py
COPY pytest.ini /app/pytest.ini
COPY tests/ /app/tests/
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Copy existing tasks.db as initial baseline for persistent volume migration
COPY tasks.db /app/initial_tasks.db

# Prepare persistent data directory, set permissions, and configure entrypoint
RUN mkdir -p /app/data && \
    chmod +x /app/docker-entrypoint.sh && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose FastAPI and Streamlit ports
EXPOSE 8000 8501

# Health check configuration for FastAPI
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Configure container entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default entrypoint starts FastAPI with a single worker for SQLite safety
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
