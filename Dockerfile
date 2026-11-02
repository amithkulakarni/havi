# Azure AI Foundry Hosted Agent – Production Dockerfile
# Base Image: Python 3.11 slim
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8088 \
    RUNTIME_ROOT=/tmp/havi_mcd

# Install system dependencies (Poppler for PDF rendering, curl for health probes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications first for Docker layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure storage directories exist in both workspace and /tmp/havi_mcd with non-privileged user permissions
RUN mkdir -p output/results output/payloads output/reviews output/logs output/notifications temp uploads data && \
    mkdir -p /tmp/havi_mcd/output/results /tmp/havi_mcd/output/payloads /tmp/havi_mcd/output/reviews /tmp/havi_mcd/output/logs /tmp/havi_mcd/output/notifications /tmp/havi_mcd/temp /tmp/havi_mcd/uploads /tmp/havi_mcd/data && \
    chmod -R 777 /tmp/havi_mcd

# Run as non-privileged security user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8088

# Health check – invocations protocol liveness probe
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -sf http://localhost:${PORT}/health || exit 1

# Start the Azure AI Foundry Hosted Agent server
CMD ["python", "hosted_server.py"]
