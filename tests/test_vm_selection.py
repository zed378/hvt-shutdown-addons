"""Focused tests for per-VM shutdown selection and VM-only mode.

Self-contained: fastapi/kubernetes are stubbed so this runs with plain
pytest anywhere. Exercises the changed behavior in app/main.py:
  - _normalize_vm_refs never widens a malformed entry
  - stop/migrate touch only selected VMs
  - force-kill is fail-closed (kills nothing it cannot positively map)
  - poweroff=False skips the host poweroff
  - peer calls forward vms + poweroff flag
"""
import json
import sys
import types
from unittest import mock


def _install_stubs():
    fastapi = types.ModuleType('fastapi')

    class HTTPException(Exception):
        def __init__(self, status_code=None, detail=None):
            self.status_code = status_code
            self.detail = detail

    def _decorator(*a, **k):
        def wrap(fn):
            return fn
        return wrap

    class _App:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return _decorator

        def post(self, *a, **k):
            return _decorator

        def exception_handler(self, *a, **k):
            return _decorator

        def middleware(self, *a, **k):
            return _decorator

        def on_event(self, *a, **k):
            return _decorator

    fastapi.FastAPI = _App
    fastapi.HTTPException = HTTPException
    fastapi.Depends = lambda *a, **k: None
    fastapi.Request = object
    fastapi.status = types.SimpleNamespace(
        HTTP_500_INTERNAL_SERVER_ERROR=500,
        HTTP_401_UNAUTHORIZED=401,
        HTTP_429_TOO_MANY_REQUESTS=429,
        HTTP_503_SERVICE_UNAVAILABLE=503,
        HTTP_409_CONFLICT=409,
    )
    responses = types.ModuleType('fastapi.responses')

    class JSONResponse(Exception):
        def __init__(self, status_code=200, content=None):
            self.status_code = status_code
            self.content = content

    responses.JSONResponse = JSONResponse
    responses.FileResponse = object
    security = types.ModuleType('fastapi.security')

    class HTTPBearer:
        def __init__(self, *a, **k):
            pass

    security.HTTPBearer = HTTPBearer
    security.HTTPAuthorizationCredentials = object

    kubernetes = types.ModuleType('kubernetes')
    kubernetes.client = mock.MagicMock(name='client')
    kubernetes.config = mock.MagicMock(name='config')

    sys.modules['fastapi'] = fastapi
    sys.modules['fastapi.responses'] = responses
    sys.modules['fastapi.security'] = security
    sys.modules['kubernetes'] = kubernetes


_install_stubs()

import app.main as main  # noqa: E402


def _vmi(ns, name, node, uid=None):
    return {
        'metadata': {'namespace': ns, 'name': name, 'uid': uid or f'uid-{ns}-{name}'},
        'status': {'nodeName': node},
    }


def _pod(ns, name, created_by=None):
    pod = mock.Mock()
    pod.metadata.name = name
    pod.metadata.namespace = ns
    pod.metadata.labels = {'kubevirt.io/created-by': created_by} if created_by else {}
    return pod


# --- _normalize_vm_refs -----------------------------------------------------

def test_normalize_vm_refs():
    assert main._normalize_vm_refs(['default/web-01', 'db-01', ' mon/agent-01 ']) == [
        'default/db-01', 'default/web-01', 'mon/agent-01']
    # Malformed entries are dropped, never widened. Stray slashes are
    # stripped first, so '/name' safely narrows to 'default/name'.
    assert main._normalize_vm_refs(['', '/', 'a/b/c', None, 123]) == []
    assert main._normalize_vm_refs(['/name', 'ns/']) == ['default/name', 'default/ns']
    assert main._normalize_vm_refs(None) == []
    assert main._normalize_vm_refs(['a/vm', 'a/vm']) == ['a/vm']


# --- stop filter ------------------------------------------------------------

