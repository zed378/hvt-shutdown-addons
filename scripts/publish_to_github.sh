#!/bin/bash
# Script to publish Helm chart to GitHub Pages
# Usage: ./scripts/publish_to_github.sh
#
# Flow:
#   1. Generate Helm tarball and index.yaml
#   2. Push tarball + index to 'pages' branch
#
# GitHub Pages serves the Helm chart repository at:
#   https://zed378.github.io/hvt-shutdown-addons
#
# The UI plugin is now served from a separate Docker image (see Dockerfile.ui),
# NOT from GitHub Pages.
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
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
HELM_REPO_URL="https://${OWNER}.github.io/${REPO_NAME}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
CHART_DIR="$PROJECT_ROOT/Charts"

CHART_VERSION=$(grep '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')
CHART_NAME=$(grep '^name:'    "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')
CHART_TGZ_FILE="$PROJECT_ROOT/${CHART_NAME}-${CHART_VERSION}.tgz"
INDEX_YAML_FILE="$PROJECT_ROOT/index.yaml"

echo "=== GitHub Pages Publisher ==="
echo ""
echo "Repository:  $GITHUB_REPO"
echo "Branch:      $BRANCH"
echo "Helm Repo:   $HELM_REPO_URL"
echo "Chart:       ${CHART_NAME} v${CHART_VERSION}"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────
for cmd in gh helm git; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: '$cmd' is not installed."
        exit 1
    fi
done

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

# ── Step 1: Generate Helm tarball and index.yaml ───────────────────────
echo "=== [1/2] Generating Helm tarball and index.yaml ==="

# Copy scripts into Charts/scripts/ for ConfigMap inclusion
mkdir -p "$CHART_DIR/scripts"
for f in "$PROJECT_ROOT/scripts"/*.sh; do
    base=$(basename "$f")
    [ "$base" != "publish_to_github.sh" ] && cp "$f" "$CHART_DIR/scripts/"
done
cp "$PROJECT_ROOT/scripts"/*.ps1    "$CHART_DIR/scripts/" 2>/dev/null || true
cp "$PROJECT_ROOT/scripts"/*.mac.sh "$CHART_DIR/scripts/" 2>/dev/null || true

helm package "$CHART_DIR" --destination "$PROJECT_ROOT"

if [ ! -f "$CHART_TGZ_FILE" ]; then
    echo "Error: Failed to package Helm chart."
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ_FILE")
helm repo index --url "$HELM_REPO_URL" "$PROJECT_ROOT"

echo "Tarball:    $CHART_FILENAME"
echo "Index:      index.yaml"
echo ""
echo "=== index.yaml ==="
cat "$INDEX_YAML_FILE"
echo ""

# ── Step 2: Push tarball + index to pages branch ───────────────────────
echo "=== [2/2] Pushing tarball and index to '$BRANCH' branch ==="
TEMP_DIR=$(mktemp -d)
PAGES_DIR="$TEMP_DIR/pages"

if git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$PAGES_DIR" 2>/dev/null; then
    echo "Cloned '$BRANCH' branch."
else
    echo "Error: Cannot clone '$BRANCH' branch. Make sure it exists."
    rm -rf "$TEMP_DIR"
    exit 1
fi

cp "$CHART_TGZ_FILE"  "$PAGES_DIR/"
cp "$INDEX_YAML_FILE" "$PAGES_DIR/"

cd "$PAGES_DIR"
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name  "$OWNER"
git add "$CHART_FILENAME" index.yaml
git commit -m "Publish ${CHART_NAME} v${CHART_VERSION}" --allow-empty \
    || echo "Nothing to commit."
git push origin "$BRANCH"
echo "Pushed: $CHART_FILENAME + index.yaml"

cd "$PROJECT_ROOT"

# ── Cleanup ────────────────────────────────────────────────────────────
echo ""
echo "=== Cleaning up ==="
rm -f "$CHART_TGZ_FILE"
rm -f "$INDEX_YAML_FILE"
rm -rf "$PROJECT_ROOT/charts-output/" "$PROJECT_ROOT/releases/" 2>/dev/null || true
rm -rf "$TEMP_DIR"

echo ""
echo "=== Published successfully ==="
echo ""
echo "Helm index:  https://${OWNER}.github.io/${REPO_NAME}/index.yaml"
echo "Helm chart:  https://${OWNER}.github.io/${REPO_NAME}/${CHART_FILENAME}"
echo ""
echo "Note: UI plugin is served from Docker image (zed378/hvt-shutdown-ui), not GitHub Pages."
echo ""
echo "To add the Helm repo:"
echo "  helm repo add hvt-shutdown ${HELM_REPO_URL}"
echo "  helm repo update"
echo "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
echo ""