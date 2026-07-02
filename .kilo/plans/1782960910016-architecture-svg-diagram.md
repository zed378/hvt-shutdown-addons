# Architecture SVG Diagram Redesign Plan

## Goal
Redesign `architecture.svg` to be cleaner, wider, and more readable. Flatten the hierarchy and improve visual balance.

## Design Decisions (All Confirmed)

**ViewBox:** 900×600 (was 800×850)  
**Nesting:** Flattened from 4 levels to 2 — Harvester Cluster → harvester-system Namespace → three sibling boxes  
**Layout:** Two-column top row (Addon CRD | DaemonSet), full-width bottom row (Physical Nodes)  
**Self-loops:** Clean arc from top of each node, label "Shutdown → delete VMs → poweroff" above arc  
**Spacing:** Generous — 30px gaps, 20px internal padding  

### Color Palette (refined, minimal)

| Element | Fill | Border |
|---|---|---|
| Harvester Cluster | #eff6ff | #2563eb |
| harvester-system Namespace | #f0fdf4 | #16a34a |
| Addon CRD | #faf5ff | #a21caf |
| DaemonSet | #fffbeb | #d97706 |
| Endpoint boxes | #f0f9ff | #0ea5e9 |
| K8s Client box | #f0f9ff | #0ea5e9 |
| FastAPI row (inline) | #f0fdf4 | #16a34a |
| Physical Nodes | #fef3c7 | #ea580c |
| Node sub-boxes | #fff7ed | #ea580c |
| Arrows (solid) | — | #6b7280 |
| Arrows (dashed) | — | #9ca3af |

### Text Colors
- Container/box titles: #111827, bold, 12-13px
- Body text: #4b5563, regular, 11px
- Connection labels: #9ca3af, italic, 10px
- Self-loop labels: #6b7280, regular, 9px

## SVG Element Map (900x600 viewBox)

### Containers
1. **Harvester Cluster:** rect(20,20,860,560) rx=18, fill=#eff6ff, stroke=#2563eb, 3px
2. **harvester-system Namespace:** rect(50,55,800,480) rx=14, fill=#f0fdf4, stroke=#16a34a, 2px

### Top Row (inside Namespace)
3. **Addon CRD:** rect(80,100,230,100) rx=10, fill=#faf5ff, stroke=#a21caf, 2px
   - Title: "Addon CRD" bold, body: "harvesterhci.io/v1beta1", "enabled/disabled toggle"
4. **DaemonSet:** rect(340,100,460,280) rx=12, fill=#fffbeb, stroke=#d97706, 2px
   - Title: "DaemonSet: node-shutdown-webhook" bold

#### Inside DaemonSet:
5. **API Endpoints:** rect(365,145,410,90) rx=8, fill=#f0f9ff, stroke=#0ea5e9, 1.5px
   - Body: POST /system/shutdown, GET /healthz, GET /healthz/ready, GET /healthz/k8s
6. **K8s API Client:** rect(390,260,360,55) rx=8, fill=#f0f9ff, stroke=#0ea5e9, 1.5px
   - Body: "- List pods", "- Delete virt-launchers"
7. **FastAPI row:** rect(375,335,390,35) rx=6, fill=#f0fdf4, stroke=#16a34a, 1.5px
   - Body: "FastAPI Container — port 8080, uvicorn / fastapi"

### Bottom Row (inside Namespace)
8. **Physical Nodes:** rect(80,420,720,100) rx=12, fill=#fef3c7, stroke=#ea580c, 2px
9. **Node A:** rect(110,462,280,48) rx=8, fill=#fff7ed, stroke=#ea580c, 1.5px
   - Title: "Node A", Body: "virt-launcher-vm1 · virt-launcher-vm2"
10. **Node B:** rect(410,462,280,48) rx=8, fill=#fff7ed, stroke=#ea580c, 1.5px
    - Title: "Node B", Body: "virt-launcher-vm3"

### Self-loop arcs
11. **Node A arc:** path d="M160,462 C160,440 240,440 240,462", stroke=#6b7280, 1.5px, marker-end=arrow
    - Label above at (164,436): "Shutdown → delete VMs → poweroff", 9px
12. **Node B arc:** path d="M460,462 C460,440 540,440 540,462", same style

### Connections (drawn last, on top)
13. **Addon CRD → DaemonSet:** path d="M310,150 L340,150", dashed, marker-end=arrow-dashed, label "Manages" at (320,144)
14. **API → Client:** path d="M575,235 L575,260", dashed, marker-end=arrow-dashed, label "calls" at (582,252)
15. **DaemonSet → Node A:** path d="M570,380 L280,462", solid, marker-end=arrow
16. **DaemonSet → Node B:** path d="M570,380 L490,462", solid, marker-end=arrow

### Markers
- `<marker id="arrow">` — arrowhead fill=#6b7280
- `<marker id="arrow-dashed">` — arrowhead fill=#9ca3af

### Constraints
- Only: `<svg>`, `<defs>`, `<marker>`, `<g>`, `<rect>`, `<text>`, `<path>`
- No `<image>`, no external URLs, no base64

## Task List

1. **Replace `architecture.svg`**
   - Overwrite existing file
   - Use viewBox="0 0 900 600"
   - Apply exact coordinates, colors, labels above

2. **Verify** in browser
   - Renders clean, balanced, no cramped text
   - No external references (grep `<image src=`, `href=` to URLs)

3. **Commit on `pages` branch:**
   - `git add architecture.svg`
   - `git commit -m "docs: redesign architecture SVG — flattened layout, wider viewBox"`

4. **Cherry-pick to `main`:**
   - `git checkout main`
   - `git cherry-pick <pages-commit-hash>`
   - Resolve README conflict if any (both branches already have the SVG reference change)
   - `git push origin main`

5. **Re-cherry-pick to `pages` if needed** to keep branches in sync.

## Verification Criteria
- SVG renders cleanly at normal browser zoom
- Layout wide and balanced, generous spacing
- Text readable, no overlapping elements
- No external image references
- README shows updated SVG under Architecture heading
- Both main and pages have the same architecture.svg