import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Set env vars required by app/main.py before importing
os.environ["NODE_NAME"] = "test-node"
os.environ["AUTH_TOKEN"] = "test-token"

# Try to import FastAPI's TestClient
try:
    from fastapi.testclient import TestClient
    from app import main as appmain
    from app.main import app, rate_limiter, RateLimiter
    HAS_DEPENDENCIES = True
except ImportError:
    HAS_DEPENDENCIES = False


class TestNodeShutdownAPI(unittest.TestCase):
    def setUp(self):
        if not HAS_DEPENDENCIES:
            self.skipTest("FastAPI or dependencies not installed")
        self.client = TestClient(app)
        # Reset shared global state so tests are independent of ordering.
        rate_limiter.buckets = {}
        appmain._shutdown_in_progress = False

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

    def test_shutdown_missing_token_is_401(self):
        # With auto_error=False, a missing Authorization header returns a
        # consistent 401 (not Starlette's default 403).
        response = self.client.post("/system/shutdown")
        self.assertEqual(response.status_code, 401)

    def test_shutdown_bad_token_is_401(self):
        response = self.client.post(
            "/system/shutdown",
            headers={"Authorization": "Bearer bad-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_docs_disabled_by_default(self):
        # Interactive docs / OpenAPI schema must not be exposed by default.
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        self.assertEqual(self.client.get("/docs").status_code, 404)

    @patch("app.main.coordinate_cluster_shutdown", new_callable=AsyncMock)
    def test_shutdown_authorized_cluster_wide(self, mock_coordinate):
        # Default (no query param) => cluster-wide coordination.
        response = self.client.post(
            "/system/shutdown",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cluster-wide shutdown sequence initiated",
                      response.json()["status"])

    @patch("app.main.threading.Thread")
    def test_shutdown_local_peer_call(self, mock_thread):
        # ?all_nodes=false => local-only shutdown via a background daemon thread.
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        response = self.client.post(
            "/system/shutdown?all_nodes=false",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Shutdown sequence initiated", response.json()["status"])
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    @patch("app.main.threading.Thread")
    def test_rate_limit_returns_429_after_auth(self, mock_thread):
        # Squeeze the limiter down to a single request, then confirm the second
        # authenticated request from the same client is throttled with 429.
        mock_thread.return_value = MagicMock()
        original_max = rate_limiter.max_requests
        rate_limiter.max_requests = 1
        rate_limiter.buckets = {}
        try:
            r1 = self.client.post(
                "/system/shutdown?all_nodes=false",
                headers={"Authorization": "Bearer test-token"},
            )
            self.assertEqual(r1.status_code, 200)
            # Allow another shutdown attempt (bypass the concurrency lock) so we
            # are exercising the rate limiter specifically, not the 409 path.
            appmain._shutdown_in_progress = False
            r2 = self.client.post(
                "/system/shutdown?all_nodes=false",
                headers={"Authorization": "Bearer test-token"},
            )
            self.assertEqual(r2.status_code, 429)
        finally:
            rate_limiter.max_requests = original_max

    def test_rate_limit_is_per_key(self):
        # Unauthenticated / other clients must not consume another client's budget.
        rl = RateLimiter(max_requests=1, window=60)
        self.assertTrue(rl.is_allowed("1.1.1.1"))
        self.assertFalse(rl.is_allowed("1.1.1.1"))   # same client throttled
        self.assertTrue(rl.is_allowed("2.2.2.2"))    # different client unaffected

    def test_peer_scheme_defaults_to_http(self):
        # With TLS disabled (default in tests), peer calls use http and no SSL ctx.
        self.assertEqual(appmain.PEER_SCHEME, "http")
        self.assertIsNone(appmain._peer_ssl_context())

    def test_peer_ssl_context_unverified_when_https_no_verify(self):
        import ssl
        with patch.object(appmain, "PEER_SCHEME", "https"), \
             patch.object(appmain, "PEER_TLS_VERIFY", False):
            ctx = appmain._peer_ssl_context()
            self.assertIsInstance(ctx, ssl.SSLContext)
            self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
            self.assertFalse(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
