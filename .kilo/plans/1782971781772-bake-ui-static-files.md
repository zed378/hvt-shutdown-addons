# Bake UI Extension Files into Docker Image

## Goal
Serve the UI extension static files (JS bundles + package.json) from the DaemonSet's Python FastAPI app on port 8081, replacing the GitHub Pages external serving. Rancher controller (`noCache: false`) will download and cache files from the internal endpoint.

## Files to Touch
1. `app/main.py` — add port 8081 static file server
2. `Dockerfile` — multi-stage build with Node.js, expose port 8081
3. `Charts/values.yaml` — change endpoint to internal URL, set `noCache: false`

## Files NOT to Touch
- `scripts/publish_to_github.sh` — GitHub Pages publishing (tarball-based) continues unchanged
- `Charts/templates/deployment.yaml` — no structural changes needed (same DaemonSet)
- `.gitignore` — no changes needed

## Architecture

```
Dockerfile (multi-stage):
  Stage 1 (node): Build UI extension → dist-pkg/ output
  Stage 2 (python): Python runtime + static UI files
    → app/static/ui-plugin/files.txt
    → app/static/ui-plugin/plugin/package.json
    → app/static/ui-plugin/*.js (3 JS bundles)

Python app (port 8080 = API, port 8081 = static files):
  8080: /healthz, /healthz/ready, /system/shutdown, etc.
  8081: /files.txt, /plugin/package.json, /hvt-shutdown-ui-1.2.0*.umd*.js

Rancher endpoint = http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081
```

## Data Flow

### 1. `files.txt` contents
```
plugin/package.json
hvt-shutdown-ui-1.2.0.umd.min.js
hvt-shutdown-ui-1.2.0.umd.min.202.js
hvt-shutdown-ui-1.2.0.umd.min.362.js
```

### 2. `plugin/package.json`
Copy of the `package.json` from `dist-pkg/`, already modified by `pkgfile.js`:
- `rancher: true`
- `main: "hvt-shutdown-ui-1.2.0.umd.min.js"`
- `files: "**/*"`

### 3. JS bundles (exactly 3 files, ~81 KB total)
| File | Size |
|------|------|
| `hvt-shutdown-ui-1.2.0.umd.min.js` | 5.7 KB |
| `hvt-shutdown-ui-1.2.0.umd.min.202.js` | 71 KB |
| `hvt-shutdown-ui-1.2.0.umd.min.362.js` | 1.9 KB |

**Excluded from serving:** `.map` files (320 KB, debug artifacts), `report.html` (295 KB, webpack analyzer), `version-commit.txt`.

### 4. Static directory structure
```
app/static/ui-plugin/
  ├── files.txt
  ├── plugin/
  │   └── package.json
  ├── hvt-shutdown-ui-1.2.0.umd.min.js
  ├── hvt-shutdown-ui-1.2.0.umd.min.202.js
  └── hvt-shutdown-ui-1.2.0.umd.min.362.js
```

## Implementation Steps

### Step 1: Update Dockerfile with multi-stage build

Replace the current Dockerfile with a multi-stage build that:
1. Uses a Node.js image to build the UI extension (`yarn build-pkg`)
2. Copies the dist-pkg output into the Python image
3. Creates the `app/static/ui-plugin/` structure from the dist-pkg files

```dockerfile
# ---- Stage 1: Build UI extension ----
FROM node:24-alpine AS ui-builder
WORKDIR /build
COPY hvt-shutdown-ui/ .
RUN yarn install --frozen-lockfile
RUN yarn build-pkg hvt-shutdown-ui true

# ---- Stage 2: Python runtime with static files ----
FROM python:3.11-slim
WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API app
COPY app/ ./app

# Copy static UI files from builder stage
COPY --from=ui-builder /build/dist-pkg/hvt-shutdown-ui-*/package.json \
    /app/static/ui-plugin/plugin/package.json
COPY --from=ui-builder /build/dist-pkg/hvt-shutdown-ui-*/hvt-shutdown-ui-*.umd.min.js \
    /app/static/ui-plugin/
COPY --from=ui-builder /build/dist-pkg/hvt-shutdown-ui-*/files.txt \
    /app/static/ui-plugin/ 2>/dev/null || true

# Create files.txt if builder didn't produce one
RUN echo -e "plugin/package.json\nhvt-shutdown-ui-1.2.0.umd.min.js\nhvt-shutdown-ui-1.2.0.umd.min.202.js\nhvt-shutdown-ui-1.2.0.umd.min.362.js" \
    > /app/static/ui-plugin/files.txt

RUN chown -R appuser:appuser /app
EXPOSE 8080
EXPOSE 8081

USER root
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Important details:**
- The `.js` glob matches 3 chunk files but excludes `.map` files (`.map` suffix)
- The `files.txt` is created by a RUN command since `build-pkg` doesn't produce one
- Use version-agnostic COPY patterns where possible (`hvt-shutdown-ui-*/`)
- The Node.js builder stage is `node:24-alpine` (matching `.nvmrc` requirement)

### Step 2: Add static file server to app/main.py

After the existing routes, add a background FastAPI static file server on port 8081:

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

# Background static file server for Rancher UIPlugin endpoint
STATIC_DIR = Path(__file__).parent / "static" / "ui-plugin"
_ui_static_task = None

def _start_static_server():
    """Start a background FastAPI static file server on port 8081."""
    if not STATIC_DIR.exists():
        logger.warning("Static UI plugin directory not found, skipping")
        return
    from fastapi import FastAPI
    static_app = FastAPI()
    static_app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=False), name="ui-plugin")
    config = uvicorn.Config(static_app, host="0.0.0.0", port=8081, log_level="warning")
    server = uvicorn.Server(config)
    _loop = asyncio.new_event_loop()
    _loop.run_until_complete(server.serve())
    _loop.close()
```

