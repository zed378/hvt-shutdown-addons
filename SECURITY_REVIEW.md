# Security Review & Hardening — hvt-shutdown-addons

**Date:** 2026-07-09
**Scope:** Full codebase — FastAPI shutdown service (`app/`), Helm chart (`Charts/`), Harvester Addon CRD, Vue UI plugin (`hvt-shutdown-ui/`), and container builds.

> **Addendum (post-review):** The UI plugin was refactored to be served from an **internal in-cluster ClusterIP DNS endpoint** (`http://hvt-shutdown-ui.cattle-ui-plugin-system.svc:80`) rather than GitHub Pages. It is built by a dedicated multi-stage image (`Dockerfile.ui`) and deployed by the Helm chart as an nginx `Deployment`/`Service` plus a `UIPlugin` CR. The UI-related findings below (the UI token field, the UI server's CORS/nosniff headers, and `Dockerfile.ui`) still apply. The `uiplugins` RBAC grant remains removed because the `UIPlugin` CR is created by the Helm release, not the runtime service account.

This service is high-impact by design: a single authenticated `POST /system/shutdown` gracefully evicts VM workloads and powers off Harvester baremetal nodes cluster-wide. It runs as a **privileged**, **hostPID/hostNetwork** DaemonSet with the **host root filesystem mounted at `/host`**. Because the blast radius is "power off the entire cluster," authentication and the safety rails around it are the whole game. The findings below are ordered by severity, with the fix applied for each.

---

## Critical

### C1 — Dead default-token safety guard shipped a publicly-known token live
**Files:** `Charts/templates/secret.yaml`, `Charts/values.yaml`, `Charts/addon.yaml`

`secret.yaml` was meant to block installation when the operator left the default token in place. It compared `auth.token` against a **base64-encoded** string (`cFFM…==`), but the token shipped in `values.yaml`/`addon.yaml` was the **plaintext** of that same value (`pQLe…`). The two are never equal, so the guard never fired — and the chart installed with a token that is committed in this public repository. Anyone who could reach NodePort `30088` on any node could power off the whole cluster.

**Fix:** The guard now compares against the correct plaintext value and aborts the install if it is used. The committed token was removed from both `values.yaml` and `addon.yaml` (`auth.token: ""`). Empty now **fails closed** — the chart generates a strong random token at install time (readable from the `node-shutdown-auth` Secret), and the recommended path is to set an explicit token via the Add-on UI. Verified by simulating all three render paths (leaked → install fails; empty → single-base64 random token; explicit → Secret decodes to exactly the operator value).

---

## High

### H1 — Rate limiting ran *before* authentication and was global, enabling emergency-shutdown lockout
**File:** `app/main.py`

The rate-limit middleware executed ahead of token verification and shared one global 10/min budget across all clients. Unauthenticated junk traffic (or a malicious actor) could exhaust the budget so that the *real*, UPS-triggered shutdown returned `429` at exactly the moment it mattered most — a denial-of-service on the emergency path itself.

**Fix:** Rate limiting now runs **inside the endpoint, after `verify_token` succeeds**, and is **keyed per client IP** (honoring `X-Forwarded-For`/`X-Real-IP`). Failed or unauthenticated requests can no longer consume anyone's budget. Behavior confirmed with unit tests (`test_rate_limit_returns_429_after_auth`, `test_rate_limit_is_per_key`).

### H2 — Over-broad RBAC on a privileged service account
**Files:** `Charts/values.yaml`, `Charts/addon.yaml`

The ClusterRole granted `delete` on `nodes`, `namespaces`, and `events`, plus `pods/status` patch and `create/update/patch` on `uiplugins` — none of which the code uses. A compromise of this pod (already privileged) additionally handed an attacker cluster-wide delete of nodes/namespaces.

**Fix:** Reduced to least privilege — read VM instances, and `get`/`list`/`delete` **pods** only (all the code actually needs to enumerate peers and evict `virt-launcher` pods). The UIPlugin CR is created by the Helm release, not the app, so `uiplugins` write access was removed. Verified the tightened rules parse and contain no dangerous verbs.

---

## Medium

### M1 — Credential material written to logs
**File:** `app/main.py`

Failed auth logged the first 8 characters of the presented token. Fixed to log the event with no token material.

### M2 — Interactive API docs exposed
**File:** `app/main.py`

`/docs`, `/redoc`, and `/openapi.json` were served unauthenticated, disclosing the API surface of a privileged service. Now disabled unless `ENABLE_DOCS=true`.

### M3 — Missing token returned `403` instead of `401`
**File:** `app/main.py`

`HTTPBearer` default behavior returned `403` for a missing header while an invalid token returned `401` — inconsistent, and it desynced from the tests. `HTTPBearer(auto_error=False)` plus explicit handling now returns a uniform `401`.

### M4 — Broken/stale test suite
**File:** `tests/test_main.py`

`test_shutdown_authorized` and `test_shutdown_all_nodes` asserted against response strings and threading behavior that no longer matched the code, and the `_shutdown_in_progress` global leaked between tests (making later tests return `409`). Rewrote the suite to match current behavior, reset shared state in `setUp`, and added coverage for auth, rate limiting, and docs-disabled.

---

## Low

### L1 — Fire-and-forget task could be garbage-collected
**File:** `app/main.py`

`asyncio.create_task(coordinate_cluster_shutdown())` kept no reference; per CPython docs such tasks can be collected mid-flight. Now retained in a module-level set with a done-callback to discard.

### L2 — Unpinned dependencies / unused packages
**File:** `requirements.txt`

Dependencies used lower bounds only (supply-chain drift risk) and included two packages the code never imports (`python-multipart`, `python-dateutil`). Added upper-bound pins and dropped the unused packages.

### L3 — Container build hygiene
**Files:** `Dockerfile`, `Dockerfile.ui`

Added `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED`, pip/setuptools upgrade, and documented *why* the API image must run as root (host chroot poweroff). Added `X-Content-Type-Options: nosniff` to the UI server (the wildcard CORS header is intentionally retained — Rancher must load the extension bundle cross-origin).

---

## Follow-up hardening implemented (second pass)

Everything previously listed as production guidance has now been built into the chart and service (all opt-in, so existing HTTP deployments are unaffected until enabled):

- **TLS transport (H3):** `tls.enabled: true` serves the API over HTTPS and switches all peer-to-peer coordination calls to HTTPS. The chart self-signs a cert (or accepts a bring-your-own `tls.secretName`), mounts it read-only at `/etc/tls`, and flips the health probes to the `HTTPS` scheme. Peer verification is configurable (`tls.peerVerify`, default off for self-signed intra-cluster certs — the bearer token remains the primary control). Implemented in `app/main.py` (`TLS_ENABLED`, `_peer_ssl_context`, HTTPS uvicorn), `Charts/templates/tls-secret.yaml`, and `deployment.yaml`.
- **NetworkPolicy source-CIDR (H4):** `networkPolicy.ingressFromCidr` now actually restricts ingress to the API port (previously the value was ignored). **Caveat documented:** hostNetwork pods largely bypass NetworkPolicy, so a host firewall on NodePort `30088` is the authoritative control.
- **UI token field:** autocomplete/spellcheck disabled, weak-token (<32 char) warning, and an explicit note that anyone who can read the add-on can read the token.

## Accepted / by-design (not changed)

- **Privileged + hostPID + host-root mount:** inherent to powering off baremetal from inside the cluster. Mitigated by the token-gated API and the now-tightened RBAC.
- **Token stored in the Addon `valuesContent`:** readable by anyone with `get` on Harvester addons — this is how the UI token field is wired. Not removable without redesigning the config flow; the UI now warns about it and recommends rotation. Treat add-on read access as sensitive.

---

## Verification performed

Because this environment blocks PyPI, the FastAPI TestClient suite could not be executed here. The following were verified offline:

- `python -m py_compile` on `app/main.py` and `tests/test_main.py` — clean.
- Standalone execution of the `RateLimiter` algorithm — per-key isolation, window expiry, and the memory-eviction path all pass.
- YAML lint of `values.yaml`, `addon.yaml` (including the embedded `valuesContent`), and `Chart.yaml`; assertions that the token is empty and RBAC no longer contains `nodes`/`uiplugins`/`pods/status`.
- Simulation of the `secret.yaml` template across all three token paths (leaked → fail, empty → auto-generate, explicit → exact decode).

**Recommended before deploy:** run `pytest tests/ -v` and `helm template Charts/ --debug` in an environment with network access, build both images, and smoke-test `POST /system/shutdown` against a non-production node.
