import asyncio
import json
import logging
import os
import secrets
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


# auto_error=False so a *missing* Authorization header is handled by
# verify_token and returns a consistent 401 (rather than Starlette's 403).
security = HTTPBearer(auto_error=False)


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify authentication token with constant-time comparison."""
    active_token = _current_token()
    if not active_token:
        logger.error("No auth token configured (AUTH_TOKEN / AUTH_TOKEN_FILE)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service misconfigured",
        )
    # Never log or echo credential material. Compare in constant time.
    presented = credentials.credentials if credentials else ""
    if not secrets.compare_digest(presented, active_token):
        logger.warning("Failed authentication attempt (invalid or missing token)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )
    return credentials.credentials


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
    #   {"nodes": ["node-a", ...], "vmStrategy": "stop|migrate|force"}
    # nodes empty/absent  => whole cluster (current behaviour).
    # nodes given          => only those nodes are shut down (selected-node shutdown).
    # vmStrategy (default "force"): how each shutting-down node handles its VMs.
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

    # Determine if this is an internal peer call (all_nodes=false) or user request
    is_peer_call = all_nodes == "false"

    if is_peer_call:
        # Internal call from another node: only shut down locally, with the strategy.
        logger.info(f"Internal shutdown call from peer — local shutdown only (vmStrategy={vm_strategy})")
        thread = threading.Thread(
            target=run_shutdown_sequence,
            args=(None, vm_strategy),
            daemon=True,
            name="shutdown-daemon",
        )
        thread.start()
    else:
        # User request: cluster-wide, or selected nodes when target_nodes is given.
        scope = "cluster-wide" if not target_nodes else f"nodes={target_nodes}"
        logger.info(f"Shutdown requested ({scope}, vmStrategy={vm_strategy})")
        task = asyncio.create_task(coordinate_cluster_shutdown(target_nodes, vm_strategy))
        # Retain a reference so the task isn't garbage-collected mid-flight.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        "status": "Shutdown sequence initiated",
        "scope": "local" if is_peer_call else ("cluster" if not target_nodes else "selected"),
        "vmStrategy": vm_strategy,
        "timestamp": datetime.now(timezone.utc).isoformat()
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


def run_shutdown_sequence(peer_ips: list[str] = None, vm_strategy: str = "force"):
    """Run the full shutdown sequence in a background daemon thread."""
    try:
        _graceful_vm_shutdown(vm_strategy)

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


def call_shutdown_on_node(ip: str, vm_strategy: str = "force"):
    """Trigger local shutdown on a peer node, passing the VM strategy."""
    url = f"{PEER_SCHEME}://{ip}:{NODE_PORT}/system/shutdown?all_nodes=false"
    logger.info(f"Sending shutdown request to peer node at {url} (vmStrategy={vm_strategy})")
    payload = json.dumps({"vmStrategy": vm_strategy}).encode("utf-8")
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


async def coordinate_cluster_shutdown(target_nodes: list[str] = None, vm_strategy: str = "force"):
    """Coordinate shutdown across nodes.

    target_nodes empty/None => whole cluster. Otherwise only the listed nodes are
    shut down (selected-node shutdown), and this coordinator only powers itself off
    if it is one of the targets. vm_strategy is forwarded to every node.
    """
    target_nodes = target_nodes or []
    self_is_target = (not target_nodes) or (NODE_NAME in target_nodes)
    logger.info(f"Coordinating shutdown (targets={target_nodes or 'ALL'}, vmStrategy={vm_strategy}, self_target={self_is_target})")
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
            tasks.append(asyncio.to_thread(call_shutdown_on_node, pod_ip, vm_strategy))

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
                args=(peer_ips, vm_strategy),
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


def _force_kill_vms_on_node() -> int:
    """Immediately force-kill all VM workloads on this node.

    Force-deletes every virt-launcher pod on this node with
    grace_period_seconds=0. This sends SIGKILL to the QEMU process
    immediately, bypassing any guest-OS shutdown sequence.
    No waiting, no ACPI signals — hard stop.

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

    logger.info(f"Force-killing {len(launchers)} virt-launcher pod(s) on node {NODE_NAME} (grace_period=0)")
    killed = 0
    for pod in launchers:
        pod_name = pod.metadata.name
        pod_ns = pod.metadata.namespace
        try:
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


def _stop_vms_on_node():
    """Gracefully stop the VirtualMachines whose VMI runs on this node.

    Patches each owning VirtualMachine to Halted / running=false (ACPI guest
    shutdown, no restart); standalone VMIs are deleted. Waits for the
    virt-launcher pods to terminate.
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
    if not on_node:
        logger.info(f"No VMs on node {NODE_NAME}")
        return
    logger.info(f"Gracefully stopping {len(on_node)} VM(s) on node {NODE_NAME}")
    for vmi in on_node:
        name = vmi["metadata"]["name"]
        ns = vmi["metadata"]["namespace"]
        try:
            vm = custom.get_namespaced_custom_object("kubevirt.io", "v1", ns, "virtualmachines", name)
            spec = vm.get("spec", {})
            if "runStrategy" in spec:
                patch = [{"op": "add", "path": "/spec/runStrategy", "value": "Halted"}]
            else:
                patch = [{"op": "add", "path": "/spec/running", "value": False}]
            custom.patch_namespaced_custom_object("kubevirt.io", "v1", ns, "virtualmachines", name, patch)
            logger.info(f"Requested stop of VM {ns}/{name}")
        except Exception as e:
            if getattr(e, "status", None) == 404:
                try:
                    custom.delete_namespaced_custom_object(
                        "kubevirt.io", "v1", ns, "virtualmachineinstances", name,
                        grace_period_seconds=GRACE_PERIOD_SECONDS)
                    logger.info(f"Deleted standalone VMI {ns}/{name}")
                except Exception as de:
                    logger.error(f"Failed to delete VMI {ns}/{name}: {de}")
            else:
                logger.error(f"Failed to stop VM {ns}/{name}: {e}")
    _wait_for_no_virt_launchers(max(VM_SHUTDOWN_TIMEOUT, 60))


def _migrate_vms_off_node():
    """Live-migrate VMs off this node to surviving nodes; stop the ones that can't.

    Creates a VirtualMachineInstanceMigration per VMI on this node, waits for them
    to leave, then falls back to a graceful stop for any remainder (e.g. no
    eligible target). Intended for selected-node shutdown where other nodes stay up.
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
            "metadata": {"generateName": f"evict-{name}-"},
            "spec": {"vmiName": name},
        }
        try:
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


def _graceful_vm_shutdown(vm_strategy: str = "force"):
    """Handle this node's VM workloads per the chosen strategy, then poweroff.

    - "force"   : force-kill virt-launcher pods immediately (grace 0). Fastest.
    - "stop"    : gracefully stop the owning VirtualMachines (ACPI, no restart).
    - "migrate" : live-migrate VMs to surviving nodes; stop the ones that can't.
    Execution always proceeds to _host_poweroff() afterwards.
    """
    logger.info(f"VM shutdown phase (strategy={vm_strategy}) on node {NODE_NAME}")
    try:
        if vm_strategy == "migrate":
            _migrate_vms_off_node()
        elif vm_strategy == "stop":
            _stop_vms_on_node()
        else:
            killed = _force_kill_vms_on_node()
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