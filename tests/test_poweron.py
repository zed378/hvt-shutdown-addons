"""Tests for BMC (IPMI / Redfish) power-on, BMC test connection and kubectl VM control.

Reuses the fastapi/kubernetes stubs from test_vm_selection so it runs with
plain pytest anywhere.
"""
import json
import types
from unittest import mock

import pytest

import test_vm_selection  # noqa: F401  (installs the stubs, imports app.main)
from app import bmc
import app.main as main


# --- bmc.normalize_config -----------------------------------------------------

def test_normalize_defaults_and_ports():
    cfg = bmc.normalize_config({"bmcIp": "10.0.99.51"}, {"user": "admin", "password": "pw"})
    assert cfg == {"protocol": "ipmi", "host": "10.0.99.51", "port": 623,
                   "user": "admin", "password": "pw", "verifyTls": False}
    cfg = bmc.normalize_config({"protocol": "redfish", "host": "https://bmc1.lab/"}, {"user": "root"})
    assert (cfg["host"], cfg["port"]) == ("bmc1.lab", 443)


@pytest.mark.parametrize("raw", [
    {"host": ""},
    {"host": "10.0.0.1/redfish"},
    {"host": "10.0.0.1; rm -rf /"},
    {"host": "10.0.0.1", "protocol": "telnet"},
    {"host": "10.0.0.1", "port": 70000},
])
def test_normalize_rejects_bad_input(raw):
    with pytest.raises(bmc.BmcError):
        bmc.normalize_config(raw, {"user": "admin"})


def test_normalize_requires_user():
    with pytest.raises(bmc.BmcError):
        bmc.normalize_config({"host": "10.0.0.1"}, {})


# --- IPMI -------------------------------------------------------------------

def test_ipmi_password_via_env_not_argv(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return types.SimpleNamespace(returncode=0, stdout="Chassis Power is off", stderr="")

    monkeypatch.setattr(bmc.subprocess, "run", fake_run)
    cfg = bmc.normalize_config({"host": "10.0.99.51"}, {"user": "admin", "password": "s3cret"})
    info = bmc.test_connection(cfg)
    assert info["powerState"] == "Off"
    assert "s3cret" not in seen["cmd"] and "-P" not in seen["cmd"]
    assert "-E" in seen["cmd"] and seen["env"]["IPMI_PASSWORD"] == "s3cret"


def test_ipmi_auth_error_is_friendly(monkeypatch):
    monkeypatch.setattr(bmc.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=1, stdout="", stderr="Error: Unable to establish IPMI v2 / RMCP+ session\nRAKP 2 message indicates an error : unauthorized name"))
    cfg = bmc.normalize_config({"host": "10.0.99.51"}, {"user": "admin"})
    with pytest.raises(bmc.BmcError, match="authentication"):
        bmc.test_connection(cfg)


def test_ipmi_power_on_skips_when_already_on(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[-1])
        return types.SimpleNamespace(returncode=0, stdout="Chassis Power is on", stderr="")

    monkeypatch.setattr(bmc.subprocess, "run", fake_run)
    cfg = bmc.normalize_config({"host": "10.0.99.51"}, {"user": "admin"})
    assert bmc.power_on(cfg) == "already on"
    assert calls == ["status"]


# --- Redfish ----------------------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode() if body is not None else b""

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _redfish_server(power_state, allowable=None):
    posted = []
    system = {"PowerState": power_state, "Manufacturer": "Dell Inc.", "Model": "R650",
              "Actions": {"#ComputerSystem.Reset": {
                  "target": "/redfish/v1/Systems/S1/Actions/ComputerSystem.Reset",
                  **({"ResetType@Redfish.AllowableValues": allowable} if allowable else {})}}}

    def urlopen(req, timeout=None, context=None):
        path = req.full_url.split(":443", 1)[1]
        if req.get_method() == "POST":
            posted.append((path, json.loads(req.data.decode())))
            return _Resp(None)
        if path == "/redfish/v1/":
            assert "Authorization" not in req.headers
            return _Resp({"RedfishVersion": "1.11.0"})
        assert req.headers["Authorization"].startswith("Basic ")
        if path == "/redfish/v1/Systems":
            return _Resp({"Members": [{"@odata.id": "/redfish/v1/Systems/S1"}]})
        return _Resp(system)

    return urlopen, posted


