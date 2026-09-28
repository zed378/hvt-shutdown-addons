import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from kubernetes import client, config

from app import bmc

# Grace period in seconds for pod termination (0 = immediate)
GRACE_PERIOD_SECONDS = int(os.getenv("GRACE_PERIOD_SECONDS", "10"))

# Max time to wait for VMs to shut down before proceeding (seconds)
VM_SHUTDOWN_TIMEOUT = int(os.getenv("VM_SHUTDOWN_TIMEOUT", "120"))

# Audit logging configuration
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "/var/log/shutdown-audit.log")
AUDIT_ENABLED = os.getenv("AUDIT_ENABLED", "true").lower() == "true"

# Ensure audit log directory exists
_audit_log_dir = os.path.dirname(AUDIT_LOG_PATH)
if _audit_log_dir and not os.path.exists(_audit_log_dir):
    try:
        os.makedirs(_audit_log_dir, exist_ok=True)
    except OSError:
        AUDIT_LOG_PATH = os.path.join(".", os.path.basename(AUDIT_LOG_PATH))

# Rate limiting configuration
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "10"))
RATE_LIMIT_WINDOW = 60  # seconds

# Expose interactive API docs (/docs, /openapi.json) only when explicitly enabled.
# Disabled by default to reduce information disclosure on a security-sensitive service.
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "false").lower() == "true"

# Concurrent shutdown protection - use Lock for atomic check-and-set
_shutdown_lock = Lock()
_shutdown_in_progress = False

# Concurrent power-on protection - use Lock for atomic check-and-set
_poweron_lock = Lock()
_poweron_in_progress = False
_poweron_status = {
    "state": "idle",
    "detail": "No power-on operation has been executed",
    "timestamp": None,
}

# IPMI default credentials from environment or mounted Secret file
IPMI_DEFAULT_USER = os.getenv("IPMI_DEFAULT_USER", "admin")
IPMI_DEFAULT_PASSWORD = os.getenv("IPMI_DEFAULT_PASSWORD", "")
IPMI_PASSWORD_FILE = os.getenv("IPMI_PASSWORD_FILE")

# Per-node BMC (IPMI / Redfish) config rendered by the chart into a Secret and
# mounted here. Shape: {"defaults": {...}, "nodes": {"<node>": {"protocol",
# "host", "port", "user", "password", "verifyTls"}}}. Re-read on every use so
# edits from the dashboard apply without restarting the DaemonSet.
BMC_CONFIG_FILE = os.getenv("BMC_CONFIG_FILE", "/etc/bmc/bmc.json")

# How VirtualMachines are started/stopped: "kubectl" runs `kubectl patch` inside
# the pod (falls back to the Python client when the binary is missing); "api"
# always uses the Python Kubernetes client. Both make the same API call.
VM_CONTROL = os.getenv("VM_CONTROL", "kubectl").lower()
KUBECTL_BIN = os.getenv("KUBECTL_BIN", "kubectl")

# Annotation remembering a VM's runStrategy before we halted it, so a scheduled
# power-on restores e.g. RerunOnFailure instead of forcing Always.
PREV_RUN_STRATEGY_ANNOTATION = "node-shutdown.harvesterhci.io/previous-run-strategy"

# Kubernetes API client (initialized lazily)
k8s_core = None

# Node name, auth token, and NodePort from environment
NODE_NAME = os.getenv("NODE_NAME")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
NODE_PORT = int(os.getenv("NODE_PORT", "30088"))

# Optional path to a file containing the auth token (a mounted Secret). When set,
# the token is read from this file on each request so that rotating the token in
# the Secret (e.g. via the token console) takes effect WITHOUT restarting the
# pod — the kubelet syncs the projected Secret within ~1 minute. Falls back to the
# AUTH_TOKEN env var when the file is absent/empty (backward compatible).
AUTH_TOKEN_FILE = os.getenv("AUTH_TOKEN_FILE")
_token_cache = {"value": None, "mtime": None}


def _current_token():
    """Return the active auth token, preferring the live Secret file."""
    if AUTH_TOKEN_FILE:
        try:
            mtime = os.stat(AUTH_TOKEN_FILE).st_mtime
            if _token_cache["mtime"] != mtime:
                with open(AUTH_TOKEN_FILE, "r") as f:
                    _token_cache["value"] = f.read().strip()
                _token_cache["mtime"] = mtime
            if _token_cache["value"]:
                return _token_cache["value"]
        except OSError:
            # File missing/unreadable — fall through to the env var.
            pass
    return AUTH_TOKEN

# Port this service listens on inside the pod.
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))

# --- TLS configuration ---
# When TLS_ENABLED=true and a cert/key are present, the service serves HTTPS and
# all peer-to-peer coordination calls are made over HTTPS as well. Certs are
# provisioned by the Helm chart (self-signed by default, or bring-your-own).
TLS_ENABLED = os.getenv("TLS_ENABLED", "false").lower() == "true"
TLS_CERT_PATH = os.getenv("TLS_CERT_PATH", "/etc/tls/tls.crt")
TLS_KEY_PATH = os.getenv("TLS_KEY_PATH", "/etc/tls/tls.key")
# Optional CA bundle used to verify peers. If PEER_TLS_VERIFY=false (default),
# peer certificates are NOT verified — acceptable for intra-cluster, self-signed
# certs because every call is still gated by the shared bearer token.
PEER_TLS_VERIFY = os.getenv("PEER_TLS_VERIFY", "false").lower() == "true"
PEER_CA_PATH = os.getenv("PEER_CA_PATH", TLS_CERT_PATH)

# Scheme used when this node talks to peer nodes.
PEER_SCHEME = "https" if TLS_ENABLED else "http"


def _peer_ssl_context():
    """Build an SSL context for outbound peer calls, or None for plain HTTP."""
    if PEER_SCHEME != "https":
        return None
    import ssl
    if PEER_TLS_VERIFY:
        try:
            return ssl.create_default_context(cafile=PEER_CA_PATH)
        except Exception:
            return ssl.create_default_context()
    # Intra-cluster self-signed certs: skip verification (token still enforced).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# Configure logging
