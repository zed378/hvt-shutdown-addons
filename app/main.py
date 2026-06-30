import os
import signal
import secrets
import subprocess
import logging
import time
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import threading
from threading import Lock

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
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
        # If we can't create the directory, fall back to current directory
        AUDIT_LOG_PATH = os.path.join(".", os.path.basename(AUDIT_LOG_PATH))

# Rate limiting configuration
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "10"))
RATE_LIMIT_WINDOW = 60  # seconds

# Concurrent shutdown protection - use Lock for atomic check-and-set
_shutdown_lock = Lock()
_shutdown_in_progress = False

# Kubernetes API client (initialized lazily)
k8s_core = None

# Node name and auth token from environment
NODE_NAME = os.getenv("NODE_NAME")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

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
    """Thread-safe rate limiter using sliding window algorithm."""
    def __init__(self, max_requests: int, window: int):
        self.max_requests = max_requests
        self.window = window
        self.requests = []
        self.lock = Lock()

    def is_allowed(self) -> bool:
        """Check if request is allowed under rate limit."""
        with self.lock:
            now = time.time()
            # Remove requests outside the current window
            self.requests = [r for r in self.requests if now - r < self.window]
            if len(self.requests) >= self.max_requests:
                return False
            self.requests.append(now)
            return True


rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE, RATE_LIMIT_WINDOW)

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify authentication token with constant-time comparison."""
    if not AUTH_TOKEN:
        logger.error("AUTH_TOKEN environment variable is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service misconfigured",
        )
    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        logger.warning("Failed authentication attempt from %s", credentials.credentials[:8] if credentials.credentials else "none")
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
    except config.ConfigException:
        try:
            config.load_kube_config()
        except Exception as kube_err:
            logger.warning(f"Kubernetes config not available: {kube_err}")

    k8s_core = client.CoreV1Api()

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
    version="1.0.0",
    lifespan=lifespan,
)


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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware for shutdown endpoint."""
    if request.url.path == "/system/shutdown":
        if not rate_limiter.is_allowed():
            logger.warning(f"Rate limit exceeded for request to {request.url.path}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Rate limit exceeded. Try again later.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
    return await call_next(request)


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
        k8s_core.list_namespaced_pod(namespace=namespace, limit=1)
        return {"status": "ready", "k8s": "connected", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "detail": f"Kubernetes connection failed: {str(e)}"},
        )


@app.post("/system/shutdown", dependencies=[Depends(verify_token)])
async def execute_shutdown(request: Request):
    """Execute graceful shutdown — returns immediately, runs in background thread."""
    global _shutdown_in_progress

    # --- Validation (synchronous, must complete before returning) ---

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

    # Start background shutdown thread (non-blocking)
    thread = threading.Thread(
        target=run_shutdown_sequence,
        daemon=True,
        name="shutdown-daemon",
    )
    thread.start()

    return {"status": "Shutdown sequence initiated, running in background", "timestamp": datetime.now(timezone.utc).isoformat()}


def run_shutdown_sequence():
    """Run the full shutdown sequence in a background daemon thread.

    This function runs to completion (or until all methods fail).
    The _shutdown_in_progress lock is ALWAYS reset in the finally block.
    """
    try:
        _execute_shutdown_logic()
    except Exception as e:
        logger.error(f"Background shutdown failed: {str(e)}")
    finally:
        global _shutdown_in_progress
        with _shutdown_lock:
            _shutdown_in_progress = False


def _execute_shutdown_logic():
    """Core shutdown logic: VM deletion, wait, host poweroff.

    Runs in a daemon thread. All blocking calls (subprocess, k8s API, time.sleep)
    are safe here because this runs in a separate thread, not the event loop.
    """
    logger.info(f"Background shutdown sequence starting on node {NODE_NAME}")

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
        raise

    # Proceed to baremetal shutdown
    logger.info("Proceeding with baremetal poweroff...")

    # Shutdown commands: host binaries accessed via chroot /host
    # Harvester (SUSE Linux Enterprise + K3s) may have binaries at different paths
    chroot_commands = [
        ["/host", "/usr/bin/systemctl", "poweroff", "--force"],
        ["/host", "/usr/bin/poweroff", "--force"],
        ["/host", "/usr/sbin/poweroff", "--force"],
        ["/host", "/sbin/poweroff", "--force"],
        ["/host", "/sbin/shutdown", "-h", "now"],
        ["/host", "/usr/sbin/shutdown", "-h", "now"],
    ]

    for chroot_root, binary, *args in chroot_commands:
        # Construct: chroot <root> <binary> <args>
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

    uvicorn.run(app, host="0.0.0.0", port=8080)