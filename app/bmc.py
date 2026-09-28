"""Out-of-band BMC control (IPMI over LAN and Redfish) for baremetal power-on.

The BMC / management IP of a server is NOT the node's host IP: it is the
dedicated iDRAC / iLO / XCC / IPMI port. Every function here takes a normalized
config dict (see ``normalize_config``) and never shells out through a shell, so
host/user values cannot inject commands. Passwords are passed to ipmitool via
the IPMI_PASSWORD environment variable (``-E``) so they never show up in the
process list.
"""
import base64
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request

PROTOCOLS = ("ipmi", "redfish")
DEFAULT_PORTS = {"ipmi": 623, "redfish": 443}

# IPv4, IPv6 (optionally bracketed) or a DNS hostname. No scheme, path or spaces.
_HOST_RE = re.compile(r"^\[?[A-Za-z0-9.\-:]+\]?$")

IPMI_TIMEOUT_SECONDS = 25
REDFISH_TIMEOUT_SECONDS = 15


class BmcError(Exception):
    """A BMC operation failed; the message is safe to show to an operator."""


def normalize_config(raw: dict, defaults: dict = None) -> dict:
    """Merge a per-node BMC entry over defaults and validate it.

    Accepts ``host`` / ``bmcIp`` / ``ip`` for the management address. Raises
    BmcError when the result is unusable.
    """
    defaults = defaults or {}
    raw = raw or {}

    def pick(*keys):
        for src in (raw, defaults):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    protocol = str(pick("protocol") or "ipmi").lower()
    if protocol not in PROTOCOLS:
        raise BmcError(f"Unsupported protocol '{protocol}' (use ipmi or redfish)")

    host = str(raw.get("host") or raw.get("bmcIp") or raw.get("ip") or "").strip()
    if host.lower().startswith(("http://", "https://")):
        host = host.split("://", 1)[1]
    host = host.rstrip("/")
    if not host:
        raise BmcError("Management (BMC) IP address is required")
    if not _HOST_RE.match(host):
        raise BmcError(f"Invalid management address '{host}'")

    port = pick("port")
    try:
        port = int(port) if port is not None else DEFAULT_PORTS[protocol]
    except (TypeError, ValueError):
        raise BmcError(f"Invalid port '{port}'")
    if not 1 <= port <= 65535:
        raise BmcError(f"Invalid port '{port}'")

    user = str(pick("user", "username") or "")
    if not user:
        raise BmcError("BMC username is required")

    verify = pick("verifyTls")
    return {
        "protocol": protocol,
        "host": host,
        "port": port,
        "user": user,
        "password": str(pick("password") or ""),
        "verifyTls": str(verify).lower() == "true" if verify is not None else False,
    }


# ---------------------------------------------------------------- IPMI ------

def _ipmi(cfg: dict, subcmd: list) -> str:
    host = cfg["host"].strip("[]")
    cmd = [
        "ipmitool", "-I", "lanplus", "-H", host, "-p", str(cfg["port"]),
        "-U", cfg["user"], "-E", "-N", "3", "-R", "2",
    ] + subcmd
    env = {**os.environ, "IPMI_PASSWORD": cfg["password"]}
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env,
                             timeout=IPMI_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise BmcError("ipmitool is not installed in the service image")
    except subprocess.TimeoutExpired:
        raise BmcError(f"IPMI timed out talking to {host}:{cfg['port']}")
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip() or f"exit code {res.returncode}"
        low = msg.lower()
        if "unauthorized" in low or "rakp" in low or "password" in low:
            raise BmcError(f"IPMI authentication failed — check username/password ({msg})")
        if "unable to establish" in low or "timeout" in low or "no response" in low:
            raise BmcError(f"Cannot reach BMC {host}:{cfg['port']} over IPMI — check management IP / network ({msg})")
        raise BmcError(f"IPMI error: {msg}")
    return res.stdout.strip()


def _ipmi_power_state(cfg: dict) -> str:
    out = _ipmi(cfg, ["chassis", "power", "status"]).lower()
    if "is on" in out:
        return "On"
    if "is off" in out:
        return "Off"
    return out or "Unknown"