_log_handlers = [logging.StreamHandler()]
if AUDIT_ENABLED:
    try:
        _log_handlers.append(logging.FileHandler(AUDIT_LOG_PATH))
    except (OSError, IOError):
        print(f"WARNING: Cannot create audit log file at {AUDIT_LOG_PATH}, using stream handler only")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("node-shutdown")

# Graceful shutdown handler
_app_shutdown_event = None


def handle_signal(signum, frame):
    """Handle termination signals gracefully."""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    if _app_shutdown_event:
        _app_shutdown_event.set()


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# Rate limiter
class RateLimiter:
    """Thread-safe per-client sliding-window rate limiter.

    Requests are tracked per key (typically the client IP) so that traffic
    from one source cannot exhaust the shutdown budget for every other
    source. Rate limiting is intentionally applied *after* authentication
    (see the shutdown endpoint) so that unauthenticated or failed requests
    can never throttle a legitimate, emergency UPS-triggered shutdown.
    """
    def __init__(self, max_requests: int, window: int):
        self.max_requests = max_requests
        self.window = window
        self.buckets: dict[str, list[float]] = {}
        self.lock = Lock()

    def is_allowed(self, key: str = "global") -> bool:
        """Check if a request from ``key`` is allowed under the rate limit."""
        with self.lock:
            now = time.time()
            # Drop timestamps outside the current window for this key
            recent = [r for r in self.buckets.get(key, []) if now - r < self.window]
            if len(recent) >= self.max_requests:
                self.buckets[key] = recent
                return False
            recent.append(now)
            self.buckets[key] = recent
            # Opportunistically evict fully-expired buckets to bound memory
            if len(self.buckets) > 1024:
                self.buckets = {
                    k: v for k, v in self.buckets.items()
                    if v and now - v[-1] < self.window
                }
            return True


rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE, RATE_LIMIT_WINDOW)


def _client_ip(request: Request) -> str:
    """Best-effort client IP, honoring common reverse-proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _normalize_vm_refs(refs) -> list[str]:
    """Normalize VM refs to sorted, deduplicated ``namespace/name`` strings.

    Accepts ``ns/name`` or a bare ``name`` (assumed ``default/name``).
    Malformed entries are dropped — never widened — so a bad entry can only
    shrink the affected set, never expand it.
    """
    out = set()
    for ref in refs or []:
        if not isinstance(ref, str):
            continue
        ref = ref.strip().strip("/")
        if not ref:
            continue
        if "/" in ref:
            ns, _, name = ref.partition("/")
            ns, name = ns.strip(), name.strip()
            if not ns or not name or "/" in name:
                continue
            out.add(f"{ns}/{name}")
        else:
            out.add(f"default/{ref}")
    return sorted(out)


# auto_error=False so a *missing* Authorization header is handled by
# verify_token and returns a consistent 401 (rather than Starlette's 403).
security = HTTPBearer(auto_error=False)


# Alternative token header. Calls from the dashboard go through the Kubernetes
# API service proxy, which consumes (strips) the Authorization header, so the UI
# sends the token in this header instead.
TOKEN_HEADER = "x-node-shutdown-token"


def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify authentication token with constant-time comparison."""
    active_token = _current_token()
    if not active_token:
        logger.error("No auth token configured (AUTH_TOKEN / AUTH_TOKEN_FILE)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service misconfigured",
        )
    # Never log or echo credential material. Compare in constant time.
    presented = credentials.credentials if credentials else (request.headers.get(TOKEN_HEADER) or "")
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input.
    if not secrets.compare_digest(presented.encode(), active_token.encode()):
        logger.warning("Failed authentication attempt (invalid or missing token)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )
    return presented


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    global k8s_core
    logger.info("Node Shutdown API starting up...")

    # Initialize Kubernetes connection
    try:
        config.load_incluster_config()
        logger.info("Connected to Kubernetes cluster using in-cluster config")
    except config.ConfigException:
        try:
            config.load_kube_config()
            logger.info("Connected to Kubernetes cluster using kube config")
        except Exception as kube_err:
            logger.warning(f"Kubernetes config not available: {kube_err}")
            k8s_core = None
            yield
            return

    try:
        k8s_core = client.CoreV1Api()
    except Exception as e:
        logger.warning(f"Failed to create Kubernetes client: {e}, continuing without it")
        k8s_core = None

    global _app_shutdown_event
    _app_shutdown_event = asyncio.Event()
    yield
    logger.info("Node Shutdown API shutting down...")
    # Clean up resources here
    try:
        k8s_core.close()
    except Exception:
        pass


app = FastAPI(
    title="Node Shutdown API",
    description="Secure node shutdown service for Harvester clusters",
    version="1.2.0",
    lifespan=lifespan,
    # Interactive docs and the OpenAPI schema are disabled unless ENABLE_DOCS=true,
    # to avoid disclosing the API surface of a privileged shutdown service.
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# Tracks fire-and-forget background coordination tasks so they are not
# garbage-collected before completion.
_background_tasks: set = set()


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Custom exception handler for better error responses."""
    logger.warning(f"HTTP error {exc.status_code} for request to {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "timestamp": datetime.now(timezone.utc).isoformat()},
    )


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """Audit logging middleware."""
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    # Determine actual client IP (checking proxy headers first)
    forwarded = request.headers.get("x-forwarded-for")
    real_ip = request.headers.get("x-real-ip")
    client_host = request.client.host if request.client else "unknown"
    client_ip = forwarded.split(",")[0].strip() if forwarded else (real_ip.strip() if real_ip else client_host)

    logger.info(
        f"Audit: method={request.method} path={request.url.path} "
        f"status={response.status_code} duration={duration:.3f}s "
        f"client={client_ip} proxy={client_host}"
    )
    return response


# NOTE: Rate limiting is intentionally NOT implemented as pre-auth middleware.
# It is enforced inside the shutdown endpoint *after* token verification and is
# keyed per client IP, so unauthenticated or failed requests can never consume
# the budget and lock out a legitimate emergency shutdown.


@app.get("/healthz")
async def health_check():
    """Liveness probe endpoint."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/healthz/ready")
async def readiness_check():
    """Readiness probe endpoint."""
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/healthz/k8s")
async def k8s_readiness_check():
    """Kubernetes connectivity health check endpoint."""
    global k8s_core
    if k8s_core is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "detail": "Kubernetes client not initialized"},
        )
    try:
        # Simple API call to verify connectivity (using least-privilege namespaced pod list)
        namespace = "harvester-system"
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
                namespace = f.read().strip()
        except Exception:
            pass
        if k8s_core is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": "Kubernetes client not initialized"},
            )
        k8s_core.list_namespaced_pod(namespace=namespace, limit=1)
        return {"status": "ready", "k8s": "connected", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "detail": f"Kubernetes connection failed: {str(e)}"},
        )


