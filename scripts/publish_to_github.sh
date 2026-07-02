#!/bin/bash
# Script to publish Helm chart to GitHub as a Helm repository
# Usage: ./scripts/publish_to_github.sh
#
# This script:
# 1. Packages the Helm chart to root directory
# 2. Generates index.yaml in root directory
# 3. Checkout only generated files to 'pages' branch via git worktree
# 4. Deletes generated files from root after publish
# 5. GitHub Pages will serve the files automatically
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

# Configuration
OWNER="zed378"
REPO_NAME="hvt-shutdown-addons"
GITHUB_REPO="https://github.com/${OWNER}/${REPO_NAME}.git"
BRANCH="pages"
HELM_REPO_URL="https://${OWNER}.github.io/${REPO_NAME}"

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
CHART_DIR="$PROJECT_ROOT/Charts"

# Generated files in root directory
CHART_TGZ_FILE="$PROJECT_ROOT/node-shutdown-1.1.0.tgz"
UI_PACKAGE_FILE="$PROJECT_ROOT/hvt-shutdown-ui/dist-pkg/hvt-shutdown-ui-1.1.0.tar.gz"
INDEX_YAML_FILE="$PROJECT_ROOT/index.yaml"
TEMP_DIR=$(mktemp -d)

echo "=== GitHub Helm Repository Publisher ==="
echo ""
echo "Repository: $GITHUB_REPO"
echo "Branch: $BRANCH"
echo "Helm Repo URL: $HELM_REPO_URL"
echo ""

# Check prerequisites
for cmd in gh helm git; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is not installed."
        exit 1
    fi
done

# Check GitHub authentication
if ! gh auth status &> /dev/null; then
    echo "Error: GitHub CLI is not authenticated. Run 'gh auth login' first."
    exit 1
fi
echo "GitHub CLI: Authenticated"

# Check git remote
remoteUrl=$(git remote get-url origin 2>&1)
if [ $? -ne 0 ]; then
    echo "Error: No git remote 'origin' configured."
    exit 1
fi
echo "Git remote: $remoteUrl"

echo "Owner: $OWNER"
echo "Repo: $REPO_NAME"
echo ""

# Package the Helm chart to root directory
echo "=== Packaging Helm chart to root ==="
helm package "$CHART_DIR" --destination "$PROJECT_ROOT"

# Build the UI extension package
echo "=== Building UI extension package ==="
cd "$PROJECT_ROOT/hvt-shutdown-ui"
# Ensure dependencies are installed and run build-pkg
if command -v yarn &> /dev/null; then
    yarn build-pkg hvt-shutdown-ui true
elif command -v yarn.cmd &> /dev/null; then
    yarn.cmd build-pkg hvt-shutdown-ui true
elif command -v npm &> /dev/null; then
    npm run build-pkg -- hvt-shutdown-ui true
elif command -v npm.cmd &> /dev/null; then
    npm.cmd run build-pkg -- hvt-shutdown-ui true
else
    echo "Error: Neither yarn nor npm was found in the path."
    exit 1
fi
cd "$PROJECT_ROOT"

if [ ! -f "$CHART_TGZ_FILE" ]; then
    echo "Error: Failed to package Helm chart."
    rm -rf "$TEMP_DIR"
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ_FILE")
UI_FILENAME=$(basename "$UI_PACKAGE_FILE")
echo "Chart: $CHART_FILENAME"
echo "UI Package: $UI_FILENAME"

# Generate index.yaml in root directory
echo "Generating index.yaml in root..."
helm repo index --url "$HELM_REPO_URL" "$PROJECT_ROOT"

echo ""
echo "=== index.yaml content ==="
cat "$INDEX_YAML_FILE"
echo ""

# Checkout generated files to pages branch
echo "=== Checking out generated files to '$BRANCH' ==="
PAGES_DIR="$TEMP_DIR/pages"

# Clone pages branch directly
if git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$PAGES_DIR" 2>/dev/null; then
    echo "Cloned $BRANCH branch."
else
    echo "Error: Cannot clone $BRANCH branch."
    rm -rf "$TEMP_DIR"
    exit 1
fi

cd "$PAGES_DIR"

# Copy generated files
cp "$CHART_TGZ_FILE" "$PAGES_DIR/"
if [ -f "$UI_PACKAGE_FILE" ]; then
    cp "$UI_PACKAGE_FILE" "$PAGES_DIR/"
else
    echo "Warning: UI package file not found at $UI_PACKAGE_FILE"
fi
cp "$INDEX_YAML_FILE" "$PAGES_DIR/"

git add $CHART_FILENAME $UI_FILENAME index.yaml
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name "$OWNER"
git commit -m "Add helm chart $CHART_FILENAME" --allow-empty || echo "No changes to commit"
git push origin "$BRANCH"

cd "$PROJECT_ROOT"

# Delete generated files from root
echo ""
echo "=== Cleaning up generated files ==="
rm -f "$CHART_TGZ_FILE"
rm -f "$INDEX_YAML_FILE"
echo "Removed generated files from root."

echo ""
echo "=== Chart published successfully ==="
echo ""
echo "Chart location: https://${OWNER}.github.io/${REPO_NAME}/charts/${CHART_FILENAME}"
echo "Index file: https://${OWNER}.github.io/${REPO_NAME}/index.yaml"
echo ""
echo "To install:"
echo "  helm repo add hvt-shutdown ${HELM_REPO_URL}"
echo "  helm repo update"
echo "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
echo ""
echo "NOTE: If you get 404 errors, enable GitHub Pages:"
echo "  1. Go to https://github.com/${OWNER}/${REPO_NAME}/settings/pages"
echo "  2. Source: Deploy from a branch"
echo "  3. Branch: pages (root /)"
echo "  4. Save"

# Cleanup
cd "$PROJECT_ROOT"
rm -rf "$TEMP_DIR"