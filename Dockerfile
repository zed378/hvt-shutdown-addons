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
#
# kubectl only supports +/-1 minor of the API server, so several versions are
# bundled under /opt/kubectl/<version>/kubectl and app/kubectl_resolver.py picks
# the one matching the running cluster at startup. On Harvester (RKE2) the host's
# own kubectl is preferred anyway; these are the fallback.
#
# Default: every minor from 1.30 so any cluster from k8s 1.30 up gets an
# exact-minor match (and 1.37 is still covered by 1.36 at +/-1 skew).
# To support a newer Harvester, append its Kubernetes minor and rebuild:
#   Harvester v1.7 -> 1.34   v1.8 -> 1.35   v1.9 -> 1.36
# Entries are "1.36" (latest patch of that minor, resolved at build time) or an
# exact "v1.36.4". Every binary is sha256-verified (~57 MB each). Empty = host
# kubectl only. Override per build: --build-arg KUBECTL_VERSIONS="1.34 1.35 1.36"
ARG KUBECTL_VERSIONS="1.30 1.31 1.32 1.33 1.34 1.35 1.36"
ARG TARGETARCH=amd64
RUN python - <<'EOF'
import hashlib, os, urllib.request
arch = os.environ.get("TARGETARCH") or "amd64"
get = lambda url, t=120: urllib.request.urlopen(url, timeout=t).read()
installed = []
for entry in os.environ.get("KUBECTL_VERSIONS", "").split():
    ver = entry if entry.startswith("v") else get(f"https://dl.k8s.io/release/stable-{entry}.txt", 60).decode().strip()
    base = f"https://dl.k8s.io/release/{ver}/bin/linux/{arch}/kubectl"
    data = get(base)
    want = get(base + ".sha256", 60).decode().split()[0]
    assert hashlib.sha256(data).hexdigest() == want, f"kubectl {ver} checksum mismatch"
    os.makedirs(f"/opt/kubectl/{ver}", exist_ok=True)
    path = f"/opt/kubectl/{ver}/kubectl"
    open(path, "wb").write(data)
    os.chmod(path, 0o755)
    installed.append((tuple(int(x) for x in ver.lstrip("v").split(".")), path))
    print("bundled kubectl", ver)
if installed:
    # Convenience for `kubectl exec ... kubectl`: newest bundled on PATH.
    os.symlink(max(installed)[1], "/usr/local/bin/kubectl")
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
