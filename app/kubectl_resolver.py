"""Pick a kubectl binary that is compatible with the cluster this pod runs in.

kubectl is only supported within one minor version of the API server, so a
single binary baked into the image breaks as soon as Harvester is upgraded (or
when deploying to an older Harvester). Instead we look at every kubectl we can
reach and choose the best match for the *running* server:

  1. an explicit path (KUBECTL_BIN)                       — operator override
  2. the host's own kubectl (RKE2 ships one per release)  — always matches
  3. versions bundled in the image under /opt/kubectl/    — closest minor wins
  4. kubectl on PATH

A candidate is accepted only when |client minor - server minor| <= max skew.
If nothing qualifies, callers fall back to the Python Kubernetes client.
"""
import glob
import json
import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger("node-shutdown")

HOST_ROOT = os.getenv("HOST_ROOT", "/host")
BUNDLED_DIR = os.getenv("KUBECTL_BUNDLED_DIR", "/opt/kubectl")
# auto | host | bundled | path  (auto = try every source in order)
SOURCE = os.getenv("KUBECTL_SOURCE", "auto").lower()
EXPLICIT_BIN = os.getenv("KUBECTL_BIN", "")
MAX_SKEW = int(os.getenv("KUBECTL_MAX_SKEW", "1"))
# Re-resolve periodically so a host upgrade (new RKE2 data dir) is picked up.
CACHE_TTL_SECONDS = int(os.getenv("KUBECTL_RESOLVE_TTL", "600"))

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
_SOURCE_RANK = {"path": 0, "host": 1, "bundled": 2, "PATH": 3}

_lock = threading.Lock()
_cache = {"at": 0.0, "result": None}


def parse_minor(version: str):
    """'v1.34.3+rke2r3' -> (1, 34); None when unparseable."""
    m = _VERSION_RE.search(version or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def server_version() -> str:
    """gitVersion of the API server, e.g. 'v1.34.3+rke2r3' ('' if unknown)."""
    try:
        from kubernetes import client
        return client.VersionApi().get_code().git_version or ""
    except Exception as e:
        logger.warning(f"Could not read Kubernetes server version: {e}")
        return ""


def _host_candidates() -> list:
    """kubectl binaries shipped with the host OS / distribution."""
    out = []
    # RKE2 (Harvester): /var/lib/rancher/rke2/bin -> /var/lib/rancher/rke2/data/<ver>/bin
    # The symlink is absolute, so resolve it manually under HOST_ROOT.
    link = os.path.join(HOST_ROOT, "var/lib/rancher/rke2/bin")
    try:
        target = os.readlink(link)
        out.append(os.path.join(HOST_ROOT, target.lstrip("/"), "kubectl"))
    except OSError:
        pass
    out += sorted(glob.glob(os.path.join(HOST_ROOT, "var/lib/rancher/rke2/data/*/bin/kubectl")), reverse=True)
    out += [os.path.join(HOST_ROOT, p) for p in ("usr/local/bin/kubectl", "usr/bin/kubectl")]
    return out


def _bundled_candidates() -> list:
    return sorted(glob.glob(os.path.join(BUNDLED_DIR, "*", "kubectl")), reverse=True)


def candidates() -> list:
    """[(source, path), ...] in preference order, de-duplicated, existing only."""
    found = []
    if EXPLICIT_BIN and os.sep in EXPLICIT_BIN:
        found.append(("path", EXPLICIT_BIN))
    if SOURCE in ("auto", "host"):
        found += [("host", p) for p in _host_candidates()]
    if SOURCE in ("auto", "bundled"):
        found += [("bundled", p) for p in _bundled_candidates()]
    if SOURCE == "auto":
        import shutil
        on_path = shutil.which(EXPLICIT_BIN or "kubectl")
        if on_path:
            found.append(("PATH", on_path))
    seen, out = set(), []
    for src, p in found:
        real = os.path.realpath(p)
        if real in seen or not (os.path.isfile(real) and os.access(real, os.X_OK)):
            continue
        seen.add(real)
        out.append((src, p))
    return out


def client_version(path: str) -> str:
    """gitVersion of a kubectl binary ('' if it does not run)."""
    try:
        res = subprocess.run([path, "version", "--client", "-o", "json"],
                             capture_output=True, text=True, timeout=15)
        return json.loads(res.stdout).get("clientVersion", {}).get("gitVersion", "") if res.returncode == 0 else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def choose(server: str, cands: list, versions: dict, max_skew: int = MAX_SKEW):
    """Pick the best candidate. Pure function (easy to test).

    ``versions`` maps path -> client gitVersion. An explicit path is used as-is
    when its version cannot be determined. Otherwise prefer the smallest skew,
    then the preferred source (path > host > bundled > PATH).
    """
    srv = parse_minor(server)
    ranked = []
    for src, path in cands:
        cli = parse_minor(versions.get(path, ""))
        if cli is None:
            if src == "path":
                return {"source": src, "path": path, "version": "", "skew": None}
            continue
        if srv is None or cli[0] != srv[0]:
            skew = 0 if srv is None else None
        else:
            skew = cli[1] - srv[1]
        if skew is None or abs(skew) > max_skew:
            logger.info(f"kubectl {versions[path]} at {path} skipped (server {server or 'unknown'}, max skew {max_skew})")
            continue
        ranked.append((abs(skew), _SOURCE_RANK.get(src, 9), {"source": src, "path": path,
                                                             "version": versions[path], "skew": skew}))
    if not ranked:
        return None
    ranked.sort(key=lambda r: (r[0], r[1]))
    return ranked[0][2]


def resolve(force: bool = False):
    """Return the chosen kubectl as a dict (source, path, version, skew, server) or None."""
    with _lock:
        if not force and _cache["at"] and time.time() - _cache["at"] < CACHE_TTL_SECONDS:
            return _cache["result"]
        server = server_version()
        cands = candidates()
        versions = {p: client_version(p) for _, p in cands}
        result = choose(server, cands, versions)
        if result:
            result = {**result, "server": server}
            logger.info(f"Using kubectl {result['version'] or '?'} ({result['source']}: {result['path']}) "
                        f"for server {server or 'unknown'}")
        else:
            logger.warning(f"No kubectl within {MAX_SKEW} minor of server {server or 'unknown'} "
                           f"among {[p for _, p in cands]} — falling back to the Python client")
        _cache.update(at=time.time(), result=result)
        return result


def info() -> dict:
    """Diagnostics for /system/info."""
    chosen = resolve()
    return {
        "source": SOURCE,
        "maxSkew": MAX_SKEW,
        "chosen": chosen,
        "candidates": [{"source": s, "path": p} for s, p in candidates()],
    }
