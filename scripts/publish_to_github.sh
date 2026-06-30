#!/bin/bash
# Script to publish Helm chart to GitHub as a Helm repository
# Usage: ./scripts/publish_to_github.sh
#
# This script:
# 1. Packages the Helm chart
# 2. Generates index.yaml with correct GitHub Pages URL
# 3. Pushes to the 'pages' branch
# 4. GitHub Pages will serve the files automatically
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
OUTPUT_DIR="$SCRIPT_DIR/../charts-output"
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

echo "Owner: $OWNER"
echo "Repo: $REPO_NAME"
echo ""

# Confirm
read -p "Publish Helm chart to GitHub? (y/n): " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Cancelled."
    rm -rf "$TEMP_DIR"
    exit 0
fi

# Package the Helm chart
echo "=== Packaging Helm chart ==="
mkdir -p "$OUTPUT_DIR"
helm package "$CHART_DIR" --destination "$OUTPUT_DIR"

# Find the packaged chart
CHART_TGZ=$(ls "$OUTPUT_DIR"/*.tgz 2>/dev/null | head -1)
if [ -z "$CHART_TGZ" ]; then
    echo "Error: Failed to package Helm chart."
    rm -rf "$TEMP_DIR"
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ")
echo "Chart: $CHART_FILENAME"

# Generate index.yaml with correct URL
echo "Generating index.yaml..."
helm repo index --url "$HELM_REPO_URL" "$OUTPUT_DIR"

echo ""
echo "=== index.yaml content ==="
cat "$OUTPUT_DIR/index.yaml"
echo ""

# Clone the target branch
echo "=== Cloning branch '$BRANCH' ==="
CLONE_DIR="$TEMP_DIR/clone"
if git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$CLONE_DIR" 2>/dev/null; then
    echo "Branch '$BRANCH' found."
else
    echo "Branch '$BRANCH' not found. Creating new branch from main..."
    git clone "$GITHUB_REPO" "$CLONE_DIR"
    cd "$CLONE_DIR"
    git checkout main
    git checkout -b "$BRANCH"
    cd "$PROJECT_ROOT"
fi

# Create charts directory in clone
mkdir -p "$CLONE_DIR/charts"

# Copy chart files
cp "$CHART_TGZ" "$CLONE_DIR/charts/"
cp "$OUTPUT_DIR/index.yaml" "$CLONE_DIR/"

# Commit and push
cd "$CLONE_DIR"
git add charts/$CHART_FILENAME index.yaml
git config user.email "${OWNER}@users.noreply.github.com"
git config user.name "$OWNER"
git commit -m "Add helm chart $CHART_FILENAME" --allow-empty || echo "No changes to commit"
git push origin "$BRANCH"

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