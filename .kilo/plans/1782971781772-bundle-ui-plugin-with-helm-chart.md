# Plan: Bundle UI Plugin Inside Helm Chart with Internal Serving

## Goal

Make the Harvester addon enablement work end-to-end by serving the built UI extension tarball from within the same DaemonSet pod that the addon deploys, so the Rancher UIPlugin controller fetches it internally — no external endpoint dependency.

## Design Decisions (Finalized)

| Decision | Choice |
|----------|--------|
| Serving mechanism | Secondary Python server on port 8081 |
| Serving source | ConfigMap mounted inside pod |
| Endpoint | `http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081` (ClusterIP → DaemonSet pods) |
| noCache | `true` (Rancher fetches directly, no filesystem cache) |
| UIPlugin placement | Inside Helm chart templates (already present, already working) |
| Version embedding | Tarball + files.txt baked into ConfigMap via `Files.Get` at Helm template render time |

## How It Works Today (Baseline)

```
publish_to_github.sh → Build UI extension → Copy to pages/ branch → GitHub Pages serves it
charts/values.yaml   → endpoint: "https://zed378.github.io/hvt-shutdown-addons/ui-plugin/hvt-shutdown-ui-1.2.0"
Charts/templates/uiplugin.yaml → UIPlugin CRD with that external endpoint
```

## Target Architecture

```
User enables addon via Harvester UI → helm install Charts/ → Rancher deploys:
  ├── ConfigMap (hvt-shutdown-ui-static)  ← tarball + files.txt + plugin/package.json
  ├── DaemonSet (node-shutdown-webhook)
  │   └── Container (Python FastAPI)
  │       ├── Port 8080: /system/shutdown API
  │       └── Port 8081: static files from /opt/hvt-shutdown/static
  ├── Service (node-shutdown-webhook-nodeport)  ← port 30088 for API
  └── UIPlugin (catalog.cattle.io/v1)
      └── endpoint: http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081
                               │
                               └── Rancher UIPlugin controller fetches from port 8081
                                   → /files.txt → lists "plugin/package.json" + "*.tar.gz"
                                   → /plugin/package.json → version match
                                   → /hvt-shutdown-ui-1.2.0.tar.gz → downloads & installs in Rancher dashboard

```

## Files to Change

### 1. `Charts/values.yaml` — update endpoint

**Location:** Line 137
**Change:** Replace GitHub Pages URL with internal ClusterIP URL.

```diff
   # Update this endpoint to where your UI assets are hosted
   endpoint: "https://zed378.github.io/hvt-shutdown-addons/ui-plugin/hvt-shutdown-ui-1.2.0"
```

becomes:

```yaml
   # Update this endpoint to where your UI assets are hosted
   endpoint: "http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081"
```

---

### 2. `Charts/templates/configmap-static.yaml` — NEW FILE

**Purpose:** ConfigMap containing the UI extension static files. Baked by helm at render time via `Files.Get`.

```yaml
{{- if .Values.uiPlugin.enabled }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Values.uiPlugin.name }}-static
  namespace: {{ .Values.daemonset.namespace }}
data:
  files.txt: |
    plugin/package.json
    hvt-shutdown-ui-{{ .Values.uiPlugin.pluginVersion }}.tar.gz
  plugin/package.json: |
    {{- .Files.Get "ui-plugin-server/plugin/package.json" | nindent 4 }}
binaryData:
  hvt-shutdown-ui-{{ .Values.uiPlugin.pluginVersion }}.tar.gz: |
    {{- .Files.Get "ui-plugin-server/hvt-shutdown-ui-{{ .Values.uiPlugin.pluginVersion }}.tar.gz" | b64enc | nindent 4 }}
{{- end }}
```

**Note on size:** ConfigMaps capped at 1MB. Since this is a single-component Vue extension (no node_modules in tarball), the built tarball is expected to be ~200-800KB. If > 1MB, switch to Secret.

---

### 3. `Charts/templates/deployment.yaml` — add port + ConfigMap mount

**Location:** Around lines 68-119 (inside `containers[0]` block)