@app.post("/system/shutdown", dependencies=[Depends(verify_token)])
async def execute_shutdown(request: Request, all_nodes: str = None):
    """Execute graceful shutdown — returns immediately, runs in background.

    Default behavior (no query param): cluster-wide shutdown.
    Query param ?all_nodes=false: local-only shutdown (used for internal peer calls).

    When cluster-wide shutdown is triggered:
    1. This node calls shutdown on all peer nodes via HTTP
    2. Each node deletes its own VM workloads gracefully
    3. Each node waits for peer nodes to go offline
    4. Each node powers off its own host
    This ensures a coordinated, orderly shutdown of the entire cluster.
    """
    global _shutdown_in_progress

    # --- Validation (synchronous, must complete before returning) ---

    # Rate limiting runs here — AFTER verify_token has already succeeded — and is
    # keyed per client IP. This guarantees failed/unauthenticated traffic cannot
    # throttle a real emergency shutdown.
    if not rate_limiter.is_allowed(_client_ip(request)):
        logger.warning("Rate limit exceeded for authenticated shutdown request")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )

    if not NODE_NAME:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="NODE_NAME environment variable is missing",
        )

    # Atomic check-and-set to prevent concurrent shutdown attempts
    with _shutdown_lock:
        if _shutdown_in_progress:
            logger.warning("Shutdown already in progress, ignoring request")
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": "Shutdown already in progress"},
            )
        _shutdown_in_progress = True

    # Optional JSON body:
    #   {"nodes": [...], "vmStrategy": "stop|migrate|force", "vms": [...], "poweroff": true}
    # nodes empty/absent  => whole cluster (current behaviour).
    # nodes given          => only those nodes are shut down (selected-node shutdown).
    # vmStrategy (default "force"): how each shutting-down node handles its VMs.
    # vms (default []): restrict the VM phase to these "namespace/name" refs.
    # Empty/absent => all VMs on each shutting-down node (current behaviour).
    # poweroff (default True): when False, run the VM phase only and skip the
    # host poweroff (used by VM-only schedules).
    req_body = {}
    try:
        raw = await request.body()
        if raw:
            req_body = json.loads(raw)
    except Exception:
        req_body = {}
    target_nodes = req_body.get("nodes") or []
    vm_strategy = str(req_body.get("vmStrategy") or "force").lower()
    if vm_strategy not in ("stop", "migrate", "force"):
        vm_strategy = "force"
    target_vms = _normalize_vm_refs(req_body.get("vms"))
    poweroff_host = bool(req_body.get("poweroff", True))

    # Determine if this is an internal peer call (all_nodes=false) or user request
    is_peer_call = all_nodes == "false"

    if is_peer_call:
        # Internal call from another node: only shut down locally, with the strategy.
        logger.info(f"Internal shutdown call from peer — local shutdown only (vmStrategy={vm_strategy}, vms={target_vms or 'ALL'}, poweroff={poweroff_host})")
        thread = threading.Thread(
            target=run_shutdown_sequence,
            args=(None, vm_strategy, target_vms, poweroff_host),
            daemon=True,
            name="shutdown-daemon",
        )
        thread.start()
    else:
        # User request: cluster-wide, or selected nodes when target_nodes is given.
        scope = "cluster-wide" if not target_nodes else f"nodes={target_nodes}"
        logger.info(f"Shutdown requested ({scope}, vmStrategy={vm_strategy}, vms={target_vms or 'ALL'}, poweroff={poweroff_host})")
        task = asyncio.create_task(coordinate_cluster_shutdown(target_nodes, vm_strategy, target_vms, poweroff_host))
        # Retain a reference so the task isn't garbage-collected mid-flight.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        "status": "Shutdown sequence initiated",
        "scope": "local" if is_peer_call else ("cluster" if not target_nodes else "selected"),
        "vmStrategy": vm_strategy,
        "poweroff": poweroff_host,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def _current_ipmi_password() -> str:
    """Return the active IPMI password, preferring the live Secret file if mounted."""
    if IPMI_PASSWORD_FILE:
        try:
            with open(IPMI_PASSWORD_FILE, "r") as f:
                val = f.read().strip()
                if val:
                    return val
        except OSError:
            pass
    return IPMI_DEFAULT_PASSWORD


def _non_empty(d) -> dict:
    """Drop None/"" values so a blank form field never overrides a stored one.

    Also folds the legacy ``bmcIp`` / ``ip`` keys into ``host`` so entries using
    different spellings merge predictably.
    """
    out = {k: v for k, v in (d or {}).items() if v not in (None, "")}
    for alias in ("bmcIp", "ip"):
        val = out.pop(alias, None)
        if val and "host" not in out:
            out["host"] = val
    return out


def _load_bmc_config() -> dict:
    """Read the mounted BMC config Secret, layered over the env defaults."""
    defaults = {"protocol": "ipmi", "user": IPMI_DEFAULT_USER, "password": _current_ipmi_password()}
    nodes = {}
    try:
        with open(BMC_CONFIG_FILE, "r") as f:
            data = json.load(f) or {}
        defaults.update(_non_empty(data.get("defaults")))
        nodes = data.get("nodes") or {}
    except (OSError, ValueError):
        pass
    return {"defaults": defaults, "nodes": nodes}


def _bmc_for_node(node: str, override: dict = None, default_override: dict = None) -> dict:
    """Resolve the effective, validated BMC config for a node.

    Precedence: request override > stored per-node entry > request defaults >
    stored defaults. Raises bmc.BmcError when unusable.
    """
    stored = _load_bmc_config()
    entry = {**_non_empty(stored["nodes"].get(node)), **_non_empty(override)}
    defaults = {**stored["defaults"], **_non_empty(default_override)}
    return bmc.normalize_config(entry, defaults)


