import os
import signal
import secrets
import subprocess
import logging
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
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

# Rate limiting configuration
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "10"))
RATE_LIMIT_WINDOW = 60  # seconds

# Concurrent shutdown protection
_shutdown_lock = Lock()
_shutdown_in_progress = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(AUDIT_LOG_PATH) if AUDIT_ENABLED else logging.StreamHandler(),
    ],
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
    def __init__(self, max_requests: int, window: int):
        self.max_requests = max_requests
        self.window = window
        self.requests = []
        self.lock = Lock()

    def is_allowed(self) -> bool:
        with self.lock:
            now = time.time()
            self.requests = [r for r in self.requests if now - r < self.window]
            if len(self.requests) >= self.max_requests:
                return False
            self.requests.append(now)
            return True


rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE, RATE_LIMIT_WINDOW)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    logger.info("Node Shutdown API starting up...")
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

# Import asyncio for lifespan
import asyncio

# Load Kubernetes in-cluster configuration
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

k8s_core = client.CoreV1Api()
NODE_NAME = os.getenv("NODE_NAME")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

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
    logger.info(
        f"Audit: method={request.method} path={request.url.path} "
        f"status={response.status_code} duration={duration:.3f}s "
        f"client={request.client.host if request.client else 'unknown'}"
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


@app.post("/system/shutdown", dependencies=[Depends(verify_token)])
async def execute_shutdown(request: Request):
    """Execute graceful shutdown with safety checks."""
    global _shutdown_in_progress

    # Prevent concurrent shutdowns
    if _shutdown_in_progress:
        logger.warning("Shutdown already in progress, ignoring request")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Shutdown already in progress"},
        )

    with _shutdown_lock:
        _shutdown_in_progress = True

    try:
        if not NODE_NAME:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="NODE_NAME environment variable is missing",
            )

        logger.info(f"Initiating shutdown sequence on node {NODE_NAME}")

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

                # Wait for all VMs to be fully terminated before proceeding
                logger.info(f"Waiting for VMs to shut down (timeout: {VM_SHUTDOWN_TIMEOUT}s)...")
                shutdown_start = time.time()
                while time.time() - shutdown_start < VM_SHUTDOWN_TIMEOUT:
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
                    # Timeout reached - log remaining VMs but proceed with shutdown
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
            logger.error(f"Failed to list/delete pods: {str(pods_error)}")
            raise

        # Proceed to baremetal shutdown
        logger.info("Proceeding with baremetal poweroff...")

        # Attempt host shutdown with fallback mechanisms
        shutdown_methods = [
            {"name": "chroot systemctl", "cmd": ["chroot", "/host", "systemctl", "poweroff", "--force"]},
            {"name": "systemctl", "cmd": ["systemctl", "poweroff", "--force"]},
            {"name": "shutdown command", "cmd": ["/sbin/shutdown", "-h", "now"]},
        ]

        shutdown_success = False
        for method in shutdown_methods:
            try:
                logger.info(f"Attempting shutdown via {method['name']}")
                subprocess.run(method["cmd"], check=True, timeout=30)
                shutdown_success = True
                logger.info(f"Shutdown initiated successfully via {method['name']}")
                break
            except subprocess.CalledProcessError as e:
                logger.warning(f"{method['name']} failed: {e}")
            except subprocess.TimeoutExpired:
                logger.warning(f"{method['name']} timed out")

        if not shutdown_success:
            logger.error("All shutdown methods failed")
            raise RuntimeError("All shutdown methods failed")

        return {"status": "Shutdown sequence successfully initiated", "timestamp": datetime.now(timezone.utc).isoformat()}

    except Exception as e:
        logger.error(f"Shutdown failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        with _shutdown_lock:
            _shutdown_in_progress = False


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)