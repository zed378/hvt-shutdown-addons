"""Standalone token console for the node-shutdown service.

A small, self-contained web UI (no Rancher extension framework required) that lets
an operator set / rotate the shutdown auth token. It writes the value into the
``node-shutdown-auth`` Kubernetes Secret; the shutdown DaemonSet reads that Secret
from a mounted file per-request, so the new token takes effect within ~1 minute
WITHOUT restarting any pod.

SECURITY: this endpoint is intentionally UNAUTHENTICATED (operator choice) and can
change the credential that gates cluster-wide shutdown. It MUST be reachable only
from a trusted management network — restrict its NodePort with a host firewall.
Runs as an unprivileged pod with a ServiceAccount scoped to read/write only the
one Secret.
"""
import base64
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from kubernetes import client, config
from pydantic import BaseModel

SECRET_NAME = os.getenv("SECRET_NAME", "node-shutdown-auth")
SECRET_NAMESPACE = os.getenv("SECRET_NAMESPACE", "harvester-system")
SECRET_KEY = os.getenv("SECRET_KEY", "auth-token")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
MIN_TOKEN_LEN = int(os.getenv("MIN_TOKEN_LEN", "32"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("token-console")

k8s_core = None


def _load_k8s():
    global k8s_core
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    k8s_core = client.CoreV1Api()


app = FastAPI(
    title="Node Shutdown Token Console",
    description="Set/rotate the node-shutdown auth token (network-restricted, unauthenticated).",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
def _startup():
    logger.warning(
        "Token console starting. This endpoint is UNAUTHENTICATED and can change the "
        "shutdown credential — restrict its NodePort to a trusted management network."
    )
    try:
        _load_k8s()
        logger.info("Connected to Kubernetes API")
    except Exception as e:  # pragma: no cover - startup connectivity
        logger.error(f"Kubernetes client init failed: {e}")


class TokenPayload(BaseModel):
    token: str


def _now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/healthz")
def healthz():
    return {"status": "ok", "timestamp": _now()}


@app.get("/api/status")
def status_endpoint():
    """Report whether a token is configured, without revealing it."""
    if k8s_core is None:
        raise HTTPException(status_code=503, detail="Kubernetes client not initialized")
    try:
        sec = k8s_core.read_namespaced_secret(SECRET_NAME, SECRET_NAMESPACE)
        raw = (sec.data or {}).get(SECRET_KEY)
        if not raw:
            return {"configured": False, "length": 0, "timestamp": _now()}
        value = base64.b64decode(raw).decode(errors="replace").strip()
        return {"configured": bool(value), "length": len(value), "timestamp": _now()}
    except Exception as e:
        logger.error(f"Failed to read secret: {e}")
        raise HTTPException(status_code=500, detail="Unable to read token secret")


@app.post("/api/token")
def set_token(payload: TokenPayload, request: Request):
    """Write the provided token into the node-shutdown-auth Secret."""
    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token must not be empty")
    if k8s_core is None:
        raise HTTPException(status_code=503, detail="Kubernetes client not initialized")
    try:
        k8s_core.patch_namespaced_secret(
            name=SECRET_NAME,
            namespace=SECRET_NAMESPACE,
            body={"stringData": {SECRET_KEY: token}},
        )
    except Exception as e:
        logger.error(f"Failed to patch secret: {e}")
        raise HTTPException(status_code=500, detail="Failed to update token secret")

    client_ip = request.client.host if request.client else "unknown"
    # Never log the token value itself.
    logger.info(f"Auth token updated (len={len(token)}) by client={client_ip}")
    weak = len(token) < MIN_TOKEN_LEN
    return {
        "status": "updated",
        "weak": weak,
        "message": (
            "Token saved. It takes effect on all nodes within ~1 minute."
            + (" WARNING: this token is short; use at least "
               f"{MIN_TOKEN_LEN} characters (e.g. `openssl rand -hex 32`)." if weak else "")
        ),
        "timestamp": _now(),
    }


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Node Shutdown — Token Console</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #0f172a; color: #e2e8f0; padding: 1.5rem; }
  .card { width: 100%; max-width: 520px; background: #1e293b; border: 1px solid #334155;
          border-radius: 14px; padding: 1.75rem 1.75rem 2rem; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
  h1 { font-size: 1.25rem; margin: 0 0 .25rem; }
  p.sub { margin: 0 0 1.25rem; color: #94a3b8; font-size: .9rem; }
  label { display: block; font-size: .82rem; color: #cbd5e1; margin-bottom: .4rem; }
  .row { display: flex; gap: .5rem; }
  input[type=text] { flex: 1; padding: .7rem .8rem; border-radius: 8px; border: 1px solid #475569;
         background: #0f172a; color: #e2e8f0; font-family: ui-monospace, monospace; font-size: .9rem; }
  button { padding: .7rem 1rem; border-radius: 8px; border: 0; cursor: pointer; font-weight: 600; }
  .gen { background: #334155; color: #e2e8f0; }
  .save { width: 100%; margin-top: 1rem; background: #2563eb; color: #fff; font-size: 1rem; }
  .save:hover { background: #1d4ed8; }
  .hint { font-size: .78rem; color: #94a3b8; margin-top: .5rem; }
  .warn { margin-top: 1.1rem; padding: .7rem .8rem; border-radius: 8px; font-size: .8rem;
          background: #422006; color: #fed7aa; border: 1px solid #7c2d12; }
  #msg { margin-top: 1rem; padding: .7rem .8rem; border-radius: 8px; font-size: .85rem; display: none; }
  #msg.ok { display: block; background: #052e16; color: #bbf7d0; border: 1px solid #166534; }
  #msg.err { display: block; background: #450a0a; color: #fecaca; border: 1px solid #991b1b; }
  #status { font-size: .8rem; color: #94a3b8; margin-top: .25rem; }
</style>
</head>
<body>
  <div class="card">
    <h1>Node Shutdown — Token Console</h1>
    <p class="sub">Set or rotate the authentication token used to trigger cluster shutdown.</p>
    <div id="status">Checking current token…</div>

    <div style="margin-top:1.1rem">
      <label for="tok">Authentication token</label>
      <div class="row">
        <input id="tok" type="text" autocomplete="off" spellcheck="false"
               placeholder="Enter or generate a strong token" />
        <button class="gen" type="button" onclick="gen()">Generate</button>
      </div>
      <div class="hint">Recommended: 64 hex chars (equivalent to <code>openssl rand -hex 32</code>).</div>
    </div>

    <button class="save" type="button" onclick="save()">Save token</button>
    <div id="msg"></div>

    <div class="warn">⚠ This page is unauthenticated and changes the credential that
      gates cluster shutdown. Keep its NodePort restricted to a trusted management network.</div>
  </div>

<script>
async function refreshStatus() {
  try {
    const r = await fetch('api/status'); const j = await r.json();
    document.getElementById('status').textContent = j.configured
      ? `A token is currently set (length ${j.length}).`
      : 'No token is currently set — the service will reject all shutdown requests until you set one.';
  } catch (e) { document.getElementById('status').textContent = 'Could not read current token status.'; }
}
function gen() {
  const a = new Uint8Array(32); crypto.getRandomValues(a);
  document.getElementById('tok').value = Array.from(a, b => b.toString(16).padStart(2,'0')).join('');
}
async function save() {
  const token = document.getElementById('tok').value.trim();
  const msg = document.getElementById('msg');
  if (!token) { msg.className = 'err'; msg.textContent = 'Token must not be empty.'; return; }
  msg.className = ''; msg.style.display = 'none';
  try {
    const r = await fetch('api/token', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    const j = await r.json();
    if (r.ok) { msg.className = 'ok'; msg.textContent = j.message || 'Saved.'; refreshStatus(); }
    else { msg.className = 'err'; msg.textContent = j.detail || 'Failed to save token.'; }
  } catch (e) { msg.className = 'err'; msg.textContent = 'Request failed: ' + e; }
}
refreshStatus();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT)
