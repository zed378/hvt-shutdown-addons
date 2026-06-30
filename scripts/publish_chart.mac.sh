#!/bin/zsh
# Zsh script for macOS to package and publish the Helm chart to a static file repository
# Usage: ./scripts/publish_chart.mac.sh [output_directory]
#
# Example:
#   ./scripts/publish_chart.mac.sh
#   ./scripts/publish_chart.mac.sh ./my-charts

set -e

# Resolve chart directory (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHART_DIR="$SCRIPT_DIR/../Charts"
OUTPUT_DIR="${1:-./charts-output}"

# Resolve output directory to absolute path
case "$OUTPUT_DIR" in
    /*) FULL_OUTPUT_DIR="$OUTPUT_DIR" ;;
    *) FULL_OUTPUT_DIR="$SCRIPT_DIR/$OUTPUT_DIR" ;;
esac

echo "Packaging Helm chart..."
helm package "$CHART_DIR" --destination "$FULL_OUTPUT_DIR"

echo ""
echo "Generating index.yaml..."
helm repo index --url "https://your-registry.example.com/charts" "$FULL_OUTPUT_DIR"

echo ""
echo "Chart published to: $FULL_OUTPUT_DIR"
echo ""
echo "Files created:"
ls -la "$FULL_OUTPUT_DIR"
echo ""
echo "Next steps:"
echo "1. Serve these files via HTTP (e.g., nginx, S3, GitHub Pages)"
echo "2. Update the repo URL in Charts/addon.yaml to your serving URL"
echo "3. Apply the Addon CRD: kubectl apply -f Charts/addon.yaml"