**Add to `ports:`** (after existing port for 8080):
```yaml
          ports:
            - name: api
              containerPort: 8080
            - name: static
              containerPort: 8081
```

**Add to `volumeMounts:`** (after existing mounts):
```yaml
            - name: static-files
              mountPath: /opt/hvt-shutdown/static
              readOnly: true
```

**Add to `volumes:`** (after existing volumes):
```yaml
        - name: static-files
          configMap:
            name: {{ .Values.uiPlugin.name }}-static
```

These additions go before the closing `volumes:` section (after line 126 `emptyDir`).

---

### 4. `app/main.py` — add static file server on port 8081

**Location:** After imports (around line 19), add:

```python
from starlette.staticfiles import StaticFiles
from starlette.applications import Starlette

STATIC_MOUNT_DIR = "/opt/hvt-shutdown/static"
```

**After the `app = FastAPI(...)` block** (after line 160), add:

```python
# Static file server for UIPlugin endpoint
static_app = None
if os.path.isdir(STATIC_MOUNT_DIR):
    static_app = Starlette()
    static_app.mount("/", StaticFiles(directory=STATIC_MOUNT_DIR, follow_symlink=False), name="static")
```

**Replace the final block** (lines 523-524):

```python
if __name__ == "__main__":
    import uvicorn

    if static_app:
        thread = threading.Thread(
            target=lambda: uvicorn.run(
                static_app, host="0.0.0.0", port=8081, log_level="warning"
            ),
            daemon=True,
        )
        thread.start()
        time.sleep(1)

    uvicorn.run(app, host="0.0.0.0", port=8080)
```

---

### 5. `ui-plugin-server/plugin/package.json` — NEW FILE

**Purpose:** Source of truth for `plugin/package.json` inside the ConfigMap tarball. Copied/adapted from `hvt-shutdown-ui/pkg/hvt-shutdown-ui/package.json`.

Content — copy `hvt-shutdown-ui/pkg/hvt-shutdown-ui/package.json` exactly as-is:

```json
{
  "name": "hvt-shutdown-ui",
  "description": "hvt-shutdown-ui plugin",
  "version": "1.2.0",
  "private": false,
  "rancher": {
    "annotations": {
      "catalog.cattle.io/rancher-version": ">= 2.10.0",
      "catalog.cattle.io/ui-extensions-version": ">= 3.0.0 < 4.0.0"
    }
  },
  "scripts": {},
  "engines": {
    "node": ">=24"
  },
  "devDependencies": {
    "@vue/cli-plugin-babel": "5.0.9",
    "@vue/cli-service": "5.0.8",
    "@vue/cli-plugin-typescript": "5.0.9"
  },
  "browserslist": [
    "> 1%",
    "last 2 prefs",
    "not dead"
  ]
}
```

**Important:** The version in this file must stay in sync with the chart version. The publish scripts will update it before packaging.

---

### 6. `ui-plugin-server/files.txt` — NEW FILE (placeholder)

**Purpose:** Manifest listing files inside the tarball. Updated by publish scripts.

```
plugin/package.json
hvt-shutdown-ui-1.2.0.tar.gz
```

---

### 7. `scripts/publish_to_github.sh` — add tarball creation step

**Location:** After line 103 (`cd "$PROJECT_ROOT"`), BEFORE line 105 tarball check.

Add these steps between the UI build step and the tarball check:

```bash
# Build the UI tarball for ConfigMap embedding
echo "=== Building UI tarball for ConfigMap ==="
BUILD_DIR=$(mktemp -d)
mkdir -p "$BUILD_DIR/plugin"
cp hvt-shutdown-ui/pkg/hvt-shutdown-ui/package.json "$BUILD_DIR/plugin/package.json"
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"${CHART_VERSION}\"/" "$BUILD_DIR/plugin/package.json"
tar czf "ui-plugin-server/hvt-shutdown-ui-${CHART_VERSION}.tar.gz" -C "$BUILD_DIR" .
rm -rf "$BUILD_DIR"
echo "files.txt: plugin/package.json" > "ui-plugin-server/files.txt"
echo "hvt-shutdown-ui-${CHART_VERSION}.tar.gz" >> "ui-plugin-server/files.txt"
echo "UI tarball built at ui-plugin-server/hvt-shutdown-ui-${CHART_VERSION}.tar.gz"
```

