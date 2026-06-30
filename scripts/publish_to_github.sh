#!/bin/bash
# Script to publish Helm chart to GitHub as a Helm repository
# Usage: ./scripts/publish_to_github.sh [github_repo_url] [branch]
#
# Examples:
#   ./scripts/publish_to_github.sh https://github.com/username/hvt-shutdown-addons.git gh-pages
#   ./scripts/publish_to_github.sh https://github.com/zed378/hvt-shutdown-addons.git pages
#
# Requirements:
#   - GitHub CLI (gh) authenticated: gh auth login
#   - Helm installed
#   - Git configured with remote origin

set -e

# Parse arguments
GITHUB_REPO="${1:-https://github.com/zed378/hvt-shutdown-addons.git}"
BRANCH="${2:-pages}"

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

# Extract owner and repo name from URL
REPO_NAME=$(basename -s .git "$GITHUB_REPO")
OWNER=$(echo "$GITHUB_REPO" | sed -n 's|.*github\.com[:/]*/\([^/]*\).*|\1|p')
if [ -z "$OWNER" ]; then
    OWNER=$(git remote get-url origin 2>/dev/null | sed -n 's|.*github\.com[:/]*/\([^/]*\).*|\1|p')
fi
if [ -z "$OWNER" ]; then
    echo "Error: Could not extract GitHub owner from repository URL."
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

echo "Packaging Helm chart..."
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

# Generate index.yaml
echo "Generating index.yaml..."
helm repo index --url "https://$OWNER.github.io/$REPO_NAME" "$OUTPUT_DIR"

# Clone the target branch
echo "Cloning branch '$BRANCH'..."
CLONE_DIR="$TEMP_DIR/clone"
git clone --branch "$BRANCH" --single-branch "$GITHUB_REPO" "$CLONE_DIR" 2>/dev/null || {
    echo "Branch '$BRANCH' not found. Creating new branch..."
    git clone "$GITHUB_REPO" "$CLONE_DIR"
    cd "$CLONE_DIR"
    git checkout -b "$BRANCH"
    cd "$PROJECT_ROOT"
}

# Create charts directory in clone
mkdir -p "$CLONE_DIR/charts"

# Copy chart files
cp "$CHART_TGZ" "$CLONE_DIR/charts/"
cp "$OUTPUT_DIR/index.yaml" "$CLONE_DIR/"

# Commit and push
cd "$CLONE_DIR"
git add charts/$CHART_FILENAME index.yaml
git config user.email "github-actions@users.noreply.github.com"
git config user.name "GitHub Actions"
git commit -m "Add helm chart $CHART_FILENAME" --allow-empty || echo "No changes to commit"
git push origin "$BRANCH"

echo ""
echo "Chart published to: https://$OWNER.github.io/$REPO_NAME"
echo "To install:"
echo "  helm repo add hvt-shutdown https://$OWNER.github.io/$REPO_NAME"
echo "  helm install node-shutdown hvt-shutdown/node-shutdown -n harvester-system"

# Cleanup
cd "$PROJECT_ROOT"
rm -rf "$TEMP_DIR"