Then in the lifespan function, start the static server as a daemon thread after `yield`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global k8s_core
    logger.info("Node Shutdown API starting up...")

    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config()
        except Exception as kube_err:
            logger.warning(f"Kubernetes config not available: {kube_err}")
    k8s_core = client.CoreV1Api()

    global _app_shutdown_event
    _app_shutdown_event = asyncio.Event()

    # Start static file server in background thread
    global _ui_static_task
    _ui_static_task = threading.Thread(target=_start_static_server, daemon=True)
    _ui_static_task.start()

    yield

    logger.info("Node Shutdown API shutting down...")
    try:
        k8s_core.close()
    except Exception:
        pass
```

**Key design decisions for the static server:**
- Uses a **separate FastAPI app instance** (`static_app`) — avoids any route conflicts with the main app
- Mounted at **root path** `"/"` so `/files.txt` maps to `STATIC_DIR/files.txt` and `/plugin/package.json` maps to `STATIC_DIR/plugin/package.json`
- `html=False` prevents auto-serving `index.html` (not needed)
- Daemon thread ensures it doesn't block app shutdown
- Static files only served on port 8081 (API stays on 8080)
- Uses `uvicorn.Server` directly (not `uvicorn.run`) to avoid re-entering the event loop

### Step 3: Update Charts/values.yaml

Change the UIPlugin configuration:

```yaml
# Rancher UIPlugin (installed by this Helm chart)
uiPlugin:
  enabled: true
  # UIPlugin metadata
  name: hvt-shutdown-ui
  namespace: cattle-ui-plugin-system

  # UIPlugin spec
  pluginName: hvt-shutdown-ui
  # Typically matches the UI extension version (kept aligned with chart version by default)
  pluginVersion: "1.2.0"
  # Internal DaemonSet service URL (no more GitHub Pages)
  endpoint: "http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081"

  # Rancher UIPlugin caching behavior
  noCache: false
```

**Changes:**
- `endpoint` changes from GitHub Pages URL to internal DaemonSet service URL
- `noCache` changes from `true` to `false` (Rancher downloads and caches files from endpoint)

The internal service URL format is: `http://<daemonset-name>.<namespace>.svc.cluster.local:<static-port>`

## Rancher Endpoint Expectations (verified)

The Rancher controller code (`fscache.go`) hardcodes the following fetch pattern:
1. `GET {endpoint}/files.txt` → reads newline-separated filenames
2. `GET {endpoint}/plugin/package.json` → version metadata (hardcoded as `PackageJSONFilename`)
3. `GET {endpoint}/{each_entry_from_files.txt}` → downloads each file individually

Files must NOT start with `/`, `..`, or `.` (dot) per `validateFilesTxtEntries()`.

Serving layout:
```
{endpoint}/
  ├─ files.txt          ← GET /files.txt
  ├─ plugin/
  │   └─ package.json   ← GET /plugin/package.json
  ├─ hvt-shutdown-ui-1.2.0.umd.min.js
  ├─ hvt-shutdown-ui-1.2.0.umd.min.202.js
  └─ hvt-shutdown-ui-1.2.0.umd.min.362.js
```

Total served: ~82 KB + ~100 bytes. Well under ConfigMap limits.

## Validation

After applying Helm chart, verify:
1. `kubectl port-forward svc/node-shutdown-webhook 8081:8081 -n harvester-system`
2. Access `http://localhost:8081/files.txt` — should show the 4 file names
3. Access `http://localhost:8081/plugin/package.json` — should show Rancher versioned JSON
4. Access `http://localhost:8081/hvt-shutdown-ui-1.2.0.umd.min.js` — should serve the JS bundle
5. `kubectl get uiplugins hvt-shutdown-ui -n cattle-ui-plugin-system -o yaml` — confirm `status.cacheState` becomes `Cached`

## Notes

1. The multi-stage Docker build eliminates the need for pre-built static files — the Node.js stage runs `yarn build-pkg` during `docker build`
2. No ConfigMap required — files served directly from the container filesystem
3. No structural changes to `Charts/templates/deployment.yaml` needed — same DaemonSet, just adds port 8081 listening
4. The `.map files` and `report.html` are intentionally excluded from the Docker image
5. The `files.txt` is generated by the Docker RUN command, not by `build-pkg` (which doesn't produce one)
6. The endpoint URL in `values.yaml` must have no trailing slash — Rancher concatenates the path directly
