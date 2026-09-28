# syntax=docker/dockerfile:1.4
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    ipmitool \
    && rm -rf /var/lib/apt/lists/*

# kubectl: VM start/stop/migrate is done with `kubectl patch|create|delete`
# using the pod's in-cluster ServiceAccount (same least-privilege RBAC).
# Pinned and checksum-verified. Keep within +/-1 minor of the Harvester k8s version.
ARG KUBECTL_VERSION=v1.34.3
ARG TARGETARCH=amd64
RUN python - <<EOF
import hashlib, os, urllib.request
base = "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl"
data = urllib.request.urlopen(base, timeout=120).read()
want = urllib.request.urlopen(base + ".sha256", timeout=60).read().decode().split()[0]
assert hashlib.sha256(data).hexdigest() == want, "kubectl checksum mismatch"
open("/usr/local/bin/kubectl", "wb").write(data)
os.chmod("/usr/local/bin/kubectl", 0o755)
EOF

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
