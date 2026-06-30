import os
import secrets
import subprocess
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from kubernetes import client, config

# Grace period in seconds for pod termination (0 = immediate)
GRACE_PERIOD_SECONDS = int(os.getenv("GRACE_PERIOD_SECONDS", "10"))

app = FastAPI(title="Node Shutdown API")
security = HTTPBearer()


@app.get("/healthz")
def health_check():
    """Liveness probe endpoint."""
    return {"status": "ok"}


@app.get("/healthz/ready")
def readiness_check():
    """Readiness probe endpoint."""
    return {"status": "ready"}

# Load Kubernetes in-cluster configuration
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

k8s_core = client.CoreV1Api()
NODE_NAME = os.getenv("NODE_NAME")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_TOKEN environment variable is not configured on host."
        )
    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token."
        )
    return credentials.credentials

@app.post("/system/shutdown", dependencies=[Depends(verify_token)])
def execute_shutdown():
    if not NODE_NAME:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="NODE_NAME environment variable is missing."
        )

    try:
        pods = k8s_core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={NODE_NAME},status.phase=Running").items
        virt_launchers = [p for p in pods if p.metadata.name.startswith("virt-launcher-")]

        for pod in virt_launchers:
            k8s_core.delete_namespaced_pod(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=GRACE_PERIOD_SECONDS)
            )

        # Attempt host shutdown with fallback mechanisms
        try:
            subprocess.run(["chroot", "/host", "systemctl", "poweroff", "--force"], check=True, timeout=30)
        except subprocess.CalledProcessError as e:
            # Fallback: try direct systemctl call without chroot
            try:
                subprocess.run(["systemctl", "poweroff", "--force"], check=True, timeout=30)
            except subprocess.CalledProcessError:
                # Final fallback: trigger systemd shutdown directly
                subprocess.run(["/sbin/shutdown", "-h", "now"], check=False)
        except subprocess.TimeoutExpired:
            pass

        return {"status": "Shutdown sequence successfully initiated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
