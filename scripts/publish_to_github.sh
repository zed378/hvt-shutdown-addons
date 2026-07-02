#!/bin/bash
# Script to build and publish UI extension + Helm chart to GitHub Pages
# Usage: ./scripts/publish_to_github.sh
#
# This script does everything in a single run and a single git push:
# 1. Builds the Vue UI extension (yarn build-pkg)
# 2. Packages the Helm chart and generates index.yaml
# 3. Clones the 'pages' branch once and copies all artifacts
# 4. Commits and pushes in one operation
#
# GitHub Pages serves both the Helm repository and UI plugin static files at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# For automated publishing on every push to main, see:
#   .github/workflows/publish-pages.yml
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Node.js 24+ with yarn installed
#   - Helm installed
#   - Git configured with remote origin
#
# BEFORE FIRST USE: Enable GitHub Pages in repository settings:
#   1. Go to https://github.com/zed378/hvt-shutdown-addons/settings/pages
#   2. Source: Deploy from a branch
#   3. Branch: pages (root /)
#   4. Save

set -e

# ── Configuration ──────────────────────────────────────────────────────
OWNER="zed378"
REPO_NAME="hvt-shutdown-addons"
GITHUB_REPO="https://github.com/${OWNER}/${REPO_NAME}.git"
BRANCH="pages"
UI_DIR="hvt-shutdown-ui"
HELM_REPO_URL="https://${OWNER}.github.io/${REPO_NAME}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
CHART_DIR="$PROJECT_ROOT/Charts"
UI_SRC_DIR="$PROJECT_ROOT/$UI_DIR"

# Extract Helm chart metadata
CHART_VERSION=$(grep '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')
CHART_NAME=$(grep '^name:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')
CHART_TGZ_FILE="$PROJECT_ROOT/${CHART_NAME}-${CHART_VERSION}.tgz"
INDEX_YAML_FILE="$PROJECT_ROOT/index.yaml"

echo "=== GitHub Pages Publisher ==="
echo ""
echo "Repository:   $GITHUB_REPO"
echo "Branch:       $BRANCH"
echo "Helm Repo:    $HELM_REPO_URL"
echo "Chart:        ${CHART_NAME} v${CHART_VERSION}"
echo "UI Source:    $UI_SRC_DIR"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────
for cmd in gh helm node yarn git; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: '$cmd' is not installed."
        exit 1
    fi
done

echo "Node:  $(node --version)"
echo "Yarn:  $(yarn --version)"
echo "Helm:  $(helm version --short 2>/dev/null)"
echo ""

if ! gh auth status &> /dev/null; then
    echo "Error: GitHub CLI is not authenticated. Run 'gh auth login' first."
    exit 1
fi
echo "GitHub CLI: Authenticated"

remoteUrl=$(git remote get-url origin 2>&1)
if [ $? -ne 0 ]; then
    echo "Error: No git remote 'origin' configured."
    exit 1
fi
echo "Git remote: $remoteUrl"
echo ""

# ── Step 1: Build UI Extension ─────────────────────────────────────────
echo "=== [1/3] Building UI Extension ==="
cd "$UI_SRC_DIR"

if [ ! -d "node_modules" ]; then
    echo "Installing UI dependencies..."
    yarn install --frozen-lockfile 2>/dev/null || npm install
fi

yarn build-pkg "$UI_DIR" true

DIST_PKG_DIR="$UI_SRC_DIR/dist-pkg"
if [ ! -d "$DIST_PKG_DIR" ]; then
    echo "Error: Build output not found at $DIST_PKG_DIR"
    exit 1
fi

UI_VERSION_DIR=$(ls -d "$DIST_PKG_DIR"/hvt-shutdown-ui-* 2>/dev/null | head -1)
if [ -z "$UI_VERSION_DIR" ] || [ ! -d "$UI_VERSION_DIR" ]; then
    echo "Error: No versioned build directory found in $DIST_PKG_DIR"
    ls -la "$DIST_PKG_DIR"
    exit 1
fi

UI_VERSION=$(basename "$UI_VERSION_DIR" | sed 's/hvt-shutdown-ui-//')
echo "Built UI version: $UI_VERSION  →  $UI_VERSION_DIR"
echo ""

cd "$PROJECT_ROOT"

