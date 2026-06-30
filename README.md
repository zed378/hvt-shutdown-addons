# hvt-shutdown-addons

Secure node shutdown service for Harvester clusters. Deploys as a DaemonSet to safely power off Harvester nodes by gracefully terminating VM workloads.

**Repository:** [github.com/zed378/hvt-shutdown-addons](https://github.com/zed378/hvt-shutdown-addons)

## Overview

This service runs as a DaemonSet on each node in a Harvester cluster. It exposes an HTTP endpoint that accepts authenticated shutdown requests, gracefully terminates all running `virt-launcher` pods on the target node, and then powers off the host system.

## Features

- **Security Hardening**: Rate limiting, audit logging, concurrent shutdown protection
- **Harvester Integration**: Deployed as a Harvester Add-on via `harvesterhci.io/v1beta1` CRD
- **Helm Chart**: Centralized configuration via `Charts/values.yaml`
- **Kubevirt Aware**: Gracefully terminates VM workloads before host shutdown

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

### 3. Package and Publish Helm Chart

```bash
# Make script executable
chmod +x publish_chart.sh

# Package the chart (creates charts-output/ directory)
./publish_chart.sh

# Edit charts-output/index.yaml and replace the placeholder URL with your actual serving URL
```

### 4. Update Addon Configuration

Edit `Charts/addon.yaml` and update the repo URL:

```yaml
spec:
  repo: "https://your-registry.example.com/charts" # Your chart repository URL
```

### 5. Install as Harvester Add-on

```bash
# Apply the Addon CRD
kubectl apply -f Charts/addon.yaml

# Enable the add-on (toggle enabled: true in addon.yaml, or:)
kubectl patch addon node-shutdown -n harvester-local-storage --type=json -p '[{"op": "replace", "path": "/spec/enabled", "value": true}]'
```

## Architecture

```mermaid
graph TB
    subgraph HarvesterCluster["Harvester Cluster"]
        subgraph Namespace["harvester-system Namespace"]
            subgraph DaemonSet["DaemonSet: node-shutdown-webhook"]
                subgraph Container["FastAPI Container :8080"]
                    API["API Endpoints<br/>POST /system/shutdown<br/>GET /healthz<br/>GET /healthz/ready"]
                    Client["K8s API Client<br/>- List pods<br/>- Delete virt-launchers"]
                end
            end
            AddonCRD["Addon CRD<br/>harvesterhci.io/v1beta1<br/>enabled/disabled toggle"]
        end
        subgraph Nodes["Physical Nodes"]
            NodeA["Node A<br/>virt-launcher-vm1<br/>virt-launcher-vm2"]
            NodeB["Node B<br/>virt-launcher-vm3"]
        end
    end

    API --> Client
    AddonCRD -. Manages .-> DaemonSet
    DaemonSet --> NodeA
    DaemonSet --> NodeB
    NodeA -. Shutdown → delete VMs → poweroff .-> NodeA
    NodeB -. Shutdown → delete VMs → poweroff .-> NodeB
```

## API Endpoints

### POST /system/shutdown

Initiates a graceful shutdown sequence on the node where the service is running.

**Authentication:** Bearer token required in `Authorization` header.

```bash
curl -X POST http://localhost:8080/system/shutdown \
  -H "Authorization: Bearer your-secret-token-string"
```

**Shutdown Behavior:**

1. Rate limit check (configurable requests per minute)
2. Concurrent shutdown protection
3. List all running pods on the current node
4. Delete `virt-launcher-*` pods with grace period (default: 10s)
5. Attempt host shutdown via fallback chain:
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

## Security Features

- **Rate Limiting**: Configurable requests per minute (default: 10)
- **Audit Logging**: All requests logged with timestamp and metadata
- **Concurrent Shutdown Protection**: Prevents race conditions
- **Constant-time Token Comparison**: Prevents timing attacks
- **Pod Security**: hostIPC/hostPID disabled, privilege escalation blocked

## Configuration

All values are in `Charts/values.yaml` and `Charts/addon.yaml`:

| Variable                            | Description                         | Default           |
| ----------------------------------- | ----------------------------------- | ----------------- |
| `auth.token`                        | Bearer token for API authentication | (change me)       |
| `image.registry`                    | Docker image registry               | your-registry     |
| `image.repository`                  | Docker image name                   | node-shutdown-api |
| `image.tag`                         | Docker image tag                    | latest            |
| `gracePeriodSeconds`                | Pod termination grace period        | 10                |
| `rateLimiting.maxRequestsPerMinute` | Rate limit threshold                | 10                |
| `auditLogging.enabled`              | Enable audit logging                | true              |

## Testing

```bash
pip install -r requirements.txt
pytest tests/
```

## Troubleshooting

```bash
# Check addon status
kubectl get addon -n harvester-local-storage

# Check daemonset pods
kubectl get pods -n harvester-system -l app=node-shutdown

# View pod logs
kubectl logs -n harvester-system -l app=node-shutdown

# Check addon events
kubectl describe addon node-shutdown -n harvester-local-storage
```