def _wait_for_node_ready(node_name: str, timeout_seconds: int = 300) -> bool:
    """Wait until a Kubernetes node transitions to Ready status."""
    global k8s_core
    if not k8s_core:
        logger.warning(f"Kubernetes client not available; cannot poll node {node_name} readiness")
        return False
    logger.info(f"Waiting for node '{node_name}' to become Ready in Kubernetes (timeout {timeout_seconds}s)...")
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            node = k8s_core.read_node_status(name=node_name)
            for condition in (node.status.conditions or []):
                if condition.type == "Ready" and condition.status == "True":
                    logger.info(f"Node '{node_name}' is Ready in Kubernetes!")
                    return True
        except Exception as e:
            logger.debug(f"Error reading node {node_name} status: {e}")
        time.sleep(10)
    logger.warning(f"Timed out waiting for node '{node_name}' to become Ready")
    return False


# ---------------------------------------------------------------------------
# VirtualMachine start/stop — `kubectl patch virtualmachine` (or the Python
# client when kubectl is unavailable / VM_CONTROL=api).
# ---------------------------------------------------------------------------

class VmNotFound(Exception):
    """The VirtualMachine object does not exist (e.g. a standalone VMI)."""
    status = 404


def _use_kubectl() -> bool:
    return VM_CONTROL == "kubectl" and shutil.which(KUBECTL_BIN) is not None


def _kubectl(args: list[str], timeout: int = 30, stdin: str = None) -> str:
    """Run kubectl with the pod's in-cluster ServiceAccount credentials."""
    res = subprocess.run([KUBECTL_BIN] + args, capture_output=True, text=True,
                         timeout=timeout, input=stdin)
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        if "NotFound" in err or "not found" in err:
            raise VmNotFound(err)
        raise RuntimeError(f"kubectl {' '.join(args[:2])} failed: {err}")
    return res.stdout


def _get_vm(ns: str, name: str) -> dict:
    if _use_kubectl():
        return json.loads(_kubectl(["get", "virtualmachines.kubevirt.io", name, "-n", ns, "-o", "json"]))
    return client.CustomObjectsApi().get_namespaced_custom_object(
        "kubevirt.io", "v1", ns, "virtualmachines", name)


def _patch_vm(ns: str, name: str, patch: dict):
    # Merge patch (an OBJECT): only the keys present are touched, so we never add
    # `running` to a runStrategy VM (or vice versa).
    if _use_kubectl():
        _kubectl(["patch", "virtualmachines.kubevirt.io", name, "-n", ns,
                  "--type", "merge", "-p", json.dumps(patch)])
    else:
        client.CustomObjectsApi().patch_namespaced_custom_object(
            "kubevirt.io", "v1", ns, "virtualmachines", name, patch)


def _delete_vmi(ns: str, name: str):
    if _use_kubectl():
        _kubectl(["delete", "virtualmachineinstances.kubevirt.io", name, "-n", ns,
                  f"--grace-period={GRACE_PERIOD_SECONDS}", "--wait=false"])
    else:
        client.CustomObjectsApi().delete_namespaced_custom_object(
            "kubevirt.io", "v1", ns, "virtualmachineinstances", name,
            grace_period_seconds=GRACE_PERIOD_SECONDS)


def _vm_stop_patch(vm: dict) -> dict:
    spec = vm.get("spec", {})
    if "runStrategy" not in spec:
        return {"spec": {"running": False}}
    patch = {"spec": {"runStrategy": "Halted"}}
    if spec["runStrategy"] != "Halted":
        patch["metadata"] = {"annotations": {PREV_RUN_STRATEGY_ANNOTATION: spec["runStrategy"]}}
    return patch


def _vm_start_patch(vm: dict) -> dict:
    spec = vm.get("spec", {})
    if "runStrategy" not in spec:
        return {"spec": {"running": True}}
    prev = (vm.get("metadata", {}).get("annotations") or {}).get(PREV_RUN_STRATEGY_ANNOTATION)
    strategy = prev if prev in ("Always", "RerunOnFailure", "Once") else "Always"
    # null removes the annotation in a merge patch.
    return {"spec": {"runStrategy": strategy},
            "metadata": {"annotations": {PREV_RUN_STRATEGY_ANNOTATION: None}}}


def _start_virtual_machines(vms: list[str]) -> int:
    """Start target KubeVirt VirtualMachines (runStrategy restored / running=true)."""
    vms = _normalize_vm_refs(vms)
    if not vms:
        return 0
    started = 0
    logger.info(f"Starting {len(vms)} VirtualMachine(s) via {'kubectl' if _use_kubectl() else 'API'}...")
    for vm_ref in vms:
        ns, _, name = vm_ref.partition("/")
        try:
            _patch_vm(ns, name, _vm_start_patch(_get_vm(ns, name)))
            logger.info(f"Successfully started VirtualMachine {ns}/{name}")
            started += 1
        except Exception as e:
            logger.error(f"Failed to start VirtualMachine {ns}/{name}: {e}")
    return started


