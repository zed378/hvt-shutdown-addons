"""Nightly node rotation (power off N nodes in turn) and returning VMs to their home node.

Flow per round, driven by three CronJobs:
  drain     pick nodes, cordon, move VMs out (recording each VM's home node),
            power a node off as soon as it is empty
  deadline  stop VMs that have not left yet, power off the selected nodes still up
  poweron   BMC power-on, wait Ready, uncordon, move VMs back home

State lives in a ConfigMap so a pod restart mid-round loses nothing; the deadline
cron still guarantees the selected nodes go off.
"""
import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone

from kubernetes import client
from kubernetes.utils import parse_quantity

logger = logging.getLogger("node-shutdown")

ANN = "node-shutdown.harvesterhci.io/"
HOME_NODE = ANN + "home-node"
HOME_REASON = ANN + "home-reason"          # migrated | stopped | gpu-detached
DETACHED_DEVICES = ANN + "detached-devices"
CORDONED_BY = ANN + "cordoned-by"
CP_LABEL = "node-role.kubernetes.io/control-plane"
HOSTNAME = "kubernetes.io/hostname"

STATE_NAME = "node-shutdown-rotation"
STATE_KEY = "state.json"
HISTORY_MAX = 30
POLL_SECONDS = 15
DRAIN_MAX_SECONDS = 4 * 3600

DEFAULTS = {
    "count": 1, "exclude": [], "neverOff": [], "maxControlPlaneOff": 1, "maxNodesOff": 3,
    "headroomPercent": 15, "pinnedVms": "move", "poweronTimeoutSeconds": 900,
    "stopVms": [], "scheduledNodes": [], "scheduledStopVms": [],
}
RETURN_DEFAULTS = {"enabled": True, "maxParallel": 2, "timeoutSeconds": 900}

_state_lock = threading.Lock()
_round_lock = threading.Lock()


class RotationError(Exception):
    """A round could not run; the message is shown to the operator."""


def _m():
    # app.main imports this module; import it lazily to avoid the cycle.
    from app import main
    return main


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _namespace() -> str:
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            return f.read().strip()
    except OSError:
        return "harvester-system"


def config(raw: dict) -> dict:
    cfg = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if v is not None}}
    cfg["count"] = max(0, min(3, int(cfg["count"])))
    cfg["returnHome"] = {**RETURN_DEFAULTS, **((raw or {}).get("returnHome") or {})}
    return cfg


# ---------------------------------------------------------------------------
# State (ConfigMap)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        cm = _m().k8s_core.read_namespaced_config_map(STATE_NAME, _namespace())
        st = json.loads((cm.data or {}).get(STATE_KEY) or "{}")
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
        st = {}
    st.setdefault("round", None)
    st.setdefault("nodes", {})
    st.setdefault("history", [])
    return st


def save_state(st: dict):
    core, ns = _m().k8s_core, _namespace()
    body = client.V1ConfigMap(metadata=client.V1ObjectMeta(name=STATE_NAME, namespace=ns),
                              data={STATE_KEY: json.dumps(st, indent=1)})
    try:
        core.replace_namespaced_config_map(STATE_NAME, ns, body)
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
        core.create_namespaced_config_map(ns, body)


def update_state(fn):
    """Read-modify-write the state under a lock; returns fn's result."""
    with _state_lock:
        st = load_state()
        out = fn(st)
        save_state(st)
        return out


def _round_node(st: dict, name: str) -> dict:
    for n in (st.get("round") or {}).get("nodes", []):
        if n["name"] == name:
            return n
    return {}


def _finish_round(st: dict, phase: str, result: str, message: str = ""):
    rnd = st.get("round")
    if not rnd:
        return
    rnd.update(phase=phase, result=result, message=message or rnd.get("message", ""), endedAt=_now())
    st["history"] = ([rnd] + st["history"])[:HISTORY_MAX]
    st["round"] = None


# ---------------------------------------------------------------------------
# Alerts (app/alerts.py) + Kubernetes Events
# ---------------------------------------------------------------------------

