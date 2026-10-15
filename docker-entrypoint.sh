#!/bin/sh
set -e

# Ensure data directory exists
mkdir -p /app/data

# Seed database volume with existing data if it doesn't exist yet
if [ ! -f /app/data/tasks.db ] && [ -f /app/initial_tasks.db ]; then
    echo "[Entrypoint] Initializing persistent volume database from initial tasks.db..."
    cp /app/initial_tasks.db /app/data/tasks.db
    chmod 664 /app/data/tasks.db || true
fi

exec "$@"