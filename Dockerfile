FROM python:3.9-slim

WORKDIR /app

# Install required packages including gosu for user switching
RUN apt-get update && apt-get install -y \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user with explicit UID/GID
RUN groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -m appuser

# Create directory structure with appropriate permissions
RUN mkdir -p /app/data /app/src && \
    touch /app/src/config.py && \
    chown -R appuser:appgroup /app && \
    chmod -R 755 /app

# Copy application files AFTER setting permissions
COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup entrypoint.sh .

# Make the entrypoint script executable
RUN chmod +x entrypoint.sh

# Set environment variables
ENV NPM_API_URL="http://nginx-proxy-manager:81" \
    NPM_API_USER="admin@example.com" \
    NPM_API_PASS="changeme" \
    UPDATE_INTERVAL=300 \
    PYTHONPATH=/app

# Create volume for persistent data
VOLUME ["/app/data"]

# Run the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]