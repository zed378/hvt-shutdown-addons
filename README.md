# hvt-shutdown-addons

Secure node shutdown service for Harvester clusters. Deploys as a DaemonSet to safely power off Harvester nodes by gracefully terminating VM workloads.

## Overview

This service runs as a DaemonSet on each node in a Harvester cluster. It exposes an HTTP endpoint that accepts authenticated shutdown requests, gracefully terminates all running `virt-launcher` pods on the target node, and then powers off the host system.

**Repository:** [github.com/zed378/hvt-shutdown-addons](https://github.com/zed378/hvt-shutdown-addons)

## Features

- **Security Hardening**: Rate limiting, audit logging, concurrent shutdown protection
- **Harvester Integration**: Designed for Harvester cluster management
- **Helm Chart**: Centralized configuration via `Charts/values.yaml`
- **Add-on Ready**: Can be deployed as a Harvester add-on

## Project Structure

```
.
├── .dockerignore
├── .gitignore
├── .editorconfig
├── Dockerfile
├── LICENSE
├── README.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   └── main.py              # FastAPI application with security hardening
├── Charts/
│   ├── daemonset.yaml        # Original environment variable snippets
│   ├── deployment.yaml       # Full DaemonSet + RBAC manifest (Helm template)
│   ├── secret.yaml           # Auth token secret (Helm template)
│   ├── addon.yaml            # Harvester add-on configuration
│   └── values.yaml           # Centralized Helm values
└── tests/
    ├── __init__.py
    └── test_main.py          # Unit tests
```

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/zed378/hvt-shutdown-addons.git
cd hvt-shutdown-addons
```

### 2. Build & Push Docker Image

```bash
docker build -t your-registry/node-shutdown-api:latest .
docker push your-registry/node-shutdown-api:latest
```

### 3. Configure & Deploy with Helm

```bash
# Edit values to customize
vim Charts/values.yaml

# Deploy to your Harvester cluster
helm install node-shutdown ./Charts/
```

## API Endpoints

### GET /healthz

Liveness probe endpoint. Returns `200 OK` when the service is running.

**Response:**

```json
{
  "status": "ok",
  "timestamp": "2026-06-30T04:21:00+00:00"
}
```

### GET /healthz/ready

Readiness probe endpoint. Returns `200 OK` when the service is ready to accept traffic.

**Response:**

```json
{
  "status": "ready",
  "timestamp": "2026-06-30T04:21:00+00:00"
}
```

### POST /system/shutdown

Initiates a graceful shutdown sequence on the node where the service is running.

**Authentication:** Bearer token required in `Authorization` header.

**Request:**

```
POST /system/shutdown
Authorization: Bearer <AUTH_TOKEN>
```

**Response:**

```json
{
  "status": "Shutdown sequence successfully initiated",
  "timestamp": "2026-06-30T04:21:00+00:00"
}
```

**Error Responses:**

| Status Code | Description                             |
| ----------- | --------------------------------------- |
| 401         | Invalid or missing authentication token |
| 409         | Shutdown already in progress            |
| 429         | Rate limit exceeded                     |
| 500         | Internal server error                   |

**Shutdown Behavior:**

1. Rate limit check (configurable requests per minute)
2. Concurrent shutdown protection (prevents multiple simultaneous shutdowns)
3. Lists all running pods on the current node
4. Identifies `virt-launcher-*` pods (Harvester VM workloads)
5. Deletes each virt-launcher pod with configurable grace period (default: 10s)
6. Attempts host shutdown via fallback chain:
   - `chroot /host systemctl poweroff`
   - `systemctl poweroff`
   - `/sbin/shutdown -h now`

## Security Improvements

### Authentication & Authorization

- **Bearer token** with constant-time comparison (`secrets.compare_digest`)
- **Audit logging** of all authentication attempts
- **Rate limiting** to prevent brute-force attacks

### Pod Security

- **hostIPC disabled**: Prevents inter-process communication host access
- **hostPID disabled**: Prevents PID namespace host access
- **Privilege escalation blocked**: `allowPrivilegeEscalation: false`
- **Capabilities dropped**: All Linux capabilities dropped (`DROP ALL`)

### Rate Limiting

- Configurable requests per minute (default: 10 per minute)
- Returns `429 Too Many Requests` when limit exceeded

### Audit Logging

- All requests logged with timestamp, method, path, status, duration
- Logs written to both stdout and file
- File path configurable via environment variable

### Concurrent Shutdown Protection

- Prevents multiple simultaneous shutdown requests
- Returns `409 Conflict` if shutdown already in progress

## Configuration

All values are centralized in `Charts/values.yaml`:

### Authentication

```yaml
auth:
  # Generate strong token: openssl rand -hex 32
  token: "your-secret-token-string"
```

### Image Configuration

```yaml
image:
  registry: "your-registry"
  repository: "node-shutdown-api"
  tag: "latest"
  pullPolicy: "IfNotPresent"
```

### Security Settings

```yaml
daemonset:
  hostNetwork: true # Required for node access
  hostIPC: false # Disabled for security
  hostPID: false # Disabled for security

podSecurity:
  allowPrivilegeEscalation: false
  drop_capabilities:
    - ALL
```

### Rate Limiting

```yaml
rateLimiting:
  enabled: true
  maxRequestsPerMinute: 10
```

### Audit Logging

```yaml
auditLogging:
  enabled: true
  path: "/var/log/shutdown-audit.log"
```

### Resource Limits

```yaml
resources:
  limits:
    cpu: "500m"
    memory: "256Mi"
  requests:
    cpu: "100m"
    memory: "128Mi"
```

### Environment Variables

| Variable                  | Description                             | Required | Default                     |
| ------------------------- | --------------------------------------- | -------- | --------------------------- |
| `NODE_NAME`               | Kubernetes node name (auto-filled)      | Yes      | -                           |
| `AUTH_TOKEN`              | Bearer token for API authentication     | Yes      | -                           |
| `GRACE_PERIOD_SECONDS`    | Pod termination grace period in seconds | No       | 10                          |
| `AUDIT_ENABLED`           | Enable audit logging                    | No       | true                        |
| `AUDIT_LOG_PATH`          | Path to audit log file                  | No       | /var/log/shutdown-audit.log |
| `MAX_REQUESTS_PER_MINUTE` | Rate limit threshold                    | No       | 10                          |

## Deployment Options

### Prerequisites

- Harvester Kubernetes cluster
- `kubectl` configured with cluster access
- Docker or Podman for building the image
- Helm 3.x (for Helm deployment)

### Option 1: Deploy with Helm (Recommended)

```bash
# Edit configuration
vim Charts/values.yaml

# Deploy
helm install node-shutdown ./Charts/

# Upgrade
helm upgrade node-shutdown ./Charts/

# Dry run to preview
helm install --dry-run --debug node-shutdown ./Charts/
```

### Option 2: Deploy with kubectl (Raw YAML)

```bash
# Generate base64 token
TOKEN=$(echo -n "your-secret-token" | base64)

# Apply the manifests
kubectl apply -f Charts/secret.yaml
kubectl apply -f Charts/deployment.yaml
```

### Option 3: Deploy as Harvester Add-on

For Harvester add-on deployment, use the `Charts/addon.yaml` configuration:

1. Build and push the Docker image
2. Update `Charts/values.yaml` with your image settings
3. Package the Helm chart:
   ```bash
   helm package Charts/
   ```
4. Create a Helm repository or use a chart URL
5. Configure Harvester to install the add-on

## Testing

Run the test suite:

```bash
pip install -r requirements.txt
pytest tests/
```

## Troubleshooting

### Pod not starting

- Check that the ServiceAccount has correct RBAC permissions
- Verify the image pull policy and registry credentials
- Check node taints and tolerations

### Shutdown fails

- Check logs: `kubectl logs -n harvester-system -l app=node-shutdown`
- Verify `AUTH_TOKEN` matches the one sent in requests
- Check that `NODE_NAME` is correctly populated
- Verify node has proper shutdown permissions

### Rate limit exceeded

- Increase the limit in `values.yaml`:
  ```yaml
  rateLimiting:
    maxRequestsPerMinute: 20
  ```

### Audit logs not appearing

- Verify audit logging is enabled in `values.yaml`
- Check the audit log volume is mounted correctly
- Check container logs: `kubectl logs -n harvester-system <pod-name>`

## Security Considerations

- **Authentication**: Uses Bearer token with constant-time comparison
- **Privileged Container**: Requires privileged mode for host shutdown
- **Network**: Uses `hostNetwork: true` — restrict access via network policies
- **Token Security**: Use strong, random tokens (`openssl rand -hex 32`)
- **Audit Logging**: Enable for production environments
- **Rate Limiting**: Configure based on your operational needs