def run_poweron_sequence(
    target_nodes: list[str],
    target_vms: list[str],
    node_overrides: dict,
    default_override: dict,
    wait_for_ready: bool = True,
    timeout_seconds: int = 300,
    wait_nodes: list[str] = None,
    holds_lock: bool = True,
):
    """Background thread: BMC power-on (IPMI/Redfish), wait for Ready, start VMs."""
    global _poweron_in_progress, _poweron_status
    try:
        _poweron_status = {
            "state": "running",
            "detail": f"Powering on {len(target_nodes)} node(s) via BMC",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Baremetal power-on via the node's BMC (management IP, not host IP)
        powered_nodes, failed_nodes = [], {}
        for node in target_nodes:
            try:
                cfg = _bmc_for_node(node, (node_overrides or {}).get(node), default_override)
                logger.info(f"Powering on node '{node}' via {cfg['protocol']} at {cfg['host']}:{cfg['port']}")
                result = bmc.power_on(cfg)
                logger.info(f"Node '{node}' BMC power-on: {result}")
                powered_nodes.append(node)
            except bmc.BmcError as e:
                logger.error(f"BMC power-on failed for node '{node}': {e}")
                failed_nodes[node] = str(e)

        # 2. Wait for powered-on (and explicitly awaited) nodes to become Ready
        to_wait = list(dict.fromkeys(powered_nodes + list(wait_nodes or [])))
        if wait_for_ready and to_wait:
            _poweron_status["detail"] = f"Waiting for node(s) {to_wait} to become Ready"
            for node in to_wait:
                _wait_for_node_ready(node, timeout_seconds=timeout_seconds)

        # 3. Start target VMs
        started_count = 0
        if target_vms:
            _poweron_status["detail"] = f"Powering on {len(target_vms)} target VirtualMachine(s)"
            started_count = _start_virtual_machines(target_vms)
            logger.info(f"Power-on sequence started {started_count} VirtualMachine(s)")

        _poweron_status = {
            "state": "error" if failed_nodes else "completed",
            "detail": (f"Power-on sequence finished (nodes: {len(powered_nodes)}/{len(target_nodes)}, "
                       f"vms: {started_count}/{len(target_vms)})"),
            "failedNodes": failed_nodes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Power-on sequence failed: {e}")
        _poweron_status = {
            "state": "error",
            "detail": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if holds_lock:
            with _poweron_lock:
                _poweron_in_progress = False


async def _json_body(request: Request) -> dict:
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@app.post("/system/bmc/test", dependencies=[Depends(verify_token)])
async def test_bmc_connection(request: Request):
    """Test IPMI / Redfish connectivity to a server's management (BMC) IP.

    JSON body (fields left empty fall back to the saved config of ``node``):
    {
      "node": "harvester-1",
      "protocol": "ipmi" | "redfish",
      "host": "10.0.99.51",          # management IP — NOT the node's host IP
      "port": 623,                    # default 623 (ipmi) / 443 (redfish)
      "user": "admin",
      "password": "...",
      "verifyTls": false              # redfish only
    }
    Always 200 with {"ok": bool, ...} so the dashboard can show the reason.
    """
    if not rate_limiter.is_allowed(_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )
    body = await _json_body(request)
    node = str(body.get("node") or "")
    override = {k: body.get(k) for k in ("protocol", "host", "bmcIp", "port", "user", "password", "verifyTls")}
    started = time.time()
    try:
        cfg = _bmc_for_node(node, override)
        info = await asyncio.to_thread(bmc.test_connection, cfg)
    except bmc.BmcError as e:
        logger.info(f"BMC test for node '{node or '-'}' failed: {e}")
        return {"ok": False, "error": str(e), "node": node,
                "durationMs": int((time.time() - started) * 1000)}
    logger.info(f"BMC test for node '{node or '-'}' OK via {cfg['protocol']} {cfg['host']}:{cfg['port']}")
    return {"ok": True, "node": node, "host": cfg["host"], "port": cfg["port"],
            "testedFrom": NODE_NAME, "durationMs": int((time.time() - started) * 1000), **info}


@app.post("/system/vm/{action}", dependencies=[Depends(verify_token)])
async def vm_power(action: str, request: Request):
    """Start or stop specific VirtualMachines, independent of the node they run on.

    POST /system/vm/start | /system/vm/stop   body: {"vms": ["ns/name", ...]}
    Used by the per-VM power schedules. Runs in the background; idempotent.
    """
    if action not in ("start", "stop"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown action")
    if not rate_limiter.is_allowed(_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )
    vms = _normalize_vm_refs((await _json_body(request)).get("vms"))
    if not vms:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No VMs given")
    fn = _start_virtual_machines if action == "start" else _stop_virtual_machines
    logger.info(f"VM {action} requested for {vms}")
    threading.Thread(target=fn, args=(vms,), daemon=True, name=f"vm-{action}").start()
    return {"status": f"VM {action} initiated", "vms": vms,
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/system/poweron/status", dependencies=[Depends(verify_token)])
async def get_poweron_status():
    """Return the current status of power-on operations."""
    return {
        "inProgress": _poweron_in_progress,
        "status": _poweron_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/system/poweron", dependencies=[Depends(verify_token)])
async def execute_poweron(request: Request):
    """Power on baremetal nodes via their BMC (IPMI/Redfish) and/or start VMs.

    JSON body:
    {
      "nodes": ["worker-1"],           # BMC power-on; config resolved from the
                                       # mounted BMC Secret unless overridden
      "vms": ["default/vm1"],          # VirtualMachines to start afterwards
      "waitNodes": ["worker-1"],       # also wait for these to be Ready first
      "nodeBmc": {"worker-1": {"protocol": "redfish", "host": "10.0.99.51"}},
      "ipmiUser": "admin", "ipmiPassword": "...",   # legacy default override
      "waitForReady": true,
      "timeoutSeconds": 300
    }
    Only requests that power on nodes take the power-on lock; a VM-only start
    (idempotent) may run alongside it, e.g. node and VM crons at the same time.
    """
    global _poweron_in_progress

    if not rate_limiter.is_allowed(_client_ip(request)):
        logger.warning("Rate limit exceeded for authenticated poweron request")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )

    req_body = await _json_body(request)
    target_nodes = [str(n) for n in (req_body.get("nodes") or []) if n]
    target_vms = _normalize_vm_refs(req_body.get("vms"))
    wait_nodes = [str(n) for n in (req_body.get("waitNodes") or []) if n]
    node_overrides = req_body.get("nodeBmc") or {}
    default_override = {"user": req_body.get("ipmiUser"), "password": req_body.get("ipmiPassword")}
    wait_for_ready = bool(req_body.get("waitForReady", True))
    try:
        timeout_seconds = int(req_body.get("timeoutSeconds", 300))
    except (TypeError, ValueError):
        timeout_seconds = 300

    holds_lock = bool(target_nodes)
    if holds_lock:
        with _poweron_lock:
            if _poweron_in_progress:
                logger.warning("Power-on already in progress, ignoring duplicate request")
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"detail": "Power-on sequence already in progress"},
                )
            _poweron_in_progress = True

    logger.info(f"Power-on initiated: nodes={target_nodes}, vms={target_vms}, waitNodes={wait_nodes}")
    thread = threading.Thread(
        target=run_poweron_sequence,
        args=(target_nodes, target_vms, node_overrides, default_override,
              wait_for_ready, timeout_seconds, wait_nodes, holds_lock),
        daemon=True,
        name="poweron-daemon",
    )
    thread.start()

    return {
        "status": "Power-on sequence initiated",
        "nodes": target_nodes,
        "vms": target_vms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_ip_online(ip: str) -> bool:
    """Check if a peer node's webhook is still online/reachable."""
    url = f"{PEER_SCHEME}://{ip}:{NODE_PORT}/healthz"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3, context=_peer_ssl_context()) as response:
            return response.status == 200
    except Exception:
        # If any exception occurs (connection refused, timeout, host unreachable), it is offline
        return False


def run_shutdown_sequence(peer_ips: list[str] = None, vm_strategy: str = "force",
                          target_vms: list[str] = None, poweroff_host: bool = True):
    """Run the full shutdown sequence in a background daemon thread.

    When ``poweroff_host`` is False only the VM phase runs and the host is
    left powered on (VM-only schedules).
    """
    try:
        _graceful_vm_shutdown(vm_strategy, target_vms)

        if not poweroff_host:
            logger.info("VM-only run complete — skipping host poweroff as requested")
            return

        if peer_ips:
            # Wait for peers to go offline before powering off this node
            logger.info(f"Local VMs shut down. Waiting for peer nodes {peer_ips} to go offline...")
            start_time = time.time()
            remaining_peers = list(peer_ips)
            while remaining_peers and (time.time() - start_time < 300):
                still_online = []
                for ip in remaining_peers:
                    is_online = check_ip_online(ip)
                    if is_online:
                        still_online.append(ip)
                remaining_peers = still_online
                if remaining_peers:
                    logger.info(f"Peer nodes still online: {remaining_peers}. Waiting...")
                    time.sleep(5)
            if remaining_peers:
                logger.warning(f"Timeout reached. Proceeding to power off coordinator node anyway. Peers still online: {remaining_peers}")
            else:
                logger.info("All peer nodes are offline. Proceeding to power off coordinator node.")

        _host_poweroff()
    except Exception as e:
        logger.error(f"Background shutdown failed: {str(e)}")
    finally:
        global _shutdown_in_progress
        with _shutdown_lock:
            _shutdown_in_progress = False


def call_shutdown_on_node(ip: str, vm_strategy: str = "force", target_vms: list[str] = None,
                          poweroff_host: bool = True):
    """Trigger local shutdown on a peer node, passing strategy, VM filter, and poweroff flag."""
    url = f"{PEER_SCHEME}://{ip}:{NODE_PORT}/system/shutdown?all_nodes=false"
    logger.info(f"Sending shutdown request to peer node at {url} (vmStrategy={vm_strategy}, poweroff={poweroff_host})")
    payload = json.dumps({"vmStrategy": vm_strategy, "vms": target_vms or [], "poweroff": poweroff_host}).encode("utf-8")
    req = urllib.request.Request(
        url,
        method="POST",
        data=payload,
        headers={
            "Authorization": f"Bearer {_current_token()}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_peer_ssl_context()) as response:
            res_data = response.read().decode()
            logger.info(f"Peer node at {ip} response: {res_data}")
    except Exception as e:
        logger.error(f"Failed to trigger shutdown on peer node at {ip}: {e}")


async def coordinate_cluster_shutdown(target_nodes: list[str] = None, vm_strategy: str = "force",
                                   target_vms: list[str] = None, poweroff_host: bool = True):
    """Coordinate shutdown across nodes.

    target_nodes empty/None => whole cluster. Otherwise only the listed nodes are
    shut down (selected-node shutdown), and this coordinator only powers itself off
    if it is one of the targets. vm_strategy, target_vms and poweroff_host are
    forwarded to every node.
    """
    target_nodes = target_nodes or []
    self_is_target = (not target_nodes) or (NODE_NAME in target_nodes)
    logger.info(f"Coordinating shutdown (targets={target_nodes or 'ALL'}, vms={target_vms or 'ALL'}, vmStrategy={vm_strategy}, poweroff={poweroff_host}, self_target={self_is_target})")
    peer_ips = []
    try:
        namespace = "harvester-system"
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
                namespace = f.read().strip()
        except Exception:
            pass

        # List all pods in the DaemonSet to get peer IPs
        pods = k8s_core.list_namespaced_pod(
            namespace=namespace,
            label_selector="app=node-shutdown"
        ).items

        tasks = []
        for pod in pods:
            pod_ip = pod.status.pod_ip or pod.status.host_ip
            node_name = pod.spec.node_name
            if not pod_ip or node_name == NODE_NAME:
                continue
            # When targeting specific nodes, only call those peers.
            if target_nodes and node_name not in target_nodes:
                continue

            logger.info(f"Adding peer node {node_name} (IP: {pod_ip}) to shutdown queue")
            peer_ips.append(pod_ip)
            tasks.append(asyncio.to_thread(call_shutdown_on_node, pod_ip, vm_strategy, target_vms, poweroff_host))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Coordinated shutdown calls to peer nodes completed")
        else:
            logger.info("No peer nodes found to shut down")

    except Exception as e:
        logger.error(f"Error during cluster shutdown coordination: {e}")
    finally:
        if self_is_target:
            logger.info(f"Initiating local VM shutdown phase on {NODE_NAME}")
            thread = threading.Thread(
                target=run_shutdown_sequence,
                args=(peer_ips, vm_strategy, target_vms, poweroff_host),
                daemon=True,
                name="shutdown-daemon",
            )
            thread.start()
        else:
            # Coordinator is not a target — orchestrate only, do not power off self.
            logger.info(f"Node {NODE_NAME} is not a shutdown target; not powering off self.")
            global _shutdown_in_progress
            with _shutdown_lock:
                _shutdown_in_progress = False


def _list_virt_launchers_on_node():
    """Return running virt-launcher pods scheduled on this node."""
    pods = k8s_core.list_pod_for_all_namespaces(
        field_selector=f"spec.nodeName={NODE_NAME},status.phase=Running"
    ).items
    return [p for p in pods if p.metadata.name.startswith("virt-launcher-")]


def _force_kill_vms_on_node(target_vms: list[str] = None) -> int:
    """Immediately force-kill VM workloads on this node.

    Force-deletes virt-launcher pods on this node with
    grace_period_seconds=0. This sends SIGKILL to the QEMU process
    immediately, bypassing any guest-OS shutdown sequence.
    No waiting, no ACPI signals — hard stop.

    When ``target_vms`` is given, only launchers positively mapped to a
    selected VMI (via the ``kubevirt.io/created-by`` UID label) are killed;
    unmapped pods are left alone (fail-closed, never widened).

    Returns the number of virt-launcher pods killed.
    """
    try:
        launchers = _list_virt_launchers_on_node()
    except Exception as e:
        logger.error(f"Failed to list virt-launcher pods: {e}")
        return 0

    if not launchers:
        logger.info(f"No virt-launcher pods running on node {NODE_NAME}")
        return 0

    if target_vms:
        try:
            custom = client.CustomObjectsApi()
            vmis = custom.list_cluster_custom_object(
                "kubevirt.io", "v1", "virtualmachineinstances"
            ).get("items", [])
        except Exception as e:
            logger.error(f"Failed to list VMIs for VM filter: {e}")
            return 0
        selected = set(target_vms)
        wanted_uids = {
            v["metadata"]["uid"]
            for v in vmis
            if v.get("status", {}).get("nodeName") == NODE_NAME
            and f"{v['metadata']['namespace']}/{v['metadata']['name']}" in selected
            and v["metadata"].get("uid")
        }
        if not wanted_uids:
            logger.warning("VM filter matched no VMIs on this node — killing nothing")
            return 0
        before = len(launchers)
        launchers = [
            p for p in launchers
            if (p.metadata.labels or {}).get("kubevirt.io/created-by") in wanted_uids
        ]
        logger.info(f"VM filter selected {len(launchers)}/{before} virt-launcher pod(s)")
        if not launchers:
            return 0

    logger.info(f"Force-killing {len(launchers)} virt-launcher pod(s) on node {NODE_NAME} (grace_period=0)")
    killed = 0
    for pod in launchers:
        pod_name = pod.metadata.name
        pod_ns = pod.metadata.namespace
        try:
            if _use_kubectl():
                _kubectl(["delete", "pod", pod_name, "-n", pod_ns,
                          "--grace-period=0", "--force", "--wait=false"])
            else:
                k8s_core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=pod_ns,
                    body=client.V1DeleteOptions(grace_period_seconds=0),
                )
            logger.info(f"Force-killed virt-launcher pod {pod_ns}/{pod_name}")
            killed += 1
        except Exception as e:
            logger.error(f"Failed to force-kill pod {pod_ns}/{pod_name}: {e}")
    return killed


def _wait_for_no_virt_launchers(timeout_s: int) -> bool:
    """Wait until no running virt-launcher pods remain on this node (best effort)."""
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            remaining = _list_virt_launchers_on_node()
        except Exception:
            remaining = []
        if not remaining:
            return True
        logger.info(f"Waiting for {len(remaining)} VM(s) to leave node {NODE_NAME}...")
        time.sleep(5)
    return False


def _stop_vms_on_node(target_vms: list[str] = None):
    """Gracefully stop the VirtualMachines whose VMI runs on this node.

    Patches each owning VirtualMachine to Halted / running=false (ACPI guest
    shutdown, no restart); standalone VMIs are deleted. Waits for the
    virt-launcher pods to terminate. When ``target_vms`` is given, only those
    "namespace/name" VMs are stopped.
    """
    custom = client.CustomObjectsApi()
    try:
        vmis = custom.list_cluster_custom_object(
            "kubevirt.io", "v1", "virtualmachineinstances"
        ).get("items", [])
    except Exception as e:
        logger.error(f"Failed to list VMIs: {e}")
        vmis = []
    on_node = [v for v in vmis if v.get("status", {}).get("nodeName") == NODE_NAME]
    if target_vms:
        selected = set(target_vms)
        before = len(on_node)
        on_node = [
            v for v in on_node
            if f"{v['metadata']['namespace']}/{v['metadata']['name']}" in selected
        ]
        logger.info(f"VM filter selected {len(on_node)}/{before} VM(s) on node {NODE_NAME}")
    if not on_node:
        logger.info(f"No VMs on node {NODE_NAME}")
        return
    logger.info(f"Gracefully stopping {len(on_node)} VM(s) on node {NODE_NAME}")
    for vmi in on_node:
        name = vmi["metadata"]["name"]
        ns = vmi["metadata"]["namespace"]
        _stop_vm(ns, name)
    _wait_for_no_virt_launchers(max(VM_SHUTDOWN_TIMEOUT, 60))


def _stop_vm(ns: str, name: str) -> bool:
    """Gracefully stop one VM (Halted / running=false); delete it if it is a standalone VMI."""
    try:
        # Merge patch (an OBJECT). A JSON-Patch array is rejected with
        # "cannot unmarshal array into Go value of type map[string]interface {}".
        _patch_vm(ns, name, _vm_stop_patch(_get_vm(ns, name)))
        logger.info(f"Requested stop of VM {ns}/{name}")
        return True
    except Exception as e:
        if getattr(e, "status", None) != 404:
            logger.error(f"Failed to stop VM {ns}/{name}: {e}")
            return False
    try:
        _delete_vmi(ns, name)
        logger.info(f"Deleted standalone VMI {ns}/{name}")
        return True
    except Exception as de:
        logger.error(f"Failed to delete VMI {ns}/{name}: {de}")
        return False


def _stop_virtual_machines(vms: list[str]) -> int:
    """Stop the given "namespace/name" VMs wherever they run (node-independent)."""
    vms = _normalize_vm_refs(vms)
    logger.info(f"Stopping {len(vms)} VirtualMachine(s) via {'kubectl' if _use_kubectl() else 'API'}...")
    return sum(1 for ref in vms if _stop_vm(*ref.split("/", 1)))


def _migrate_vms_off_node(target_vms: list[str] = None):
    """Live-migrate VMs off this node to surviving nodes; stop the ones that can't.

    Creates a VirtualMachineInstanceMigration per VMI on this node, waits for them
    to leave, then falls back to a graceful stop for any remainder (e.g. no
    eligible target). Intended for selected-node shutdown where other nodes stay up.
    When ``target_vms`` is given, only those "namespace/name" VMs are migrated.
    """
    custom = client.CustomObjectsApi()
    try:
        vmis = custom.list_cluster_custom_object(
            "kubevirt.io", "v1", "virtualmachineinstances"
        ).get("items", [])
    except Exception as e:
        logger.error(f"Failed to list VMIs: {e}")
        vmis = []
    on_node = [v for v in vmis if v.get("status", {}).get("nodeName") == NODE_NAME]
    if target_vms:
        selected = set(target_vms)
        before = len(on_node)
        on_node = [
            v for v in on_node
            if f"{v['metadata']['namespace']}/{v['metadata']['name']}" in selected
        ]
        logger.info(f"VM filter selected {len(on_node)}/{before} VM(s) on node {NODE_NAME}")
    if not on_node:
        logger.info(f"No VMs to migrate off node {NODE_NAME}")
        return
    logger.info(f"Live-migrating {len(on_node)} VM(s) off node {NODE_NAME}")
    for vmi in on_node:
        name = vmi["metadata"]["name"]
        ns = vmi["metadata"]["namespace"]
        migration = {
            "apiVersion": "kubevirt.io/v1",
            "kind": "VirtualMachineInstanceMigration",
            "metadata": {"generateName": f"evict-{name}-", "namespace": ns},
            "spec": {"vmiName": name},
        }
        try:
            if _use_kubectl():
                # `create` (not apply) because generateName needs a create call.
                _kubectl(["create", "-f", "-"], stdin=json.dumps(migration))
            else:
                custom.create_namespaced_custom_object(
                    "kubevirt.io", "v1", ns, "virtualmachineinstancemigrations", migration)
            logger.info(f"Started migration of VMI {ns}/{name}")
        except Exception as e:
            logger.error(f"Failed to start migration for {ns}/{name}: {e}")
    if _wait_for_no_virt_launchers(max(VM_SHUTDOWN_TIMEOUT, 120)):
        logger.info("All VMs migrated off the node")
        return
    logger.warning("Some VMs did not migrate in time — stopping the remainder")
    try:
        _stop_vms_on_node()
    except Exception as e:
        logger.error(f"Fallback stop failed: {e}")


def _graceful_vm_shutdown(vm_strategy: str = "force", target_vms: list[str] = None):
    """Handle this node's VM workloads per the chosen strategy, then poweroff.

    - "force"   : force-kill virt-launcher pods immediately (grace 0). Fastest.
    - "stop"    : gracefully stop the owning VirtualMachines (ACPI, no restart).
    - "migrate" : live-migrate VMs to surviving nodes; stop the ones that can't.
    When ``target_vms`` is given, only those "namespace/name" VMs are touched;
    otherwise every VM workload on this node is handled. Execution always
    proceeds to _host_poweroff() afterwards (unless the caller skips it).
    """
    logger.info(f"VM shutdown phase (strategy={vm_strategy}, vms={target_vms or 'ALL'}) on node {NODE_NAME}")
    try:
        if vm_strategy == "migrate":
            _migrate_vms_off_node(target_vms)
        elif vm_strategy == "stop":
            _stop_vms_on_node(target_vms)
        else:
            killed = _force_kill_vms_on_node(target_vms)
            logger.info(f"Force-killed {killed} VM(s) on node {NODE_NAME}")
    except Exception as pods_error:
        logger.error(f"VM strategy '{vm_strategy}' failed: {str(pods_error)} — proceeding to poweroff anyway")


def _host_poweroff():
    """Execute host poweroff commands."""
    logger.info("Proceeding with baremetal poweroff...")

    # Shutdown commands: host binaries accessed via chroot /host
    chroot_commands = [
        ["/host", "/usr/bin/systemctl", "poweroff", "--force"],
        ["/host", "/usr/bin/poweroff", "--force"],
        ["/host", "/usr/sbin/poweroff", "--force"],
        ["/host", "/sbin/poweroff", "--force"],
        ["/host", "/sbin/shutdown", "-h", "now"],
        ["/host", "/usr/sbin/shutdown", "-h", "now"],
    ]

    for chroot_root, binary, *args in chroot_commands:
        full_cmd = ["chroot", chroot_root, binary] + args
        try:
            logger.info(f"Attempting shutdown via {binary}")
            subprocess.run(full_cmd, check=True, timeout=30)
            logger.info(f"Shutdown initiated successfully via {binary}")
            return  # Success — node powers off
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"Shutdown via {binary} failed: {e}")

    # SysRq kernel fallback shutdown
    logger.warning("All chroot shutdown commands failed. Attempting SysRq kernel fallback...")
    try:
        sysrq_path = "/host/proc/sys/kernel/sysrq"
        if os.path.exists(sysrq_path):
            with open(sysrq_path, "w") as f:
                f.write("1\n")
            logger.info("SysRq successfully enabled on host")

        trigger_path = "/host/proc/sysrq-trigger"
        if os.path.exists(trigger_path):
            with open(trigger_path, "w") as f:
                f.write("o\n")
            logger.info("SysRq poweroff command ('o') written to host trigger")
            time.sleep(10)
            return
        else:
            logger.error("SysRq trigger file /host/proc/sysrq-trigger does not exist")
    except Exception as sysrq_err:
        logger.error(f"SysRq kernel fallback failed: {sysrq_err}")

    logger.error("All shutdown methods failed")
    raise RuntimeError("All shutdown methods failed")


if __name__ == "__main__":
    import uvicorn

    ssl_args = {}
    if TLS_ENABLED and os.path.exists(TLS_CERT_PATH) and os.path.exists(TLS_KEY_PATH):
        ssl_args = {"ssl_certfile": TLS_CERT_PATH, "ssl_keyfile": TLS_KEY_PATH}
        logger.info("TLS enabled — serving HTTPS on port %s", LISTEN_PORT)
    elif TLS_ENABLED:
        logger.warning(
            "TLS_ENABLED=true but cert/key not found at %s / %s — falling back to plain HTTP",
            TLS_CERT_PATH, TLS_KEY_PATH,
        )
    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT, **ssl_args)