def test_stop_vms_only_selected(monkeypatch):
    main.NODE_NAME = 'node1'
    vmis = [_vmi('default', 'web-01', 'node1'),
            _vmi('default', 'db-01', 'node1'),
            _vmi('default', 'other', 'node2')]
    fake_custom = mock.Mock()
    fake_custom.list_cluster_custom_object.return_value = {'items': vmis}
    patched = []

    def fake_get(*a):
        ns, name = a[3], a[4]
        return {'metadata': {'namespace': ns, 'name': name}, 'spec': {'running': True}}

    fake_custom.get_namespaced_custom_object.side_effect = fake_get
    fake_custom.patch_namespaced_custom_object.side_effect = lambda *a: patched.append((a[2], a[4]))
    monkeypatch.setattr(main.client, 'CustomObjectsApi', lambda: fake_custom)
    monkeypatch.setattr(main, '_wait_for_no_virt_launchers', lambda *a: True)

    main._stop_vms_on_node(['default/web-01'])
    assert patched == [('default', 'web-01')]


def test_stop_vms_no_match_touches_nothing(monkeypatch):
    main.NODE_NAME = 'node1'
    fake_custom = mock.Mock()
    fake_custom.list_cluster_custom_object.return_value = {'items': [_vmi('default', 'web-01', 'node1')]}
    monkeypatch.setattr(main.client, 'CustomObjectsApi', lambda: fake_custom)
    monkeypatch.setattr(main, '_wait_for_no_virt_launchers', lambda *a: True)

    main._stop_vms_on_node(['default/ghost'])
    fake_custom.patch_namespaced_custom_object.assert_not_called()


# --- force-kill fail-closed --------------------------------------------------

def test_force_kill_only_mapped_pods(monkeypatch):
    main.NODE_NAME = 'node1'
    vmis = [_vmi('default', 'web-01', 'node1', uid='uid-1'),
            _vmi('default', 'db-01', 'node1', uid='uid-2')]
    fake_custom = mock.Mock()
    fake_custom.list_cluster_custom_object.return_value = {'items': vmis}
    monkeypatch.setattr(main.client, 'CustomObjectsApi', lambda: fake_custom)
    pods = [_pod('default', 'virt-launcher-web-01', 'uid-1'),
            _pod('default', 'virt-launcher-db-01', 'uid-2'),
            _pod('default', 'virt-launcher-unknown', None)]
    monkeypatch.setattr(main, '_list_virt_launchers_on_node', lambda: pods)
    deleted = []
    main.k8s_core = mock.Mock()
    main.k8s_core.delete_namespaced_pod.side_effect = lambda name, namespace, body: deleted.append(name)

    killed = main._force_kill_vms_on_node(['default/web-01'])
    assert killed == 1
    assert deleted == ['virt-launcher-web-01']


def test_force_kill_no_match_kills_nothing(monkeypatch):
    main.NODE_NAME = 'node1'
    fake_custom = mock.Mock()
    fake_custom.list_cluster_custom_object.return_value = {'items': [_vmi('default', 'web-01', 'node1', uid='uid-1')]}
    monkeypatch.setattr(main.client, 'CustomObjectsApi', lambda: fake_custom)
    monkeypatch.setattr(main, '_list_virt_launchers_on_node',
                        lambda: [_pod('default', 'virt-launcher-web-01', 'uid-1')])
    main.k8s_core = mock.Mock()

    assert main._force_kill_vms_on_node(['default/ghost']) == 0
    main.k8s_core.delete_namespaced_pod.assert_not_called()


# --- VM-only mode skips host poweroff ----------------------------------------

def test_vm_only_skips_host_poweroff(monkeypatch):
    calls = []
    monkeypatch.setattr(main, '_graceful_vm_shutdown', lambda *a: calls.append(('vm', a)))
    monkeypatch.setattr(main, '_host_poweroff', lambda: calls.append(('host', ())))

    main.run_shutdown_sequence(None, 'stop', ['default/web-01'], poweroff_host=False)
    assert ('host', ()) not in calls
    assert calls[0][0] == 'vm'
    assert calls[0][1] == ('stop', ['default/web-01'])


def test_peer_call_forwards_vms_and_poweroff(monkeypatch):
    main.AUTH_TOKEN = 't'
    captured = {}

    class FakeResp:
        status = 200

        def read(self):
            return b'{}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        captured['body'] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(main.urllib.request, 'urlopen', fake_urlopen)
    main.call_shutdown_on_node('10.0.0.2', 'migrate', ['default/web-01'], False)
    assert captured['body'] == {'vmStrategy': 'migrate', 'vms': ['default/web-01'], 'poweroff': False}