# ------------------------------------------------------------- Redfish ------

def _redfish(cfg: dict, method: str, path: str, body: dict = None, auth: bool = True) -> dict:
    host = cfg["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # bare IPv6 literal
    url = f"https://{host}:{cfg['port']}{path}"
    headers = {"Accept": "application/json"}
    if auth:
        cred = f"{cfg['user']}:{cfg['password']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(cred).decode()
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    ctx = ssl.create_default_context()
    if not cfg["verifyTls"]:
        # BMCs almost always ship self-signed certs.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=REDFISH_TIMEOUT_SECONDS, context=ctx) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise BmcError(f"Redfish authentication failed (HTTP {e.code}) — check username/password")
        raise BmcError(f"Redfish {method} {path} failed: HTTP {e.code}")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLError):
            raise BmcError(f"TLS error talking to {cfg['host']}:{cfg['port']} ({reason}) — "
                           "disable certificate verification for self-signed BMCs")
        raise BmcError(f"Cannot reach Redfish at {cfg['host']}:{cfg['port']} — "
                       f"check management IP / network ({reason})")
    except (TimeoutError, OSError) as e:
        raise BmcError(f"Cannot reach Redfish at {cfg['host']}:{cfg['port']} ({e})")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise BmcError(f"Redfish {path} returned a non-JSON response — is this a Redfish endpoint?")


def _redfish_system(cfg: dict) -> tuple:
    """Return (system_path, system_json) for the first ComputerSystem."""
    systems = _redfish(cfg, "GET", "/redfish/v1/Systems")
    members = systems.get("Members") or []
    if not members or not members[0].get("@odata.id"):
        raise BmcError("Redfish service exposes no ComputerSystem")
    path = members[0]["@odata.id"]
    return path, _redfish(cfg, "GET", path)


# ---------------------------------------------------------- public API ------

def test_connection(cfg: dict) -> dict:
    """Authenticate to the BMC and read the power state. Raises BmcError."""
    if cfg["protocol"] == "ipmi":
        state = _ipmi_power_state(cfg)
        return {"protocol": "ipmi", "powerState": state,
                "detail": f"IPMI OK — chassis power is {state}"}
    # Unauthenticated service root first: separates "wrong IP / not Redfish"
    # from "wrong credentials" for a clearer error.
    root = _redfish(cfg, "GET", "/redfish/v1/", auth=False)
    _, system = _redfish_system(cfg)
    state = system.get("PowerState") or "Unknown"
    info = {
        "protocol": "redfish",
        "powerState": state,
        "redfishVersion": root.get("RedfishVersion"),
        "manufacturer": system.get("Manufacturer"),
        "model": system.get("Model"),
        "serialNumber": system.get("SerialNumber"),
    }
    what = " ".join(x for x in (info["manufacturer"], info["model"]) if x) or "system"
    info["detail"] = f"Redfish OK — {what}, power is {state}"
    return info


def power_state(cfg: dict) -> str:
    if cfg["protocol"] == "ipmi":
        return _ipmi_power_state(cfg)
    _, system = _redfish_system(cfg)
    return system.get("PowerState") or "Unknown"


def power_on(cfg: dict) -> str:
    """Power the server on. Returns a human-readable result. Idempotent."""
    if cfg["protocol"] == "ipmi":
        if _ipmi_power_state(cfg) == "On":
            return "already on"
        out = _ipmi(cfg, ["chassis", "power", "on"])
        return out or "power on sent"

    path, system = _redfish_system(cfg)
    if (system.get("PowerState") or "").lower() == "on":
        return "already on"
    action = (system.get("Actions") or {}).get("#ComputerSystem.Reset") or {}
    target = action.get("target") or f"{path.rstrip('/')}/Actions/ComputerSystem.Reset"
    allowed = action.get("ResetType@Redfish.AllowableValues") or []
    reset_type = "On" if (not allowed or "On" in allowed) else (
        "PushPowerButton" if "PushPowerButton" in allowed else "On")
    _redfish(cfg, "POST", target, {"ResetType": reset_type})
    return f"Redfish reset {reset_type} sent"
