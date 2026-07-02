#!/bin/bash
# Script to publish Helm chart to GitHub as a Helm repository
# Usage: ./scripts/publish_to_github.sh
#
# This script:
# 1. Packages the Helm chart to root directory
# 2. Generates index.yaml in root directory
# 3. Deploys generated files to 'pages' branch via git worktree
# 4. GitHub Pages will serve the Helm repository automatically
#
# Note: UI extension static files are now baked into the Docker image
# and served internally on port 8081. No UI tarball is published to GitHub.
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

# Extract version dynamically from Chart.yaml
CHART_VERSION=$(grep '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')
CHART_NAME=$(grep '^name:' "$CHART_DIR/Chart.yaml" | awk '{print $2}' | tr -d '"')

# Derived paths using dynamic version
CHART_TGZ_FILE="$PROJECT_ROOT/${CHART_NAME}-${CHART_VERSION}.tgz"
INDEX_YAML_FILE="$PROJECT_ROOT/index.yaml"
TEMP_DIR=$(mktemp -d)

echo "=== GitHub Helm Repository Publisher ==="
echo ""
echo "Repository: $GITHUB_REPO"
echo "Branch: $BRANCH"
echo "Helm Repo URL: $HELM_REPO_URL"
echo "Chart: ${CHART_NAME} v${CHART_VERSION}"
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

if [ ! -f "$CHART_TGZ_FILE" ]; then
    echo "Error: Failed to package Helm chart."
    rm -rf "$TEMP_DIR"
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ_FILE")
echo "Chart: $CHART_FILENAME"

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
cp "$INDEX_YAML_FILE" "$PAGES_DIR/"

git add "$CHART_FILENAME" index.yaml
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name "$OWNER"
git commit -m "Publish ${CHART_NAME} v${CHART_VERSION}" --allow-empty || echo "No changes to commit"
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
echo "Chart location: https://${OWNER}.github.io/${REPO_NAME}/${CHART_FILENAME}"
echo "Index file: https://${OWNER}.github.io/${REPO_NAME}/index.yaml"
echo ""
echo "To install:"
echo "  helm repo add hvt-shutdown ${HELM_REPO_URL}"
echo "  helm repo update"
echo "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"
echo ""

# Cleanup
cd "$PROJECT_ROOT"
rm -rf "$TEMP_DIR"