def alert(title: str, lines: list, severity: str = "warning", node: str = None):
    logger.warning(f"Rotation alert: {title} — {'; '.join(lines)}")
    try:
        from app import alerts
        alerts.send(title, lines, severity)
    except Exception as e:
        logger.error(f"Alert delivery failed: {e}")
    try:
        ns = _namespace()
        _m().k8s_core.create_namespaced_event(ns, client.CoreV1Event(
            metadata=client.V1ObjectMeta(generate_name="node-shutdown-rotation-", namespace=ns),
            involved_object=client.V1ObjectReference(kind="Node", name=node or "", api_version="v1"),
            reason="NodeRotation", message=f"{title}: {'; '.join(lines)}"[:1000],
            type="Warning" if severity != "info" else "Normal",
            first_timestamp=datetime.now(timezone.utc), last_timestamp=datetime.now(timezone.utc),
            count=1, source=client.V1EventSource(component="node-shutdown")))
    except Exception as e:
        logger.debug(f"Event not recorded: {e}")


# ---------------------------------------------------------------------------
# Cluster inventory
# ---------------------------------------------------------------------------

def _qty(v) -> float:
    try:
        return float(parse_quantity(v)) if v not in (None, "") else 0.0
    except Exception:
        return 0.0


def _ready(node) -> bool:
    return any(c.type == "Ready" and c.status == "True" for c in (node.status.conditions or []))


def _vm_spec(vm: dict) -> dict:
    return vm.get("spec", {}).get("template", {}).get("spec", {})


GPU_PREFIXES = ("nvidia.com/", "amd.com/")


def _passthrough(vm: dict):
    """'gpu' when every passed-through device is a GPU (safe to detach), 'device' when
    another device is passed through (e.g. a NIC: the VM is tied to its node), else None."""
    devices = _vm_spec(vm).get("domain", {}).get("devices", {})
    gpus, host = devices.get("gpus") or [], devices.get("hostDevices") or []
    if any(not str(d.get("deviceName", "")).startswith(GPU_PREFIXES) for d in host):
        return "device"
    return "gpu" if gpus or host else None


