FROM python:3.11-slim-bookworm

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal system utilities for health checks & network requests
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt

# Copy application source code
COPY . .

# Ensure entrypoint is executable if present
RUN chmod +x /app/docker-entrypoint.sh

# Volume for persistent output (contacts, emails, user data)
VOLUME ["/app/output"]

# Expose AutoApply Dashboard Web UI port
EXPOSE 5000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
