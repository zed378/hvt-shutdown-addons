# Node Shutdown API

A Kubernetes service that safely shuts down nodes in a Harvester cluster by terminating virtual machine workloads and powering off the host system.

## Overview

This service runs as a DaemonSet on each node in the cluster. It exposes an HTTP endpoint that accepts authenticated shutdown requests, gracefully terminates all running `virt-launcher` pods on the target node, and then powers off the host system.

## Project Structure

```
.
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   └── main.py              # FastAPI application
├── Charts/
│   ├── daemonset.yaml        # Environment variable snippets
│   ├── deployment.yaml       # Full DaemonSet + RBAC manifest
│   ├── secret.yaml           # Auth token secret
│   └── values.yaml           # Helm values
└── tests/
    ├── __init__.py
    └── test_main.py          # Unit tests
```

## API Endpoints

### GET /healthz

Liveness probe endpoint. Returns `200 OK` when the service is running.

**Response:**

```json
{
  "status": "ok"
}
```

### GET /healthz/ready

Readiness probe endpoint. Returns `200 OK` when the service is ready to accept traffic.

**Response:**

```json
{
  "status": "ready"
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
  "status": "Shutdown sequence successfully initiated."
}
```

**Behavior:**

1. Lists all running pods on the current node
2. Identifies `virt-launcher-*` pods (Harvester VM workloads)
3. Deletes each virt-launcher pod with configurable grace period (default: 10s)
4. Attempts host shutdown via chroot → systemctl → /sbin/shutdown (fallback chain)

## Configuration

### Environment Variables

| Variable               | Description                             | Required | Default |
| ---------------------- | --------------------------------------- | -------- | ------- |
| `NODE_NAME`            | Kubernetes node name (auto-filled)      | Yes      | -       |
| `AUTH_TOKEN`           | Bearer token for API authentication     | Yes      | -       |
| `GRACE_PERIOD_SECONDS` | Pod termination grace period in seconds | No       | 10      |

### Kubernetes Secret

Create the auth secret before deploying:

```bash
echo -n "your-secret-token" | base64
# Apply the secret from Charts/secret.yaml (update the base64 value)
kubectl apply -f Charts/secret.yaml
```

## Deployment

### Prerequisites

- Harvester Kubernetes cluster
- `kubectl` configured with cluster access
- Docker or Podman for building the image

### Build & Deploy

```bash
# Build the Docker image
docker build -t your-registry/node-shutdown-api:latest .

# Push to registry
docker push your-registry/node-shutdown-api:latest

# Apply the manifests
kubectl apply -f Charts/secret.yaml
kubectl apply -f Charts/deployment.yaml
```

### Deploy with Helm

All configuration values are centralized in `Charts/values.yaml`. Update this file and run:

```bash
# Customize values in Charts/values.yaml
#   - auth.token          : Authentication bearer token (auto base64 encoded)
#   - image.registry      : Container image registry
#   - image.repository    : Container image name
#   - image.tag           : Container image tag
#   - gracePeriodSeconds  : Pod termination grace period in seconds

helm install node-shutdown ./Charts/

# Or upgrade existing release
helm upgrade node-shutdown ./Charts/

# Dry run to preview rendered manifests
helm install --dry-run --debug node-shutdown ./Charts/
```

### Deploy with kubectl (raw YAML)

For non-Helm deployments, edit `Charts/secret.yaml` manually with base64-encoded token:

```bash
# Generate base64 token
TOKEN=$(echo -n "your-secret-token" | base64)

# Update secret.yaml with the encoded token, then apply:
kubectl apply -f Charts/secret.yaml
kubectl apply -f Charts/deployment.yaml
```

## Security Considerations

- **Authentication:** Uses Bearer token with constant-time comparison (`secrets.compare_digest`) to prevent timing attacks. Consider upgrading to mTLS or OIDC for production.
- **Privileged Container:** The DaemonSet runs in privileged mode to access host system shutdown. Restrict this to trusted nodes only.
- **Network:** Uses `hostNetwork: true` — ensure network policies restrict access to the service.

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

### Shutdown fails

- Check logs: `kubectl logs -n harvester-system -l app=node-shutdown`
- Verify `AUTH_TOKEN` matches the one sent in requests
- Check that `NODE_NAME` is correctly populated

### Port unreachable

- Ensure the container port (8080) matches your Dockerfile `EXPOSE` directive
- Verify the service/port configuration in your Kubernetes manifests
