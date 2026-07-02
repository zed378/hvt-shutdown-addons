# Mermaid Architecture Diagram Plan

## Goal
Replace `architecture.svg` with `architecture.mmd` — a single Mermaid class diagram that captures the service architecture (components, components' methods/endpoints) and the two shutdown flows (cluster-wide vs local) inline via embedded sequence subgraphs.

## Design Decisions

**Diagram type:** Mermaid `classDiagram` as the parent, with two embedded `sequenceDiagram` subgraphs via class methods that describe the flow. This keeps everything in one file.

Actually — Mermaid class diagrams cannot embed subgraphs. A class diagram that references flow logic is hard to read. Better approach: **Mermaid `graph TD` (directed flow diagram)** that serves as both architecture and flow in one. This is simpler, cleaner, and Mermaid renders it well.

- **Layout:** Top-to-bottom flow
- **Nodes:** Groups for Harvester cluster → namespace → service components → physical nodes
- **Two flows:** Cluster-wide (left branch) and local (right branch) from the `/system/shutdown` endpoint

### Node groups and colors

| Group | Fill | Border |
|---|---|---|
| Harvester Cluster | #eff6ff | #2563eb |
| harvester-system | #f0fdf4 | #16a34a |
| Webhook Service (DaemonSet) | #fffbeb | #d97706 |
| API layer (endpoints) | #f0f9ff | #0ea5e9 |
| Business logic layer | #f5f3ff | #7c3aed |
| Infra layer (K8s client, Power) | #fef2f2 | #dc2626 |
| Physical Node | #fef3c7 | #ea580c |
| Peer Node | #ede9fe | #8b5cf6 |

### Diagram structure (textual outline)

```
External
  └── Admin/Controller (triggers POST /system/shutdown)
        │
        ▼
    [Harvester Cluster]
    ┌──────────────────────────────────────────────┐
    │  harvester-system (Namespace)                │
    │                                              │
    │  ┌──────────────────────────────────┐        │
    │  │ DaemonSet: node-shutdown-webhook │        │
    │  │                                  │        │
    │  │  ┌────────────────────────┐      │        │
    │  │  │ API Endpoints         │      │        │
    │  │  │ GET  /healthz         │      │        │
    │  │  │ GET  /healthz/ready   │      │        │
    │  │  │ GET  /healthz/k8s     │      │        │
    │  │  │ POST /system/shutdown │──────┼──┐     │
    │  │  └────────────────────────┘      │  │     │
    │  │          │                       │  │     │
    │  │  ┌───────▼───────────────┐       │  │     │
    │  │  │ Auth Middleware       │       │  │     │
    │  │  │ Rate Limiter          │       │  │     │
    │  │  │ Audit Middleware      │       │  │     │
    │  │  └───────────┬───────────┘       │  │     │
    │  │              │                   │  │     │
    │  │  ┌───────────▼───────────┐       │  │     │
    │  │  │ Shutdown Coordinator  │       │  │     │
    │  │  │ (all_nodes param)     │───────┘  │     │
    │  │  └──┬──────────────┬────┘           │     │
    │  │     │              │                 │     │
    │  │  [cluster-wide] [local-only]        │     │
    │  │  (?all_nodes)  (?all_nodes=false)   │     │
    │  │     │              │                 │     │
    │  │  ┌──▼──┐      ┌───▼──┐              │     │
    │  │  │Coor-│      │Local │              │     │
    │  │  │dinate│      │Seque│              │     │
    │  │  └──┬──┘      └───┬──┘              │     │
    │  │     │              │                 │     │
    │  │  ┌──▼──────────────▼──┐              │     │
    │  │  │ VM Shutdown Phase  │              │     │
    │  │  │ (shared)           │              │     │
    │  │  └──────┬─────────────┘              │     │
    │  │         │                            │     │
    │  │  ┌──────▼─────────────┐              │     │
    │  │  │ K8s API Client     │              │     │
    │  │  │ - list pods        │              │     │
    │  │  │ - delete pods      │              │     │
    │  │  └────────────────────┘              │     │
    │  │                                      │     │
    │  │  (left)                               │     │
    │  │ ┌──────────────────┐                 │     │
    │  │ │Peer Discovery &  │                 │     │
    │  │ │HTTP Coordination │                 │     │
    │  │ │list DaemonSet     │                 │     │
    │  │ │pods → call each  │                 │     │
    │  │ │via HTTP POST     │                 │     │
    │  │ │wait for peers     │                 │     │
    │  │ │to go offline      │                 │     │
    │  │ └──────────────────┘                 │     │
    │  │                                      │     │
    │  │  ┌──────────────────────────────┐    │     │
    │  │  │ Infra: Host Poweroff          │    │     │
    │  │  │ chroot /host systemctl        │    │     │
    │  │  │ chroot /host poweroff         │    │     │
    │  │  │ chroot /host shutdown         │    │     │
    │  │  │ fallback: SysRq 'o'           │    │     │
    │  │  └──────────────────────────────┘    │     │
    │  └──────────────────────────────────────┘        │
    │                                                  │
    │  Physical Nodes:                                 │
    │  [Node A] ──── [Node B] ──── [Node N]            │
    │  virt-launchers      virt-launchers               │
    │  DaemonPod A         DaemonPod B                  │
    └──────────────────────────────────────────────────┘
```

### Merging into one Mermaid diagram

The diagram will use Mermaid's `flowchart` with subgraph clusters and styled nodes. Here's the concrete Mermaid code:

```mermaid
flowchart TD
    Admin["Admin / Controller"] -->|POST /system/shutdown| Cluster

    subgraph Cluster["Harvester Cluster"]
        subgraph NS["harvester-system Namespace"]
            subgraph DS["DaemonSet: node-shutdown-webhook"]
                subgraph API["API Endpoints"]
                    h1["GET /healthz"]
                    h2["GET /healthz/ready"]
                    h3["GET /healthz/k8s"]
                    shutdown["POST /system/shutdown"]
                end

                subgraph MW["Middleware Layer"]
                    auth["Auth Middleware<br/>Bearer token verify<br/>constant-time compare"]
                    rate["Rate Limiter<br/>sliding window<br/>10 req/min default"]
                    audit["Audit Logger<br/>method, path, status,<br/>duration, client IP"]
                end

                subgraph LOGIC["Shutdown Logic"]
                    coord["Shutdown Coordinator<br/>atomic check-and-set<br/>concurrent protection"]

                    subgraph CW["Cluster-wide Path<br/>(all_nodes not false)"]
                        discover["Discover peer nodes<br/>list DaemonSet pods<br/>by label app=node-shutdown"]
                        pcall["Peer HTTP calls<br/>POST /system/shutdown<br/>?all_nodes=false<br/>with Bearer token"]
                        pwait["Wait for peers<br/>poll healthz every 5s<br/>timeout 300s"]
                    end

                    subgraph LO["Local-only Path<br/>(all_nodes=false)"]
                        localseq["Local shutdown sequence<br/>skip peer coordination"]
                    end
                end

                subgraph VM["VM Shutdown Phase<br/>(shared by both paths)"]
                    listvm["List Running VMs<br/>field_selector nodeName<br/>filter virt-launcher-*"]
                    delvm["Delete VM Pods<br/>grace_period=10s<br/>default"]
                    waitvm["Wait for Termination<br/>poll every 5s<br/>timeout=120s default"]
                end

                subgraph INFRA["Infrastructure"]
                    k8s["K8s API Client<br/>CoreV1Api<br/>incluster config"]
                    subgraph POWEROFF["Host Poweroff Chain"]
                        cmd1["systemctl poweroff"]
                        cmd2["poweroff --force"]
                        cmd3["shutdown -h now"]
                        sysrq["SysRq fallback<br/>write 'o'<br/>to sysrq-trigger"]
                    end
                end
            end
        end
    end

    subgraph PHYSICAL["Physical Nodes"]
        NodeA["Node A<br/>DaemonPod + VMs"]
        NodeB["Node B<br/>DaemonPod + VMs"]
        NodeN["Node N<br/>DaemonPod + VMs"]
    end

    shutdown --> MW
    MW --> LOGIC
    coord --> CW
    coord --> LO

    %% Cluster-wide flow
    CW --> discover
    discover --> pcall
    pcall --> pwait
    pwait --> VM
    LO --> VM

    %% Shared VM shutdown
    VM --> listvm
    listvm --> delvm
    delvm --> waitvm
    waitvm --> INFRA

    %% Poweroff chain
    POWEROFF --> cmd1 --> cmd2 --> cmd3 --> sysrq

    %% DaemonSet deploys to all physical nodes
    DS --> NodeA
    DS --> NodeB
    DS --> NodeN

    %% Peer calls go to other physical nodes
    pcall -.->|HTTP| NodeB
    pcall -.->|HTTP| NodeN

    %% Styling
    classDef cluster fill:#eff6ff,stroke:#2563eb,stroke-width:2px
    classDef ns fill:#f0fdf4,stroke:#16a34a,stroke-width:2px
    classDef ds fill:#fffbeb,stroke:#d97706,stroke-width:2px
    classDef api fill:#f0f9ff,stroke:#0ea5e9,stroke-width:1.5px
    classDef mw fill:#edf2f7,stroke:#4a5568,stroke-width:1.5px,stroke-dasharray:5,5
    classDef logic fill:#f5f3ff,stroke:#7c3aed,stroke-width:1.5px
    classDef flow fill:#fef3c7,stroke:#d69e2e,stroke-width:1.5px
    classDef vm fill:#fff1f2,stroke:#f43f5e,stroke-width:1.5px
    classDef infra fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px
    classDef node fill:#fef3c7,stroke:#ea580c,stroke-width:2px
    classDef admin fill:#e2e8f0,stroke:#475569,stroke-width:1.5px

    class Cluster cluster
    class NS ns
    class DS ds
    class API api
    class h1,h2,h3,shutdown api
    class MW mw
    class auth,rate,audit mw
    class LOGIC logic
    class CW,LO flow
    class VM vm
    class listvm,delvm,waitvm vm
    class INFRA infra
    class k8s,POWEROFF,cmd1,cmd2,cmd3,sysrq infra
    class NodeA,NodeB,NodeN node
    class Admin admin
```

### File changes

1. **Delete** `architecture.svg`
2. **Create** `architecture.mmd` with the Mermaid diagram above
3. **Update** `README.md` line 159: change `![Architecture Diagram](architecture.svg)` to ```` ```mermaid\n[inline diagram]\n```` or keep the `.mmd` file link (most renderers auto-detect `.mmd`)

### README integration

Option A (recommended): GitHub auto-renders `.mmd` files when viewed in raw mode. The README link `architecture.mmd` will render as a rendered diagram on GitHub.

Option B: Embed the diagram inline in the README using Mermaid fenced code blocks (avoids needing a separate file but duplicates content).

I chose Option A: keep `architecture.mmd` as a standalone file. The README reference becomes:

```markdown
![Architecture Diagram](architecture.mmd)
```

(Note: GitHub renders `.mmd` in the viewer but not in Markdown image syntax. For best display, we'll embed the Mermaid block directly in the README under the Architecture heading, replacing the `![Architecture Diagram](architecture.svg)` line.)

## Task List

1. **Create `architecture.mmd`** in repo root with the Mermaid flowchart above
2. **Update `README.md`:**
   - Replace line 159 `![Architecture Diagram](architecture.svg)` with the inline Mermaid code block
3. **Delete `architecture.svg`** (optional — leave for now, will be cleaned up in a later commit)
4. **Verify:**
   - Paste `architecture.mmd` content into [Mermaid Live Editor](https://mermaid.live) to confirm rendering
   - Check README renders correctly in Markdown viewer

## Risks & Edge Cases

- **Mermaid rendering in README:** GitHub auto-renders Mermaid in fenced code blocks (>= v2.38). Older viewers won't render it. This is acceptable.
- **Diagram width:** Mermaid renders horizontally by default. The diagram is wide — may require horizontal scroll on narrow screens. This is inherent to flowcharts with parallel branches.
- **`.mmd` file display:** GitHub shows `.mmd` as a code block (syntax-highlighted) in the repo viewer. It does not auto-render as a diagram in the file tree. Users must use Mermaid Live Editor or a local renderer. We document this.

## Validation

- Diagram renders cleanly in Mermaid Live Editor (no syntax errors)
- All endpoints, middleware, and both shutdown paths are visible
- Color coding distinguishes: API (blue), middleware (gray), logic (purple), VM (red), infra (green), nodes (orange)
- No overlapping nodes or confusing arrows
