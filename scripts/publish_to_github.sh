#!/bin/bash
# Publish the Helm charts to GitHub Pages.
# Usage: ./scripts/publish_to_github.sh
#
# Each chart is published into a folder on the 'pages' branch, and EVERY folder
# gets its OWN index.yaml. A chart's add-on `repo:` URL must match its folder:
#
#   Charts/          -> node-shutdown        -> <pages>/            (repo root)
#   DashboardChart/  -> harvester-dashboard  -> <pages>/dashboard/  (own index)
#   VpnChart/        -> vpn                  -> <pages>/vpn/       (own index)
#
# GitHub Pages serves the Helm chart repository at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# Requirements: helm, git (with push access to the 'pages' branch).
#
# BEFORE FIRST USE: Enable GitHub Pages in repository settings:
#   Settings -> Pages -> Source: Deploy from a branch -> Branch: pages (root /)

set -e

# ── Configuration ──────────────────────────────────────────────────────
OWNER="zed378"
REPO_NAME="hvt-shutdown-addons"
GITHUB_REPO="https://github.com/${OWNER}/${REPO_NAME}.git"
BRANCH="pages"
HELM_REPO_URL="https://${OWNER}.github.io/${REPO_NAME}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
README_FILE="$PROJECT_ROOT/README.md"

# "<chart dir>:<pages folder>"  —  the folder "root" means the repo root.
CHARTS=(
  "Charts:root"
  "VpnChart:vpn"
  "DashboardChart:dashboard"
)

echo "=== GitHub Pages Publisher (per-folder indexes) ==="
echo "Repository:  $GITHUB_REPO"
echo "Helm Repo:   $HELM_REPO_URL"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────
for cmd in helm git; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: '$cmd' is not installed."; exit 1
    fi
done
if ! git remote get-url origin >/dev/null 2>&1; then
    echo "Error: No git remote 'origin' configured."; exit 1
fi
echo "Git remote: $(git remote get-url origin)"
echo ""

TEMP_DIR=$(mktemp -d)
FOLDERS=$(printf '%s\n' "${CHARTS[@]}" | cut -d: -f2 | sort -u)

# ── Step 1: package each chart into its folder's stage dir, index each ──
echo "=== [1/2] Packaging + indexing charts ==="
for folder in $FOLDERS; do
    stage="$TEMP_DIR/stage-$folder"
    mkdir -p "$stage"
    packaged=0
    for entry in "${CHARTS[@]}"; do
        d="${entry%%:*}"; f="${entry##*:}"
        [ "$f" = "$folder" ] || continue
        chart="$PROJECT_ROOT/$d"
        if [ ! -f "$chart/Chart.yaml" ]; then
            echo "Skip: $d has no Chart.yaml"; continue
        fi
        name=$(grep '^name:'    "$chart/Chart.yaml" | awk '{print $2}' | tr -d '"')
        version=$(grep '^version:' "$chart/Chart.yaml" | awk '{print $2}' | tr -d '"')
        helm package "$chart" --destination "$stage" >/dev/null
        if [ ! -f "$stage/${name}-${version}.tgz" ]; then
            echo "Error: Failed to package $d ($name)."; rm -rf "$TEMP_DIR"; exit 1
        fi
        echo "Packaged: ${name} v${version} -> /${folder}"
        packaged=1
    done
    [ "$packaged" = "1" ] || continue

    # Index URL must match where the folder is served from.
    if [ "$folder" = "root" ]; then index_url="$HELM_REPO_URL"; else index_url="$HELM_REPO_URL/$folder"; fi
    helm repo index --url "$index_url" "$stage"
    echo "Indexed: $index_url/index.yaml"
done
echo ""

# ── Step 2: push every folder to the pages branch ───────────────────────
echo "=== [2/2] Pushing to '$BRANCH' branch ==="
PAGES_DIR="$TEMP_DIR/pages"
if ! git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$PAGES_DIR" 2>/dev/null; then
    echo "Error: Cannot clone '$BRANCH' branch. Make sure it exists."
    rm -rf "$TEMP_DIR"; exit 1
fi
echo "Cloned '$BRANCH' branch."

for folder in $FOLDERS; do
    stage="$TEMP_DIR/stage-$folder"
    [ -d "$stage" ] || continue
    if [ "$folder" = "root" ]; then dest="$PAGES_DIR"; else dest="$PAGES_DIR/$folder"; fi
    mkdir -p "$dest"
    cp "$stage"/* "$dest"/
    echo "Staged files into /${folder}"
done
if [ -f "$README_FILE" ]; then
    cp "$README_FILE" "$PAGES_DIR/README.md"
fi

cd "$PAGES_DIR"
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name  "$OWNER"
git add -A
git commit -m "Publish charts (per-folder indexes)" --allow-empty || echo "Nothing to commit."
git push origin "$BRANCH"
echo "Pushed."

cd "$PROJECT_ROOT"

# ── Cleanup ────────────────────────────────────────────────────────────
echo ""
echo "=== Cleaning up ==="
rm -rf "$TEMP_DIR"
echo "Done."

echo ""
echo "=== Published successfully ==="
echo "node-shutdown: ${HELM_REPO_URL}/index.yaml"
echo "Dashboard:     ${HELM_REPO_URL}/dashboard/index.yaml"
echo "VPN:           ${HELM_REPO_URL}/vpn/index.yaml"
echo ""
echo "Add-on repo: URLs must match these folders:"
echo "  Charts/addon.yaml          repo: ${HELM_REPO_URL}"
echo "  VpnChart/addon.yaml        repo: ${HELM_REPO_URL}/vpn"
echo "  DashboardChart/addon.yaml  repo: ${HELM_REPO_URL}/dashboard"
echo ""
echo "Install order: enable 'harvester-dashboard' (the UI), then feature add-ons."