def _allowed_hosts(vm: dict):
    """Hostnames a VM is restricted to (None = not restricted by hostname)."""
    spec = _vm_spec(vm)
    hosts = None
    sel = (spec.get("nodeSelector") or {}).get(HOSTNAME)
    if sel:
        hosts = {sel}
    terms = (((spec.get("affinity") or {}).get("nodeAffinity") or {})
             .get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms") or []
    for term in terms:
        for ex in term.get("matchExpressions") or []:
            if ex.get("key") == HOSTNAME and ex.get("operator") == "In":
                vals = set(ex.get("values") or [])
                hosts = vals if hosts is None else hosts & vals
    return hosts


def inventory() -> dict:
    """Nodes, VMs per node and free capacity, read once per decision."""
    m = _m()
    custom = client.CustomObjectsApi()
    nodes = {}
    for n in m.k8s_core.list_node().items:
        alloc = n.status.allocatable or {}
        nodes[n.metadata.name] = {
            "name": n.metadata.name,
            "ready": _ready(n),
            "unschedulable": bool(n.spec.unschedulable),
            "controlPlane": (n.metadata.labels or {}).get(CP_LABEL) == "true",
            "cordonedBy": (n.metadata.annotations or {}).get(CORDONED_BY),
            "memAlloc": _qty(alloc.get("memory")), "cpuAlloc": _qty(alloc.get("cpu")) * 1000,
            "memUsed": 0.0, "cpuUsed": 0.0, "vms": [],
        }
    launchers = {}
    for p in m.k8s_core.list_pod_for_all_namespaces(field_selector="status.phase!=Succeeded,status.phase!=Failed").items:
        node = nodes.get(p.spec.node_name)
        if not node:
            continue
        mem = cpu = 0.0
        for c in p.spec.containers or []:
            req = (c.resources.requests if c.resources else None) or {}
            mem += _qty(req.get("memory"))
            cpu += _qty(req.get("cpu")) * 1000
        node["memUsed"] += mem
        node["cpuUsed"] += cpu
        if (p.metadata.labels or {}).get("kubevirt.io") == "virt-launcher":
            uid = (p.metadata.labels or {}).get("kubevirt.io/created-by")
            launchers[uid] = (mem, cpu)
    for vmi in custom.list_cluster_custom_object("kubevirt.io", "v1", "virtualmachineinstances").get("items", []):
        node = nodes.get(vmi.get("status", {}).get("nodeName"))
        if not node:
            continue
        meta = vmi["metadata"]
        ref = f"{meta['namespace']}/{meta['name']}"
        try:
            vm = m._get_vm(meta["namespace"], meta["name"])
        except Exception:
            vm = {"spec": {"template": {"spec": vmi.get("spec", {})}}}
        mem, cpu = launchers.get(meta.get("uid"), (0.0, 0.0))
        hosts = _allowed_hosts(vm)
        kind = _passthrough(vm)
        # Harvester pins a passthrough VM to its device's node; for a GPU VM that
        # pin is dropped together with the GPU, so it does not count as pinned.
        pinned = kind is None and hosts is not None and not (hosts - {node["name"]})
        node["vms"].append({"ref": ref, "gpu": kind == "gpu", "device": kind == "device",
                            "mem": mem, "cpu": cpu, "pinned": pinned})
    return nodes


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan(raw_cfg: dict, inv: dict = None, st: dict = None) -> dict:
    """Pick the nodes for one round. Pure decision; no side effects."""
    cfg = config(raw_cfg)
    inv = inv if inv is not None else inventory()
    st = st if st is not None else load_state()
    headroom = 1 - max(0, min(90, float(cfg["headroomPercent"]))) / 100
    # VMs the operator stops for the night (rotation.stopVms or their own VM
    # shutdown schedule) neither need room elsewhere nor keep their node busy.
    stopping = set(cfg["stopVms"]) | set(cfg["scheduledStopVms"])
    rows, eligible = [], []
    for name in sorted(inv):
        n = inv[name]
        info = st["nodes"].get(name, {})
        reason = ""
        if name in cfg["exclude"]:
            reason = "dikecualikan"
        elif name in cfg["neverOff"]:
            reason = "tidak pernah mati"
        elif not n["ready"]:
            reason = "tidak Ready"
        elif n["unschedulable"]:
            reason = "sudah cordoned"
        elif info.get("status") == "failed":
            reason = "gagal power-on sebelumnya"
        elif name in cfg["scheduledNodes"]:
            reason = "punya jadwal per-node"
        elif any(v["device"] and v["ref"] not in stopping for v in n["vms"]):
            reason = "VM passthrough non-GPU: " + ", ".join(v["ref"] for v in n["vms"] if v["device"])
        elif cfg["pinnedVms"] == "skipNode" and any(v["pinned"] for v in n["vms"]):
            reason = "VM di-pin: " + ", ".join(v["ref"] for v in n["vms"] if v["pinned"])
        rows.append({"name": name, "controlPlane": n["controlPlane"], "gpu": any(v["gpu"] for v in n["vms"]),
                     "lastOffAt": info.get("lastOffAt"), "status": info.get("status", "ok"),
                     "eligible": not reason, "reason": reason})
        if not reason:
            eligible.append(name)
    # Least recently off first (by day, never-off nodes first), then the node
    # that has been off the fewest times, then name.
    def turn(x):
        info = st["nodes"].get(x, {})
        return ((info.get("lastOffAt") or "")[:10], info.get("offCount", 0), x)
    eligible.sort(key=turn)

    down = [x for x, n in inv.items() if not n["ready"] or n["unschedulable"]]
    budget = min(cfg["count"], max(0, int(cfg["maxNodesOff"]) - len(down)))
    cp_budget = int(cfg["maxControlPlaneOff"]) - sum(1 for x in down if inv[x]["controlPlane"])
    skipped = {r["name"]: r["reason"] for r in rows if r["reason"]}
    selected = []
    for name in eligible:
        if len(selected) >= budget:
            skipped.setdefault(name, "kuota putaran penuh")
            continue
        if inv[name]["controlPlane"]:
            if cp_budget <= 0:
                skipped[name] = "batas control-plane mati tercapai"
                continue
            cp_budget -= 1
        selected.append(name)

    def capacity(sel):
        moving = [v for x in sel for v in inv[x]["vms"]
                  if v["ref"] not in stopping and not (v["pinned"] and cfg["pinnedVms"] == "stop")]
        survivors = [n for x, n in inv.items() if x not in sel and n["ready"] and not n["unschedulable"]]

        def freed(n, key):
            return sum(v[key] for v in n["vms"] if v["ref"] in stopping)
        cap = {
            "memoryNeeded": sum(v["mem"] for v in moving), "cpuNeeded": sum(v["cpu"] for v in moving),
            "memoryFree": sum(max(0.0, n["memAlloc"] * headroom - n["memUsed"] + freed(n, "mem")) for n in survivors),
            "cpuFree": sum(max(0.0, n["cpuAlloc"] * headroom - n["cpuUsed"] + freed(n, "cpu")) for n in survivors),
        }
        cap["fits"] = cap["memoryNeeded"] <= cap["memoryFree"] and cap["cpuNeeded"] <= cap["cpuFree"]
        return cap

    reduced = False
    cap = capacity(selected)
    while selected and not cap["fits"]:
        skipped[selected.pop()] = "kapasitas node lain tidak cukup"
        reduced = True
        cap = capacity(selected)
    cap = {k: (int(v) if isinstance(v, float) else v) for k, v in cap.items()}
    for r in rows:
        if r["name"] in skipped and not r["reason"]:
            r["reason"] = skipped[r["name"]]
    return {"selected": selected, "skipped": skipped, "reduced": reduced, "capacity": cap, "nodes": rows}


# ---------------------------------------------------------------------------
# Node + VM actions
# ---------------------------------------------------------------------------

def cordon(name: str):
    _m().k8s_core.patch_node(name, {"metadata": {"annotations": {CORDONED_BY: "rotation"}},
                                    "spec": {"unschedulable": True}})


def uncordon_if_ours(name: str) -> bool:
    core = _m().k8s_core
    node = core.read_node(name)
    if (node.metadata.annotations or {}).get(CORDONED_BY) != "rotation":
        return False
    core.patch_node(name, {"metadata": {"annotations": {CORDONED_BY: None}},
                           "spec": {"unschedulable": False}})
    return True


def _peer_ips() -> dict:
    pods = _m().k8s_core.list_namespaced_pod(_namespace(), label_selector="app=node-shutdown").items
    return {p.spec.node_name: (p.status.pod_ip or p.status.host_ip) for p in pods
            if p.status.phase == "Running" and (p.status.pod_ip or p.status.host_ip)}


def _peer_post(ip: str, path: str, body: dict, timeout: int = 30) -> dict:
    m = _m()
    req = urllib.request.Request(
        f"{m.PEER_SCHEME}://{ip}:{m.NODE_PORT}{path}", method="POST", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {m._current_token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=m._peer_ssl_context()) as res:
        return json.loads(res.read().decode() or "{}")


def poweroff_node(name: str):
    """Ask the node's own pod to stop its remaining VMs and power the host off."""
    ip = _peer_ips().get(name)
    if not ip:
        raise RotationError(f"no node-shutdown pod running on {name}")
    _peer_post(ip, "/system/shutdown?all_nodes=false", {"vmStrategy": "stop", "vms": [], "poweroff": True})


def _annotate(ref: str, ann: dict):
    ns, _, name = ref.partition("/")
    _m()._patch_vm(ns, name, {"metadata": {"annotations": ann}})


def _create_migration(ref: str, home: str = None) -> str:
    m = _m()
    ns, _, name = ref.partition("/")
    spec = {"vmiName": name}
    if home:
        spec["addedNodeSelector"] = {HOSTNAME: home}
    obj = {"apiVersion": "kubevirt.io/v1", "kind": "VirtualMachineInstanceMigration",
           "metadata": {"generateName": f"{'home' if home else 'rotate'}-{name}-"[:50], "namespace": ns},
           "spec": spec}
    created = client.CustomObjectsApi().create_namespaced_custom_object(
        "kubevirt.io", "v1", ns, "virtualmachineinstancemigrations", obj)
    return created["metadata"]["name"]


def _migration_phase(ns: str, mig: str) -> str:
    try:
        obj = client.CustomObjectsApi().get_namespaced_custom_object(
            "kubevirt.io", "v1", ns, "virtualmachineinstancemigrations", mig)
        return obj.get("status", {}).get("phase", "")
    except Exception:
        return ""


def _vmi_node(ref: str):
    ns, _, name = ref.partition("/")
    try:
        vmi = client.CustomObjectsApi().get_namespaced_custom_object(
            "kubevirt.io", "v1", ns, "virtualmachineinstances", name)
        return vmi.get("status", {}).get("nodeName")
    except Exception:
        return None


def _stop_and_wait(ref: str) -> str:
    m = _m()
    ns, _, name = ref.partition("/")
    m._patch_vm(ns, name, m._vm_stop_patch(m._get_vm(ns, name)))
    return m._wait_vms([ref], False, max(m.VM_SHUTDOWN_TIMEOUT, 60)).get(ref, "")


def _start_and_wait(ref: str) -> str:
    m = _m()
    ns, _, name = ref.partition("/")
    m._patch_vm(ns, name, m._vm_start_patch(m._get_vm(ns, name)))
    return m._wait_vms([ref], True, m.VM_START_TIMEOUT).get(ref, "")


def _without_hostname(node_affinity: dict):
    """nodeAffinity with its kubernetes.io/hostname requirements removed (None if nothing is left)."""
    req = (node_affinity or {}).get("requiredDuringSchedulingIgnoredDuringExecution") or {}
    terms = []
    for term in req.get("nodeSelectorTerms") or []:
        exprs = [e for e in term.get("matchExpressions") or [] if e.get("key") != HOSTNAME]
        if exprs or term.get("matchFields"):
            terms.append({**term, "matchExpressions": exprs})
    return {**node_affinity, "requiredDuringSchedulingIgnoredDuringExecution":
            {"nodeSelectorTerms": terms} if terms else None}


def _apply_placement(ref: str, saved: dict, restore: bool):
    """Put back (restore) or drop the node pin and, for GPU VMs, the GPUs."""
    ns, _, name = ref.partition("/")
    spec = {"nodeSelector": {HOSTNAME: saved.get("host") if restore else None}}
    if saved.get("nodeAffinity") is not None:
        spec["affinity"] = {"nodeAffinity": saved["nodeAffinity"] if restore
                            else _without_hostname(saved["nodeAffinity"])}
    if saved.get("gpus") or saved.get("hostDevices"):
        spec["domain"] = {"devices": {"gpus": (saved.get("gpus") or None) if restore else None,
                                      "hostDevices": (saved.get("hostDevices") or None) if restore else None}}
    _m()._patch_vm(ns, name, {"spec": {"template": {"spec": spec}}})


def cold_move(ref: str, home: str, drop_gpu: bool) -> str:
    """Move a VM that cannot live-migrate off ``home``: stop it, drop its node pin
    (and its GPUs), start it elsewhere. What was dropped is saved on the VM and put
    back when it returns home. Returns '' or an error."""
    m = _m()
    ns, _, name = ref.partition("/")
    spec = _vm_spec(m._get_vm(ns, name))
    devices = spec.get("domain", {}).get("devices", {})
    affinity = (spec.get("affinity") or {}).get("nodeAffinity")
    saved = {"host": (spec.get("nodeSelector") or {}).get(HOSTNAME),
             "nodeAffinity": affinity if affinity and _allowed_hosts({"spec": {"template": {"spec": {
                 "affinity": {"nodeAffinity": affinity}}}}}) is not None else None}
    if drop_gpu:
        saved.update(gpus=devices.get("gpus") or [], hostDevices=devices.get("hostDevices") or [])
    _annotate(ref, {HOME_NODE: home, HOME_REASON: "cold", DETACHED_DEVICES: json.dumps(saved)})
    err = _stop_and_wait(ref)
    if err:
        return f"stop failed: {err}"
    _apply_placement(ref, saved, restore=False)
    err = _start_and_wait(ref)
    return f"start on another node failed: {err}" if err else ""


def move_out(node: str, vm: dict, pinned_mode: str) -> str:
    """Start moving one VM off ``node``; returns its bucket: migrated | stopped | coldMoved."""
    ref = vm["ref"]
    if vm["gpu"] or (vm["pinned"] and pinned_mode == "move"):
        err = cold_move(ref, node, drop_gpu=vm["gpu"])
        if err:
            raise RotationError(err)
        return "coldMoved"
    if vm["pinned"] and pinned_mode == "stop":
        _annotate(ref, {HOME_NODE: node, HOME_REASON: "stopped"})
        ns, _, name = ref.partition("/")
        _m()._stop_vm(ns, name)
        return "stopped"
    _annotate(ref, {HOME_NODE: node, HOME_REASON: "migrated"})
    _create_migration(ref)
    return "migrated"


def mark_home(node: str, target_vms: list = None, reason: str = "migrated"):
    """Record ``node`` as the home of the VMs running there (used by per-node schedules)."""
    for v in inventory().get(node, {}).get("vms", []):
        if target_vms and v["ref"] not in target_vms:
            continue
        try:
            _annotate(v["ref"], {HOME_NODE: node, HOME_REASON: reason})
        except Exception as e:
            logger.warning(f"Could not record home node of {v['ref']}: {e}")


# ---------------------------------------------------------------------------
# Return home
# ---------------------------------------------------------------------------

def _clear_home(ref: str):
    _annotate(ref, {HOME_NODE: None, HOME_REASON: None, DETACHED_DEVICES: None})


def _vms_with_home(node: str) -> list:
    out = []
    for vm in client.CustomObjectsApi().list_cluster_custom_object(
            "kubevirt.io", "v1", "virtualmachines").get("items", []):
        ann = vm["metadata"].get("annotations") or {}
        if ann.get(HOME_NODE) == node:
            out.append((f"{vm['metadata']['namespace']}/{vm['metadata']['name']}", ann))
    return out


def _migrate_home(refs: list, node: str, timeout_s: int, parallel: int) -> dict:
    """Live-migrate refs to ``node`` in batches of ``parallel``; returns {ref: reason} failures."""
    failures = {}
    for i in range(0, len(refs), max(1, parallel)):
        batch, running = refs[i:i + max(1, parallel)], {}
        for ref in batch:
            try:
                running[ref] = _create_migration(ref, home=node)
            except Exception as e:
                failures[ref] = f"migration not created: {e}"
        deadline = time.time() + timeout_s
        while running and time.time() < deadline:
            for ref, mig in list(running.items()):
                phase = _migration_phase(ref.partition("/")[0], mig)
                if phase == "Succeeded":
                    running.pop(ref)
                elif phase == "Failed":
                    failures[ref] = "migration failed"
                    running.pop(ref)
            if running:
                time.sleep(5)
        for ref in running:
            failures[ref] = f"migration not finished after {timeout_s}s"
    return failures


def return_home(node: str, raw_cfg: dict = None) -> dict:
    """Bring every VM whose home is ``node`` back to it. Never retries a failure."""
    rh = {**RETURN_DEFAULTS, **(raw_cfg or {})}
    returned, failed, to_migrate = [], {}, []
    for ref, ann in _vms_with_home(node):
        reason = ann.get(HOME_REASON, "migrated")
        try:
            if reason in ("cold", "gpu-detached"):
                err = _restore_cold(ref, ann)
                if err:
                    failed[ref] = err
                else:
                    _clear_home(ref)
                    returned.append(ref)
                continue
            if reason == "stopped":
                err = _start_and_wait(ref)
                if err:
                    failed[ref] = f"start failed: {err}"
                    continue
            current = _vmi_node(ref)
            if current is None and reason == "scheduled":
                _clear_home(ref)        # stopped by its own schedule; that schedule starts it
            elif current is None:
                failed[ref] = "VM is not running"
            elif current == node:
                _clear_home(ref)
                returned.append(ref)
            else:
                to_migrate.append(ref)
        except Exception as e:
            failed[ref] = str(e)
    mig_failed = _migrate_home(to_migrate, node, int(rh["timeoutSeconds"]), int(rh["maxParallel"]))
    for ref in to_migrate:
        if ref in mig_failed:
            failed[ref] = mig_failed[ref]
        elif _vmi_node(ref) == node:
            _clear_home(ref)
            returned.append(ref)
        else:
            failed[ref] = "migration finished but VM is not on its home node"
    if failed:
        alert(f"VM gagal kembali ke {node}", [f"{r}: {why}" for r, why in failed.items()], node=node)
    logger.info(f"Return home to {node}: returned={returned} failed={failed}")
    return {"node": node, "returned": returned, "failed": failed}


def _restore_cold(ref: str, ann: dict) -> str:
    """Stop, put the node pin (and GPUs) back, start on the home node."""
    try:
        saved = json.loads(ann.get(DETACHED_DEVICES) or "{}")
    except ValueError:
        saved = {}
    err = _stop_and_wait(ref)
    if err:
        return f"stop failed: {err}"
    _apply_placement(ref, saved, restore=True)
    err = _start_and_wait(ref)
    if not err:
        return ""
    # Could not start pinned/with GPU: run it unpinned again and keep the
    # annotation so the next return-home tries again.
    _stop_and_wait(ref)
    _apply_placement(ref, saved, restore=False)
    again = _start_and_wait(ref)
    return f"pin/GPU not restored ({err}); VM {'running elsewhere' if not again else 'not running: ' + again}"


# ---------------------------------------------------------------------------
# Round phases
# ---------------------------------------------------------------------------

def drain(raw_cfg: dict, forced: list = None) -> dict:
    """Start a round: plan, cordon, move VMs out. Returns the plan; raises RotationError when cancelled."""
    m = _m()
    cfg = config(raw_cfg)
    with _round_lock:
        st = load_state()
        if st["round"] and st["round"].get("phase") in ("draining", "off", "poweringOn"):
            raise RotationError(f"round {st['round']['id']} is still {st['round']['phase']}")
        inv = inventory()
        p = plan(cfg, inv, st)
        if forced:
            p["selected"] = [x for x in forced if x in inv]
        if not p["selected"]:
            msg = "no node can be powered off: " + "; ".join(f"{k}: {v}" for k, v in p["skipped"].items())
            st["round"] = {"id": datetime.now(timezone.utc).date().isoformat(), "startedAt": _now(),
                           "phase": "cancelled", "nodes": []}
            _finish_round(st, "cancelled", "cancelled", msg)
            save_state(st)
            alert("Rotasi dibatalkan", [msg])
            raise RotationError(msg)
        if p["reduced"]:
            alert("Rotasi dikurangi karena kapasitas", [f"dipilih: {', '.join(p['selected'])}",
                  f"memori perlu {p['capacity']['memoryNeeded']} / sisa {p['capacity']['memoryFree']}"])
        # Never coordinate from a node that is about to go off.
        if m.NODE_NAME in p["selected"] and not forced:
            ips = _peer_ips()
            peer = next((ip for n, ip in ips.items() if n not in p["selected"]), None)
            if peer:
                logger.info(f"Forwarding rotation drain to peer {peer}")
                return _peer_post(peer, "/system/rotation/drain",
                                  {**(raw_cfg or {}), "forcedSelection": p["selected"]}, timeout=60)
        st["round"] = {"id": datetime.now(timezone.utc).date().isoformat(), "startedAt": _now(),
                       "phase": "draining", "result": None, "message": "", "stoppedVms": [],
                       "nodes": [{"name": x, "phase": "draining", "offAt": None, "onAt": None, "error": "",
                                  "migrated": [], "stopped": [], "coldMoved": [], "returned": [],
                                  "returnFailed": {}} for x in p["selected"]]}
        save_state(st)
    for name in p["selected"]:
        cordon(name)
    threading.Thread(target=_drain_loop, args=(cfg, {x: inv[x]["vms"] for x in p["selected"]}),
                     daemon=True, name="rotation-drain").start()
    return p


def _drain_loop(cfg: dict, vms_by_node: dict):
    m = _m()
    # VMs the operator stops for the night: stopped now, started again at power-on.
    stopped = []
    for ref in cfg["stopVms"]:
        ns, _, name = ref.partition("/")
        if m._stop_vm(ns, name):
            stopped.append(ref)
    if stopped:
        update_state(lambda st: (st["round"] or {}).update(stoppedVms=stopped))
    for node, vms in vms_by_node.items():
        buckets = {"migrated": [], "stopped": [], "coldMoved": []}
        errors = []
        for vm in vms:
            if vm["ref"] in cfg["stopVms"]:
                continue
            if vm["ref"] in cfg["scheduledStopVms"]:
                # Its own VM schedule stops it; only remember where it belongs.
                _annotate(vm["ref"], {HOME_NODE: node, HOME_REASON: "scheduled"})
                continue
            try:
                buckets[move_out(node, vm, cfg["pinnedVms"])].append(vm["ref"])
            except Exception as e:
                errors.append(f"{vm['ref']}: {e}")
        update_state(lambda st: _round_node(st, node).update(
            {**buckets, "error": "; ".join(errors)}))
    started = time.time()
    pending = set(vms_by_node)
    while pending and time.time() - started < DRAIN_MAX_SECONDS:
        rnd = load_state().get("round") or {}
        if rnd.get("phase") != "draining":
            return
        inv = inventory()
        for node in sorted(pending):
            if inv.get(node, {}).get("vms"):
                continue
            try:
                poweroff_node(node)
                _mark_off(node)
            except Exception as e:
                logger.error(f"Power-off of drained node {node} failed: {e}")
                continue
            pending.discard(node)
        if pending:
            time.sleep(POLL_SECONDS)
    if not pending:
        update_state(lambda st: (st["round"] or {}).update(phase="off"))


def _mark_off(node: str):
    def fn(st):
        n = _round_node(st, node)
        n.update(phase="off", offAt=_now())
        _record_off(st, node, n["offAt"])
    update_state(fn)


def _record_off(st: dict, node: str, at: str):
    info = st["nodes"].setdefault(node, {})
    info["lastOffAt"] = at
    info["offCount"] = info.get("offCount", 0) + 1
    info.setdefault("status", "ok")


def deadline(raw_cfg: dict) -> dict:
    """Hard stop: power off every selected node still up, stopping its remaining VMs."""
    cfg = config(raw_cfg)
    st = load_state()
    rnd = st.get("round")
    if not rnd or rnd.get("phase") not in ("draining", "off"):
        return {"status": "no active round"}
    inv = inventory()
    errors, stopped_all = {}, []
    for n in rnd["nodes"]:
        if n["phase"] != "draining":
            continue
        name = n["name"]
        left = [v["ref"] for v in inv.get(name, {}).get("vms", [])]
        for ref in left:
            if ref in cfg["scheduledStopVms"]:
                continue
            try:
                _annotate(ref, {HOME_NODE: name, HOME_REASON: "stopped"})
            except Exception as e:
                logger.warning(f"Could not record home node of {ref}: {e}")
        try:
            poweroff_node(name)
            stopped_all += left

            def fn(s, name=name, left=left):
                rn = _round_node(s, name)
                rn["stopped"] = sorted(set(rn.get("stopped", []) + left))
                rn.update(phase="off", offAt=_now())
                _record_off(s, name, rn["offAt"])
            update_state(fn)
        except Exception as e:
            errors[name] = str(e)
            update_state(lambda s, name=name, e=e: _round_node(s, name).update(error=str(e)))
    update_state(lambda s: (s["round"] or {}).update(phase="off"))
    if stopped_all:
        alert("VM di-stop karena tidak sempat migrasi", stopped_all)
    if errors:
        alert("Node gagal dimatikan pada batas waktu", [f"{k}: {v}" for k, v in errors.items()], "critical")
        raise RotationError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return {"status": "off", "stopped": stopped_all}


def poweron(raw_cfg: dict) -> dict:
    """Power the round's nodes (and earlier failed ones) back on and bring their VMs home."""
    m = _m()
    cfg = config(raw_cfg)
    st = load_state()
    rnd = st.get("round") or {}
    targets = [n["name"] for n in rnd.get("nodes", []) if n.get("phase") in ("off", "draining", "failed")]
    targets += [x for x, info in st["nodes"].items() if info.get("status") == "failed" and x not in targets]
    if not targets:
        return {"status": "nothing to power on"}
    if rnd:
        update_state(lambda s: (s["round"] or {}).update(phase="poweringOn"))

    results = {}

    def one(name):
        try:
            m.bmc.power_on(m._bmc_for_node(name))
            if not m._wait_for_node_ready(name, int(cfg["poweronTimeoutSeconds"])):
                raise RotationError(f"not Ready after {cfg['poweronTimeoutSeconds']}s")
            uncordon_if_ours(name)
            home = return_home(name, cfg["returnHome"]) if cfg["returnHome"].get("enabled") else \
                {"returned": [], "failed": {}}
            results[name] = {"ok": True, **home}
        except Exception as e:
            results[name] = {"ok": False, "error": str(e)}

    threads = [threading.Thread(target=one, args=(x,), daemon=True) for x in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    failed_nodes = {x: r["error"] for x, r in results.items() if not r["ok"]}
    start_failed = {}
    for ref in rnd.get("stoppedVms") or []:
        err = _start_and_wait(ref)
        if err:
            start_failed[ref] = err
    if start_failed:
        alert("VM gagal dinyalakan kembali setelah rotasi", [f"{r}: {e}" for r, e in start_failed.items()])

    def fn(s):
        for name, r in results.items():
            info = s["nodes"].setdefault(name, {})
            info["status"] = "ok" if r["ok"] else "failed"
            rn = _round_node(s, name)
            if rn:
                rn.update(phase="done" if r["ok"] else "failed", error=r.get("error", ""),
                          onAt=_now() if r["ok"] else None, returned=r.get("returned", []),
                          returnFailed=r.get("failed", {}))
        if s.get("round"):
            if failed_nodes:
                result = "failed" if len(failed_nodes) == len(results) else "partial"
            else:
                result = "partial" if start_failed or any(r.get("failed") for r in results.values()) else "ok"
            _finish_round(s, "done", result)
    update_state(fn)
    for name, err in failed_nodes.items():
        alert(f"Node {name} gagal hidup", [err, "node lain tetap dinyalakan"], "critical", node=name)
    return_failed = {x: r["failed"] for x, r in results.items() if r.get("failed")}
    if failed_nodes or return_failed or start_failed:
        raise RotationError(json.dumps({"failedNodes": failed_nodes, "returnFailed": return_failed,
                                        "startFailed": start_failed}))
    return {"status": "done", "nodes": results}


def reset(node: str):
    update_state(lambda st: st["nodes"].setdefault(node, {}).update(status="ok"))
