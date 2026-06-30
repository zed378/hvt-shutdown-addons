import os
import unittest
from unittest.mock import patch, MagicMock

# Set env vars required by app/main.py before importing
os.environ["NODE_NAME"] = "test-node"
os.environ["AUTH_TOKEN"] = "test-token"

# Try to import FastAPI's TestClient
try:
    from fastapi.testclient import TestClient
    from app.main import app, rate_limiter
    HAS_DEPENDENCIES = True
except ImportError:
    HAS_DEPENDENCIES = False

class TestNodeShutdownAPI(unittest.TestCase):
    def setUp(self):
        if not HAS_DEPENDENCIES:
            self.skipTest("FastAPI or dependencies not installed")
        self.client = TestClient(app)
        # Reset rate limiter
        rate_limiter.requests = []

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_healthz_ready(self):
        response = self.client.get("/healthz/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    @patch("app.main.k8s_core")
    def test_healthz_k8s_connected(self, mock_k8s):
        mock_k8s.list_namespaced_pod.return_value = MagicMock()
        response = self.client.get("/healthz/k8s")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["k8s"], "connected")

    def test_shutdown_unauthorized(self):
        response = self.client.post("/system/shutdown")
        self.assertEqual(response.status_code, 401)

    def test_shutdown_bad_token(self):
        response = self.client.post(
            "/system/shutdown",
            headers={"Authorization": "Bearer bad-token"}
        )
        self.assertEqual(response.status_code, 401)

    @patch("app.main.threading.Thread")
    def test_shutdown_authorized(self, mock_thread):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance
        
        response = self.client.post(
            "/system/shutdown",
            headers={"Authorization": "Bearer test-token"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Shutdown sequence initiated", response.json()["status"])
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    @patch("app.main.coordinate_cluster_shutdown")
    def test_shutdown_all_nodes(self, mock_coordinate):
        response = self.client.post(
            "/system/shutdown?all_nodes=true",
            headers={"Authorization": "Bearer test-token"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("all_nodes=True", response.json()["status"])

if __name__ == "__main__":
    unittest.main()
