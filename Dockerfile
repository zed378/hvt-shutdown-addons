# ---- Python runtime with FastAPI ----
# API-only image.
FROM python:3.11-slim

# Hardening / hygiene:
# - no .pyc files, unbuffered logs (so audit lines flush promptly)
# - pip: no cache, no version check chatter
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python dependencies first (better layer caching).
# Upgrade pip/setuptools to pick up security fixes, then install pinned deps.
COPY requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

# Copy API app
COPY app/ ./app

EXPOSE 8080

# NOTE: This container intentionally runs as root and is deployed privileged
# with the host root filesystem mounted at /host. Root is REQUIRED so the
# service can chroot into the host and power the baremetal node off. Isolation
# is provided by the DaemonSet's least-privilege RBAC and the auth-token-gated
# API, not by dropping the container user.
USER root

# Run via the module entrypoint so TLS_ENABLED and related settings are honored
# (uvicorn's SSL flags are applied inside app/main.py's __main__ block).
CMD ["python", "-m", "app.main"]
