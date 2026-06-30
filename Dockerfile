FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Install required system packages including chroot for host shutdown
RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app

# Set ownership to non-root user
RUN chown -R appuser:appuser /app

# Note: Running as root is required for chroot /host to access host filesystem
# The container already runs in privileged mode via securityContext
EXPOSE 8080

# Run as root for host shutdown capability (privileged mode handles security)
USER root

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]