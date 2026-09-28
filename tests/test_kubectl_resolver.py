"""Tests for picking a kubectl compatible with the running cluster version."""
import os

import test_vm_selection  # noqa: F401  (installs the fastapi/kubernetes stubs)
from app import kubectl_resolver as kr

HOST = ("host", "/host/var/lib/rancher/rke2/data/v1.34.3-rke2r3/bin/kubectl")
B34 = ("bundled", "/opt/kubectl/v1.34.3/kubectl")
B35 = ("bundled", "/opt/kubectl/v1.35.2/kubectl")
B36 = ("bundled", "/opt/kubectl/v1.36.4/kubectl")
VERSIONS = {HOST[1]: "v1.34.3+rke2r3", B34[1]: "v1.34.3", B35[1]: "v1.35.2", B36[1]: "v1.36.4"}


def test_parse_minor():
    assert kr.parse_minor("v1.34.3+rke2r3") == (1, 34)
    assert kr.parse_minor("1.36") == (1, 36)
    assert kr.parse_minor("") is None


def test_host_kubectl_preferred_on_exact_match():
    got = kr.choose("v1.34.3+rke2r3", [HOST, B34, B35, B36], VERSIONS)
    assert got["source"] == "host" and got["skew"] == 0


def test_upgraded_cluster_picks_matching_bundled_minor():
    # Harvester v1.9 (k8s 1.36) but host binary not reachable: bundled 1.36 wins.
    got = kr.choose("v1.36.4+rke2r1", [B34, B35, B36], VERSIONS)
    assert got["path"] == B36[1] and got["skew"] == 0


def test_nearest_within_skew_when_no_exact_minor():
    # k8s 1.37 (future Harvester): 1.36 is within +/-1, 1.34/1.35 are not.
    got = kr.choose("v1.37.0", [B34, B35, B36], VERSIONS)
    assert got["path"] == B36[1] and got["skew"] == -1
    # k8s 1.33 (older Harvester): only 1.34 is within skew.
    assert kr.choose("v1.33.5", [B34, B35, B36], VERSIONS)["path"] == B34[1]


def test_nothing_within_skew_returns_none():
    assert kr.choose("v1.30.0", [B34, B35, B36], VERSIONS) is None
    assert kr.choose("v1.30.0", [B34, B35, B36], VERSIONS, max_skew=5)["path"] == B34[1]


def test_unknown_server_version_prefers_host():
    assert kr.choose("", [B36, HOST], VERSIONS)["source"] == "host"


def test_explicit_path_used_even_if_version_unknown():
    got = kr.choose("v1.34.3", [("path", "/custom/kubectl"), B34], {**VERSIONS, "/custom/kubectl": ""})
    assert got["path"] == "/custom/kubectl"


def test_host_candidates_resolve_absolute_rke2_symlink(monkeypatch):
    # /var/lib/rancher/rke2/bin is an ABSOLUTE symlink on the host; inside the pod
    # it must be re-rooted under HOST_ROOT rather than followed.
    monkeypatch.setattr(kr, "HOST_ROOT", "/host")
    monkeypatch.setattr(kr.os, "readlink", lambda p: "/var/lib/rancher/rke2/data/v1.34.3-rke2r3-abc/bin")
    monkeypatch.setattr(kr.glob, "glob", lambda pattern: [])
    cands = kr._host_candidates()
    assert cands[0].replace("\\", "/") == "/host/var/lib/rancher/rke2/data/v1.34.3-rke2r3-abc/bin/kubectl"
