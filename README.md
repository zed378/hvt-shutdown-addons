# hvt-shutdown-addons

Secure node shutdown service for Harvester clusters. Deploys as a DaemonSet to safely power off Harvester nodes by gracefully terminating VM workloads.

**Repository:** [github.com/zed378/hvt-shutdown-addons](https://github.com/zed378/hvt-shutdown-addons)

## Overview

This service runs as a DaemonSet on each node in a Harvester cluster. It exposes an HTTP endpoint that accepts authenticated shutdown requests, gracefully terminates all running `virt-launcher` pods on the target node, and then powers off the host system.

## Features

- **Security Hardening**: Rate limiting, audit logging, concurrent shutdown protection, constant-time token comparison
- **Harvester Integration**: Deployed as a Harvester Add-on via `harvesterhci.io/v1beta1` CRD
- **Helm Chart**: Centralized configuration via `Charts/values.yaml`
- **Kubevirt Aware**: Gracefully terminates VM workloads before host shutdown with timeout-based fallback
- **Health Checks**: Liveness, readiness, and Kubernetes connectivity probes
- **UI Plugin**: Vue.js frontend extension built into Docker image, served on port 8081

## Security Features

### Authentication

- **Bearer Token Authentication**: All shutdown requests require a strong Bearer token via `AUTH_TOKEN` environment variable
- **Constant-time Comparison**: Uses `secrets.compare_digest()` to prevent timing attacks
- **Empty Token Protection**: Returns 500 if `AUTH_TOKEN` is not configured

### Rate Limiting

- **Sliding Window Algorithm**: Configurable requests per minute (default: 10)
- **Returns HTTP 429**: When rate limit is exceeded

### Concurrent Shutdown Protection

- **Atomic Lock Mechanism**: Uses `threading.Lock` for race-condition-free check-and-set
- **Returns HTTP 409**: When shutdown is already in progress

### Audit Logging

- **All Requests Logged**: Method, path, status code, duration, client IP
- **Configurable Path**: Default `/var/log/shutdown-audit.log`
- **File and Stream Handlers**: Dual output for flexible log collection

### Network Security

- **NetworkPolicy Support**: Optional Kubernetes NetworkPolicy to restrict ingress traffic
- **NodePort Access**: Exposed via NodePort with authentication protecting the API

**VIP (Virtual IP)** is any node's IP address in your Harvester cluster, or a load-balancer virtual IP that distributes traffic across cluster nodes. Using VIP allows you to access the API through any node in the cluster via the NodePort.

API base URL (example):

- `http://VIP:30088` (NodePort — replace `VIP` with any cluster node IP or load-balancer VIP)
- Shutdown endpoint: `POST /system/shutdown`
- Health endpoint: `GET /healthz`

### Container Security

- **Minimal Base Image**: Uses `python:3.11-slim` to reduce attack surface
- **Dropped Capabilities**: All Linux capabilities dropped by default

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/zed378/hvt-shutdown-addons.git
cd hvt-shutdown-addons
```

### 2. Generate Secure Auth Token

```bash
# Generate a strong random token
openssl rand -hex 32
```

### 3. Update Configuration

Edit `Charts/values.yaml` and update:

- `auth.token`: Your generated secure token
- `image.registry` and `image.repository`: Your container registry

### 4. Build & Push Docker Image

```bash
docker build -t your-registry/hvt-shutdown:latest .
docker push your-registry/hvt-shutdown:latest
```

The Docker image includes:

- UI extension built from `hvt-shutdown-ui` (built into image)
- Python FastAPI backend on port 8080
- UI static files served on port 8081

### 5. Package and Publish Helm Chart

Each platform has a dedicated script in the `scripts/` directory:

**Linux/macOS (bash):**

```bash
chmod +x scripts/publish_to_github.sh
./scripts/publish_to_github.sh
```

**Windows (PowerShell):**

```powershell
.\scripts\publish_to_github.ps1
```

### 6. Update Addon Configuration

Edit `Charts/addon.yaml` and update the repo URL:

```yaml
spec:
  repo: "https://zed378.github.io/hvt-shutdown-addons/" # Your chart repository URL
```

### 7. Install as Harvester Add-on

```bash
# Apply the Addon CRD
kubectl apply -f Charts/addon.yaml

# Enable the add-on (toggle enabled: true in addon.yaml, or:)
kubectl patch addon node-shutdown -n harvester-system --type=json -p '[{"op": "replace", "path": "/spec/enabled", "value": true}]'
```

Once enabled, the Add-on's bundled Harvester UI extension is automatically injected into the Harvester Dashboard.

To configure your Authentication Token:

1. Navigate to **Advanced -> Addons** in your Harvester UI.
2. Click **Edit Config** on the `node-shutdown` addon.
3. You will see a custom graphical interface! Enter your secure token into the **Authentication Token** field and click Save.

### 8. Access the API via NodePort

After the DaemonSet is running, you can access the API using the chart's NodePort (default **30088**):

- **Base URL**: `http://VIP:30088` (replace `VIP` with any cluster node IP or load-balancer VIP)
- Replace `VIP` with your Harvester cluster node IP address, e.g., `http://192.168.1.100:30088`

**Health check:**

```bash
curl http://VIP:30088/healthz
```

**Trigger shutdown:**

```bash
curl -X POST http://VIP:30088/system/shutdown \
  -H "Authorization: Bearer your-secret-token"
```

## Architecture

![Architecture Diagram](architecture.svg)

## API Endpoints

### POST /system/shutdown

Initiates a shutdown sequence.

- If called normally (no query param), the service coordinates a **cluster-wide** shutdown by calling peer nodes, then starts the local VM shutdown + host poweroff.
- If called with `?all_nodes=false` (internal/peer calls), it performs **local-only** shutdown.

**Authentication:** Bearer token required in `Authorization` header.

```bash
# NodePort access (recommended)
curl -X POST http://VIP:30088/system/shutdown \
  -H "Authorization: Bearer your-secret-token"

# (Optional) local-only shutdown used for peer coordination
curl -X POST "http://VIP:30088/system/shutdown?all_nodes=false" \
  -H "Authorization: Bearer your-secret-token"
```

**Shutdown Behavior:**

1. Rate limit check (configurable requests per minute)
2. Concurrent shutdown protection (returns 409 if already in progress)
3. List all running pods on the current node
4. Delete `virt-launcher-*` pods with grace period (default: 10s)
5. Wait for VMs to terminate (max: `VM_SHUTDOWN_TIMEOUT` seconds, default: 120s)
6. Attempt host shutdown via fallback chain:
   - `chroot /host systemctl poweroff`
   - `systemctl poweroff`
   - `/sbin/shutdown -h now`

**Response:**

```json
{
  "status": "Shutdown sequence successfully initiated",
  "timestamp": "2026-06-30T04:21:00+00:00"
}
```

**Error Responses:**

| Status Code | Meaning                                         |
| ----------- | ----------------------------------------------- |
| 401         | Invalid or missing authentication token         |
| 409         | Shutdown already in progress                    |
| 429         | Rate limit exceeded                             |
| 500         | Server error (missing config, shutdown failure) |

### GET /healthz

Liveness probe endpoint.

```json
{ "status": "ok", "timestamp": "2026-06-30T04:21:00+00:00" }
```

### GET /healthz/ready

Readiness probe endpoint.

```json
{ "status": "ready", "timestamp": "2026-06-30T04:21:00+00:00" }
```

### GET /healthz/k8s

Kubernetes connectivity health check endpoint. Returns 503 if the Kubernetes client is not initialized or cannot connect.

```json
{
  "status": "ready",
  "k8s": "connected",
  "timestamp": "2026-06-30T04:21:00+00:00"
}
```

## Configuration

All values are in `Charts/values.yaml`:

| Variable                            | Description                               | Default        |
| ----------------------------------- | ----------------------------------------- | -------------- |
| `auth.token`                        | Bearer token for API authentication       | **(required)** |
| `image.registry`                    | Docker image registry                     | zed378         |
| `image.repository`                  | Docker image name                         | hvt-shutdown   |
| `image.tag`                         | Docker image tag                          | latest         |
| `gracePeriodSeconds`                | Pod termination grace period              | 10             |
| `vmShutdownTimeout`                 | Max wait time for VMs before poweroff     | 120            |
| `rateLimiting.maxRequestsPerMinute` | Rate limit threshold                      | 10             |
| `auditLogging.enabled`              | Enable audit logging                      | true           |
| `networkPolicy.enabled`             | Enable Kubernetes NetworkPolicy           | false          |
| `uiPlugin.createUIPluginResource`   | Auto-create UIPlugin after endpoint ready | true           |

## Security Best Practices

1. **Generate Strong Tokens**: Always use `openssl rand -hex 32` or similar for auth tokens
2. **Enable NetworkPolicy**: Set `networkPolicy.enabled: true` to restrict access
3. **Restrict NodePort**: Use firewall rules to limit access to NodePort range
4. **Monitor Audit Logs**: Regularly review `/var/log/shutdown-audit.log`
5. **Rotate Tokens**: Periodically change `AUTH_TOKEN` and update the Kubernetes secret
6. **Use TLS**: Deploy with an ingress controller that provides TLS termination

## Testing

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Scripts

All scripts are located in the `scripts/` directory:

| Script                          | Platform             | Description                                |
| ------------------------------- | -------------------- | ------------------------------------------ |
| `scripts/publish_to_github.sh`  | Linux/macOS (bash)   | Publish Helm chart to GitHub Pages as repo |
| `scripts/publish_to_github.ps1` | Windows (PowerShell) | Publish Helm chart to GitHub Pages as repo |
| `scripts/create_release.sh`     | Linux/macOS (bash)   | Interactive GitHub release creator         |
| `scripts/create_release.ps1`    | Windows (PowerShell) | Windows GitHub release creator             |

## Troubleshooting

```bash
# Check addon status
kubectl get addon -n harvester-system

# Check daemonset pods
kubectl get pods -n harvester-system -l app=node-shutdown

# View pod logs
kubectl logs -n harvester-system -l app=node-shutdown

# Check addon events
kubectl describe addon node-shutdown -n harvester-system

# Test health endpoints
kubectl exec -n harvester-system <pod-name> -- curl http://localhost:8080/healthz
```

## Changelog

### v1.1.0

- **UI Plugin Built into Docker Image**: UI static files are built during Docker build and served internally on port 8081
- **UI Plugin Endpoint**: Uses `http://localhost:8081` via hostNetwork for UI serving
- **UIPlugin Resource Support**: Added UIPlugin custom resource for proper Rancher UI Extensions integration
- **ClusterIP Service for UIPlugin**: Automatic ClusterIP allocation for reliable UIPlugin endpoint routing
- **Cluster-wide Shutdown Coordination**: Added peer-to-peer shutdown via HTTP POST to all DaemonSet pods
- **RBAC Enhancements**: Added nodes, events, namespaces, and pods/status permissions for coordinated shutdown

## License

[MIT License](LICENSE)
