"""Tests for the node shutdown API."""

import unittest
from unittest.mock import MagicMock, patch

from app.main import app, verify_token
from fastapi.testclient import TestClient


class TestShutdownAPI(unittest.TestCase):
    """Test cases for the shutdown API endpoints."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = TestClient(app)
        self.valid_token = "test-secret-token"
        self.headers = {"Authorization": f"Bearer {self.valid_token}"}

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    @patch("app.main.NODE_NAME", "test-node-01")
    @patch("app.main.k8s_core")
    def test_shutdown_success(self, mock_k8s_core):
        """Test successful shutdown request with valid token."""
        # Mock the pod list response
        mock_pod = MagicMock()
        mock_pod.metadata.name = "virt-launcher-test-pod"
        mock_pod.metadata.namespace = "default"
        mock_pod.metadata.namespace = "default"

        mock_response = MagicMock()
        mock_response.items = [mock_pod]
        mock_k8s_core.list_pod_for_all_namespaces.return_value = mock_response

        response = self.client.post(
            "/system/shutdown",
            headers=self.headers,
        )

        # Note: This will fail because chroot is not available in test env,
        # but we verify the token authentication works
        self.assertEqual(response.status_code, 200)

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    def test_shutdown_missing_token(self):
        """Test shutdown request without authentication token."""
        response = self.client.post("/system/shutdown")
        self.assertEqual(response.status_code, 403)

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    def test_shutdown_invalid_token(self):
        """Test shutdown request with invalid token."""
        invalid_headers = {"Authorization": "Bearer wrong-token"}
        response = self.client.post(
            "/system/shutdown",
            headers=invalid_headers,
        )
        self.assertEqual(response.status_code, 401)

    @patch("app.main.AUTH_TOKEN", "")
    def test_shutdown_missing_auth_token_config(self):
        """Test when AUTH_TOKEN is not configured on the host."""
        response = self.client.post(
            "/system/shutdown",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 500)

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    @patch("app.main.NODE_NAME", "")
    def test_shutdown_missing_node_name(self):
        """Test when NODE_NAME environment variable is missing."""
        response = self.client.post(
            "/system/shutdown",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 500)


class TestVerifyToken(unittest.TestCase):
    """Test cases for token verification."""

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    def test_valid_token(self):
        """Test valid token passes verification."""
        credentials = MagicMock()
        credentials.credentials = "test-secret-token"
        result = verify_token(credentials)
        self.assertEqual(result, "test-secret-token")

    @patch("app.main.AUTH_TOKEN", "test-secret-token")
    def test_invalid_token(self):
        """Test invalid token raises HTTP exception."""
        from fastapi import HTTPException
        credentials = MagicMock()
        credentials.credentials = "wrong-token"
        with self.assertRaises(HTTPException) as context:
            verify_token(credentials)
        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()