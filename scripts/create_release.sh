#!/bin/bash
# Bash script to create a GitHub release for this project (Linux/macOS)
# Usage: ./scripts/create_release.sh [version] [tag] [message]
#
# Examples:
#   ./scripts/create_release.sh
#   ./scripts/create_release.sh 1.1.0 v1.1.0 "Bug fixes and security updates"
#
# Options:
#   --draft        Create as draft release
#   --prerelease   Create as pre-release

set -e

# Parse options
DRAFT=""
PRERELEASE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --draft) DRAFT="--draft"; shift ;;
        --prerelease) PRERELEASE="--prerelease"; shift ;;
        *) break ;;
    esac
done

# Positional arguments
VERSION="${1:-}"
TAG="${2:-}"
MESSAGE="${3:-}"

# Get git info
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
REPO_NAME=$(basename -s .git $(git remote get-url origin 2>/dev/null) 2>/dev/null || echo "hvt-shutdown-addons")

# Prompt for version if not provided
if [ -z "$VERSION" ]; then
    read -p "Enter release version (e.g., 1.0.0): " VERSION
fi

# Default tag to v + version
if [ -z "$TAG" ]; then
    TAG="v$VERSION"
fi

# Default message
if [ -z "$MESSAGE" ]; then
    MESSAGE="Release $TAG ($COMMIT_HASH)"
fi

echo ""
echo "=== GitHub Release Creator ==="
echo ""
echo "Version: $VERSION"
echo "Tag: $TAG"
echo "Message: $MESSAGE"
echo "Branch: $BRANCH"
echo "Latest Commit: $COMMIT_HASH"
echo ""

# Check if helm is installed
if ! command -v helm &> /dev/null; then
    echo "Error: Helm is not installed. Please install Helm first."
    echo "Download from: https://helm.sh/docs/intro/install/"
    exit 1
fi
echo "Helm: Found"

# Check if gh CLI is installed
if ! gh auth status &> /dev/null; then
    echo "Error: GitHub CLI is not authenticated. Run 'gh auth login' first."
    exit 1
fi
echo "GitHub CLI: Authenticated"

# Confirm before proceeding
read -p "Proceed with release? (y/n): " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Release cancelled."
    exit 0
fi

# Create releases directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
RELEASES_DIR="$PROJECT_ROOT/releases"
OUTPUT_DIR="$SCRIPT_DIR/../charts-output"

# Extract owner and repo name for GitHub Pages URL
OWNER=$(echo "$GITHUB_REPO" | sed -n 's|.*github\.com[:/]*/\([^/]*\).*|\1|p')
if [ -z "$OWNER" ]; then
    OWNER=$(git remote get-url origin 2>/dev/null | sed -n 's|.*github\.com[:/]*/\([^/]*\).*|\1|p')
fi
if [ -z "$OWNER" ]; then
    OWNER=$(echo "$COMMIT_HASH" | xargs git config --get user.name 2>/dev/null || echo "yourusername")
fi

mkdir -p "$RELEASES_DIR"
mkdir -p "$OUTPUT_DIR"

# Package the Helm chart
echo ""
echo "Packaging Helm chart..."
CHART_DIR="$PROJECT_ROOT/Charts"
helm package "$CHART_DIR" --destination "$OUTPUT_DIR"

# Generate index (use GitHub Pages URL or default)
GITHUB_REPO="${GITHUB_REPO:-https://github.com/$OWNER/$REPO_NAME}"
echo "Generating index.yaml..."
helm repo index --url "https://$OWNER.github.io/$REPO_NAME" "$OUTPUT_DIR"

# Find the packaged chart file
CHART_TGZ=$(ls "$OUTPUT_DIR"/*.tgz 2>/dev/null | head -1)
if [ -z "$CHART_TGZ" ]; then
    echo "Error: Failed to package Helm chart."
    exit 1
fi

CHART_FILENAME=$(basename "$CHART_TGZ")
echo "Chart packaged: $CHART_FILENAME"

# Copy chart to releases directory
cp "$CHART_TGZ" "$RELEASES_DIR/$CHART_FILENAME"
cp "$OUTPUT_DIR/index.yaml" "$RELEASES_DIR/index.yaml"

# Create compressed archive of the repository
echo ""
echo "Creating release archive..."

# Determine archive format based on OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    ARCHIVE_NAME="${REPO_NAME}-${VERSION}.tar.gz"
    cd "$PROJECT_ROOT"
    # Get list of files to include (exclude releases/, charts-output/, .git, __pycache__, etc.)
    find . -type f \
        -not -path './.git/*' \
        -not -path './.git' \
        -not -path './releases/*' \
        -not -path './releases' \
        -not -path './charts-output/*' \
        -not -path './charts-output' \
        -not -path './__pycache__/*' \
        -not -path '*/__pycache__/*' \
        -not -path './*.pytest_cache/*' \
        -not -name '*.pyc' \
        -not -name '*.pyo' \
        | tar -czf "$RELEASES_DIR/$ARCHIVE_NAME" -T -
else
    # Linux
    ARCHIVE_NAME="${REPO_NAME}-${VERSION}.tar.gz"
    cd "$PROJECT_ROOT"
    find . -type f \
        -not -path './.git/*' \
        -not -path './.git' \
        -not -path './releases/*' \
        -not -path './releases' \
        -not -path './charts-output/*' \
        -not -path './charts-output' \
        -not -path './__pycache__/*' \
        -not -path '*/__pycache__/*' \
        -not -path './*.pytest_cache/*' \
        -not -name '*.pyc' \
        -not -name '*.pyo' \
        | tar -czf "$RELEASES_DIR/$ARCHIVE_NAME" -T -
fi

echo "Archive created: $ARCHIVE_NAME"

# Show release artifacts
echo ""
echo "=== Release Artifacts ==="
ls -lh "$RELEASES_DIR"

echo ""
echo "To create the release, run:"
echo ""
echo "  cd $PROJECT_ROOT"
echo "  gh release create $TAG $RELEASES_DIR/* --title '$TAG' --generate-notes $DRAFT $PRERELEASE"
echo ""
echo "Then push tags and create release on GitHub:"
echo "  git tag $TAG"
echo "  git push origin $TAG"
echo ""