def test_redfish_test_connection(monkeypatch):
    urlopen, _ = _redfish_server("Off")
    monkeypatch.setattr(bmc.urllib.request, "urlopen", urlopen)
    cfg = bmc.normalize_config({"protocol": "redfish", "host": "10.0.99.52"}, {"user": "root", "password": "x"})
    info = bmc.test_connection(cfg)
    assert info["powerState"] == "Off" and info["manufacturer"] == "Dell Inc."
    assert "R650" in info["detail"]


def test_redfish_power_on_posts_reset(monkeypatch):
    urlopen, posted = _redfish_server("Off", allowable=["PushPowerButton", "ForceOff"])
    monkeypatch.setattr(bmc.urllib.request, "urlopen", urlopen)
    cfg = bmc.normalize_config({"protocol": "redfish", "host": "10.0.99.52"}, {"user": "root"})
    bmc.power_on(cfg)
    assert posted == [("/redfish/v1/Systems/S1/Actions/ComputerSystem.Reset", {"ResetType": "PushPowerButton"})]


def test_redfish_power_on_noop_when_on(monkeypatch):
    urlopen, posted = _redfish_server("On")
    monkeypatch.setattr(bmc.urllib.request, "urlopen", urlopen)
    cfg = bmc.normalize_config({"protocol": "redfish", "host": "10.0.99.52"}, {"user": "root"})
    assert bmc.power_on(cfg) == "already on" and posted == []


def test_redfish_401_is_friendly(monkeypatch):
    def urlopen(req, timeout=None, context=None):
        raise bmc.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(bmc.urllib.request, "urlopen", urlopen)
    cfg = bmc.normalize_config({"protocol": "redfish", "host": "10.0.99.52"}, {"user": "root"})
    with pytest.raises(bmc.BmcError, match="authentication failed"):
        bmc.test_connection(cfg)


# --- config resolution --------------------------------------------------------

def test_bmc_for_node_merges_file_and_override(tmp_path, monkeypatch):
    f = tmp_path / "bmc.json"
    f.write_text(json.dumps({
        "defaults": {"user": "admin", "password": "default-pw"},
        "nodes": {"hv1": {"protocol": "redfish", "bmcIp": "10.0.99.51", "password": "node-pw"}},
    }))
    monkeypatch.setattr(main, "BMC_CONFIG_FILE", str(f))
    cfg = main._bmc_for_node("hv1")
    assert (cfg["protocol"], cfg["host"], cfg["password"]) == ("redfish", "10.0.99.51", "node-pw")
    # Blank form fields never wipe stored values; a new host wins over bmcIp.
    cfg = main._bmc_for_node("hv1", {"host": "10.0.99.60", "password": "", "user": None})
    assert (cfg["host"], cfg["password"], cfg["user"]) == ("10.0.99.60", "node-pw", "admin")
    with pytest.raises(bmc.BmcError):
        main._bmc_for_node("unknown-node")


# --- token header --------------------------------------------------------------

def test_verify_token_accepts_custom_header(monkeypatch):
    monkeypatch.setattr(main, "AUTH_TOKEN_FILE", None)
    monkeypatch.setattr(main, "AUTH_TOKEN", "tok")
    req = types.SimpleNamespace(headers={"x-node-shutdown-token": "tok"})
    assert main.verify_token(req, None) == "tok"
    bad = types.SimpleNamespace(headers={"x-node-shutdown-token": "nope"})
    with pytest.raises(main.HTTPException) as e:
        main.verify_token(bad, None)
    assert e.value.status_code == 401
    with pytest.raises(main.HTTPException):
        main.verify_token(types.SimpleNamespace(headers={"x-node-shutdown-token": "tök"}), None)


