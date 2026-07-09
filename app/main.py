import asyncio
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
    if not AUTH_TOKEN:
        logger.error("AUTH_TOKEN environment variable is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service misconfigured",
        )
    # Never log or echo credential material. Compare in constant time.
    presented = credentials.credentials if credentials else ""
    if not secrets.compare_digest(presented, AUTH_TOKEN):
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

    # Determine if this is an internal peer call (all_nodes=false) or user request
    is_peer_call = all_nodes == "false"

    if is_peer_call:
        # Internal call from another node: only shut down locally
        logger.info("Internal shutdown call from peer node — performing local shutdown only")
        thread = threading.Thread(
            target=run_shutdown_sequence,
            args=(None,),  # No peer IPs needed for local-only shutdown
            daemon=True,
            name="shutdown-daemon",
        )
        thread.start()
    else:
        # User request: cluster-wide shutdown
        logger.info("Cluster-wide shutdown requested")
        task = asyncio.create_task(coordinate_cluster_shutdown())
        # Retain a reference so the task isn't garbage-collected mid-flight.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        "status": "Shutdown sequence initiated" if is_peer_call else "Cluster-wide shutdown sequence initiated",
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


def run_shutdown_sequence(peer_ips: list[str] = None):
    """Run the full shutdown sequence in a background daemon thread."""
    try:
        _graceful_vm_shutdown()

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


def call_shutdown_on_node(ip: str):
    """Trigger shutdown on a peer node via HTTP POST request using standard urllib."""
    url = f"{PEER_SCHEME}://{ip}:{NODE_PORT}/system/shutdown?all_nodes=false"
    logger.info(f"Sending shutdown request to peer node at {url}")
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {AUTH_TOKEN}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_peer_ssl_context()) as response:
            res_data = response.read().decode()
            logger.info(f"Peer node at {ip} response: {res_data}")
    except Exception as e:
        logger.error(f"Failed to trigger shutdown on peer node at {ip}: {e}")


async def coordinate_cluster_shutdown():
    """Coordinated shutdown across all nodes by calling the webhook DaemonSet endpoints."""
    logger.info("Coordinating shutdown across all nodes...")
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

            logger.info(f"Adding peer node {node_name} (IP: {pod_ip}) to shutdown queue")
            peer_ips.append(pod_ip)
            tasks.append(asyncio.to_thread(call_shutdown_on_node, pod_ip))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Coordinated shutdown calls to peer nodes completed")
        else:
            logger.info("No peer nodes found to shut down")

    except Exception as e:
        logger.error(f"Error during cluster shutdown coordination: {e}")
    finally:
        # Finally, start the shutdown sequence on this node, passing the peer IPs to wait for
        logger.info(f"Initiating local VM shutdown phase on {NODE_NAME}")
        thread = threading.Thread(
            target=run_shutdown_sequence,
            args=(peer_ips,),
            daemon=True,
            name="shutdown-daemon",
        )
        thread.start()


def _graceful_vm_shutdown():
    """gracefully terminate VM workloads and wait for them to exit."""
    logger.info(f"Graceful VM shutdown starting on node {NODE_NAME}")
    try:
        pods = k8s_core.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={NODE_NAME},status.phase=Running"
        ).items
        virt_launchers = [p for p in pods if p.metadata.name.startswith("virt-launcher-")]

        if virt_launchers:
            logger.info(f"Found {len(virt_launchers)} VM workloads, initiating graceful deletion")
            for pod in virt_launchers:
                try:
                    k8s_core.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        body=client.V1DeleteOptions(grace_period_seconds=GRACE_PERIOD_SECONDS),
                    )
                    logger.info(f"Deleted virt-launcher pod: {pod.metadata.name}")
                except Exception as pod_error:
                    logger.error(f"Failed to delete pod {pod.metadata.name}: {str(pod_error)}")

            # Wait for all VMs to terminate before proceeding to host poweroff
            logger.info(f"Waiting for VMs to shut down (timeout: {VM_SHUTDOWN_TIMEOUT}s)...")
            shutdown_start = time.time()
            wait_timeout = max(VM_SHUTDOWN_TIMEOUT, 300)  # Cap absolute wait at 5 minutes
            while time.time() - shutdown_start < wait_timeout:
                remaining_pods = k8s_core.list_pod_for_all_namespaces(
                    field_selector=f"spec.nodeName={NODE_NAME},status.phase=Running"
                ).items
                remaining_vms = [p for p in remaining_pods if p.metadata.name.startswith("virt-launcher-")]
                if not remaining_vms:
                    logger.info("All VMs have been shut down successfully")
                    break
                logger.info(f"Waiting for {len(remaining_vms)} VM(s) to shut down...")
                time.sleep(5)
            else:
                remaining_pods = k8s_core.list_pod_for_all_namespaces(
                    field_selector=f"spec.nodeName={NODE_NAME},status.phase=Running"
                ).items
                remaining_vms = [p for p in remaining_pods if p.metadata.name.startswith("virt-launcher-")]
                if remaining_vms:
                    vm_names = [p.metadata.name for p in remaining_vms]
                    logger.warning(f"Timeout reached. {len(remaining_vms)} VM(s) still running: {vm_names}. Proceeding with node shutdown anyway.")
                else:
                    logger.info("All VMs shut down within timeout period")
        else:
            logger.info("No VM workloads found on this node")

    except Exception as pods_error:
        logger.error(f"Failed to list/delete VM pods: {str(pods_error)}")


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