---

### 8. `scripts/publish_chart.sh` / `scripts/publish_chart.ps1` — add tarball step

Same logic as publish_to_github.sh — add the tarball build step after Helm packaging, before repo publishing.

---

### 9. `.gitignore` — update

Append:
```
# UI plugin server static files
ui-plugin-server/hvt-shutdown-ui-*.tar.gz
ui-plugin-server/node_modules/
plugin/package.json
```

---

### 10. `node-shutdown-1.1.0.tgz` — DELETE

Remove the leftover tarball from root.

---

### 11. `Charts/uiplugin.yaml` — DELETE

This is now a deprecated placeholder (2 lines of comment). Delete it since the actual UIPlugin lives in `Charts/templates/uiplugin.yaml`.

## Data Flow (End-to-End)

```
1. Developer runs: ./scripts/publish_to_github.sh
   ├─ yarn build-pkg → hvt-shutdown-ui/dist-pkg/hvt-shutdown-ui-1.2.0/
   ├─ Creates ui-plugin-server/hvt-shutdown-ui-1.2.0.tar.gz
   ├─ Sets version in ui-plugin-server/plugin/package.json
   ├─ Updates ui-plugin-server/files.txt
   ├─ Helm packages chart (ConfigMap now has tarball embedded)
   └─ Pushes *.tgz, index.yaml, and UI static files to pages/ branch

2. User runs: helm install ... Charts/
   ├─ Kubernetes creates ConfigMap with tarball
   ├─ DaemonSet deploys, mounts ConfigMap at /opt/hvt-shutdown/static
   └─ Python app starts: port 8080 (API) + port 8081 (static)

3. Rancher UIPlugin controller (running in cluster)
   ├─ Gets UIPlugin CRD: endpoint=http://node-shutdown-webhook.harvester-system.svc.cluster.local:8081
   ├─ Curls /files.txt → gets "plugin/package.json" and "hvt-shutdown-ui-1.2.0.tar.gz"
   ├─ Curls /plugin/package.json → validates version
   ├─ Curls /hvt-shutdown-ui-1.2.0.tar.gz → downloads extension
   └─ Extension caches locally → loads in Rancher dashboard
```

## Validation Steps

1. **Template render:** `helm template Charts/ -f Charts/values.yaml` — verify 5 resources (ServiceAccount, ClusterRole, ClusterRoleBinding, DaemonSet, Service, UIPlugin, ConfigMap)
2. **ConfigMap content:** Check ConfigMap has the correct tarball size (should be < 1MB)
3. **Port 8081 availability:** After deploy, `kubectl exec -it <pod> -- curl -s http://localhost:8081/files.txt`
4. **Files served:** `kubectl exec -it <pod> -- curl -s http://localhost:8081/plugin/package.json` → valid JSON
5. **Tarball served:** `kubectl exec -it <pod> -- curl -s http://localhost:8081/hvt-shutdown-ui-1.2.0.tar.gz | file -` → should say gzip
6. **UIPlugin Ready:** `kubectl get uiplugin -n cattle-ui-plugin-system` → Ready=true
7. **Dashboard extension loads:** Open Harvester UI → verify shutdown config tab appears

## Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| ConfigMap exceeds 1MB | Medium | Build tarball is likely < 500KB since it contains only compiled Vue assets (no node_modules). Verify during build. If > 1MB, switch to K8s Secret. |
| DaemonSet hostNetwork + ClusterIP routing | Low | ClusterIP service routes to any DaemonSet pod. Rancher controller fetches from whichever pod responds — all pods serve identical files. |
| Port 8081 conflict with host services | Low | Port 8081 on pod is different from host port. Only accessible within cluster via ClusterIP. |
| Version drift between tarball and values.yaml | Medium | Publish script sets version in both ui-plugin-server/plugin/package.json AND values.yaml simultaneously. |