# --- kubectl VM control -------------------------------------------------------

def _kubectl_fake(vm):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = json.dumps(vm) if cmd[1] == "get" else ""
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    return run, calls


def test_start_vm_via_kubectl_restores_run_strategy(monkeypatch):
    vm = {"metadata": {"annotations": {main.PREV_RUN_STRATEGY_ANNOTATION: "RerunOnFailure"}},
          "spec": {"runStrategy": "Halted"}}
    run, calls = _kubectl_fake(vm)
    monkeypatch.setattr(main, "VM_CONTROL", "kubectl")
    monkeypatch.setattr(main.shutil, "which", lambda b: "/usr/local/bin/kubectl")
    monkeypatch.setattr(main.subprocess, "run", run)

    assert main._start_virtual_machines(["prod/web-01", "a/b/c"]) == 1
    patch_cmd = calls[-1]
    assert patch_cmd[:6] == ["kubectl", "patch", "virtualmachines.kubevirt.io", "web-01", "-n", "prod"]
    patch = json.loads(patch_cmd[patch_cmd.index("-p") + 1])
    assert patch == {"spec": {"runStrategy": "RerunOnFailure"},
                     "metadata": {"annotations": {main.PREV_RUN_STRATEGY_ANNOTATION: None}}}


def test_stop_patch_remembers_run_strategy():
    patch = main._vm_stop_patch({"spec": {"runStrategy": "RerunOnFailure"}})
    assert patch["spec"] == {"runStrategy": "Halted"}
    assert patch["metadata"]["annotations"][main.PREV_RUN_STRATEGY_ANNOTATION] == "RerunOnFailure"
    assert main._vm_stop_patch({"spec": {"running": True}}) == {"spec": {"running": False}}
    assert main._vm_start_patch({"spec": {"running": False}}) == {"spec": {"running": True}}


def test_kubectl_not_found_maps_to_404(monkeypatch):
    monkeypatch.setattr(main.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=1, stdout="", stderr='Error from server (NotFound): virtualmachines.kubevirt.io "x" not found'))
    with pytest.raises(main.VmNotFound) as e:
        main._kubectl(["get", "vm", "x"])
    assert e.value.status == 404


# --- power-on sequence ----------------------------------------------------------

def test_poweron_sequence_reports_failures(monkeypatch):
    monkeypatch.setattr(main, "BMC_CONFIG_FILE", "/nonexistent")
    monkeypatch.setattr(main.bmc, "power_on", mock.Mock(return_value="sent"))
    started = []
    monkeypatch.setattr(main, "_start_virtual_machines", lambda vms: started.extend(vms) or len(vms))
    main.run_poweron_sequence(
        ["hv1", "hv2"], ["default/vm1"],
        {"hv1": {"host": "10.0.99.51", "user": "admin"}}, {},
        wait_for_ready=False, holds_lock=False)
    assert main.bmc.power_on.call_count == 1          # hv2 has no BMC config
    assert set(main._poweron_status["failedNodes"]) == {"hv2"}
    assert main._poweron_status["state"] == "error"
    assert started == ["default/vm1"]


def test_stop_virtual_machines_node_independent(monkeypatch):
    run, calls = _kubectl_fake({"spec": {"runStrategy": "RerunOnFailure"}})
    monkeypatch.setattr(main, "VM_CONTROL", "kubectl")
    monkeypatch.setattr(main.shutil, "which", lambda b: "/usr/local/bin/kubectl")
    monkeypatch.setattr(main.subprocess, "run", run)
    assert main._stop_virtual_machines(["prod/db-01"]) == 1
    patch = json.loads(calls[-1][calls[-1].index("-p") + 1])
    assert patch["spec"] == {"runStrategy": "Halted"}