# ── Step 2: Package Helm Chart ─────────────────────────────────────────
echo "=== [2/3] Packaging Helm Chart ==="

# Copy scripts into Charts/scripts/ for ConfigMap inclusion
mkdir -p "$CHART_DIR/scripts"
for f in "$PROJECT_ROOT/scripts"/*.sh; do
    base=$(basename "$f")
    [ "$base" != "publish_to_github.sh" ] && cp "$f" "$CHART_DIR/scripts/"
done
cp "$PROJECT_ROOT/scripts"/*.ps1  "$CHART_DIR/scripts/" 2>/dev/null || true
cp "$PROJECT_ROOT/scripts"/*.mac.sh "$CHART_DIR/scripts/" 2>/dev/null || true

helm package "$CHART_DIR" --destination "$PROJECT_ROOT"

if [ ! -f "$CHART_TGZ_FILE" ]; then
    echo "Error: Failed to package Helm chart."
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ_FILE")
echo "Packaged: $CHART_FILENAME"

helm repo index --url "$HELM_REPO_URL" "$PROJECT_ROOT"
echo "Generated: index.yaml"
echo ""
echo "=== index.yaml ==="
cat "$INDEX_YAML_FILE"
echo ""

# ── Step 3: Deploy to pages branch (single clone + single push) ────────
echo "=== [3/3] Deploying to '$BRANCH' branch ==="
TEMP_DIR=$(mktemp -d)
PAGES_DIR="$TEMP_DIR/pages"

if git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$PAGES_DIR" 2>/dev/null; then
    echo "Cloned '$BRANCH' branch."
else
    echo "Error: Cannot clone '$BRANCH' branch. Make sure it exists."
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Copy UI static files
rm -rf "$PAGES_DIR/hvt-shutdown-ui-$UI_VERSION"
cp -r "$UI_VERSION_DIR" "$PAGES_DIR/"
cp "$UI_VERSION_DIR/package.json" "$PAGES_DIR/package.json"
echo "Copied UI files → hvt-shutdown-ui-$UI_VERSION/"

# Copy Helm artifacts
cp "$CHART_TGZ_FILE" "$PAGES_DIR/"
cp "$INDEX_YAML_FILE" "$PAGES_DIR/"
echo "Copied Helm artifacts → $CHART_FILENAME + index.yaml"

cd "$PAGES_DIR"

git add "hvt-shutdown-ui-$UI_VERSION" package.json "$CHART_FILENAME" index.yaml
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name "$OWNER"
git commit -m "Publish UI v${UI_VERSION} and ${CHART_NAME} v${CHART_VERSION}" --allow-empty \
    || echo "Nothing to commit."
git push origin "$BRANCH"

cd "$PROJECT_ROOT"

# ── Cleanup ────────────────────────────────────────────────────────────
echo ""
echo "=== Cleaning up ==="
rm -f "$CHART_DIR/scripts/publish_to_github.sh"
rm -f "$CHART_DIR/scripts/publish_chart.sh"
rm -f "$CHART_DIR/scripts/create_release.sh"
rm -f "$CHART_DIR/scripts/publish_to_github.ps1"
rm -f "$CHART_DIR/scripts/publish_chart.ps1"
rm -f "$CHART_DIR/scripts/publish_chart.mac.sh"
rm -f "$CHART_DIR/scripts/create_release.ps1"
rm -f "$CHART_TGZ_FILE"
rm -f "$INDEX_YAML_FILE"
rm -rf "$PROJECT_ROOT/charts-output/" "$PROJECT_ROOT/releases/" 2>/dev/null || true
rm -rf "$TEMP_DIR"
echo "Done."

echo ""
echo "=== Published successfully ==="
echo ""
echo "Helm index:    https://${OWNER}.github.io/${REPO_NAME}/index.yaml"
echo "Helm chart:    https://${OWNER}.github.io/${REPO_NAME}/${CHART_FILENAME}"
echo "UIPlugin URL:  https://${OWNER}.github.io/${REPO_NAME}"
echo "UI plugin:     https://${OWNER}.github.io/${REPO_NAME}/hvt-shutdown-ui-${UI_VERSION}/package/index.html"
echo ""
echo "To add the Helm repo:"
echo "  helm repo add hvt-shutdown ${HELM_REPO_URL}"
echo "  helm repo update"
echo "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
echo ""
