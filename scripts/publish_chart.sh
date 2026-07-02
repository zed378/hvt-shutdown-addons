#!/bin/bash
# Script to package and publish the Helm chart to a static file repository
# Usage: ./scripts/publish_chart.sh [output_directory]
#
# For GitHub Pages as Helm repository, use: ./scripts/publish_to_github.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHART_DIR="$SCRIPT_DIR/../Charts"
OUTPUT_DIR="${1:-$SCRIPT_DIR/../charts-output}"

echo "Packaging Helm chart..."
helm package "$CHART_DIR" --destination "$OUTPUT_DIR"

echo ""
echo "Generating index.yaml..."
helm repo index --url "https://your-registry.example.com/charts" "$OUTPUT_DIR"

echo ""
echo "Chart published to: $OUTPUT_DIR"
echo ""
echo "Files created:"
ls -la "$OUTPUT_DIR"
echo ""
echo "Next steps:"
echo "1. Serve these files via HTTP (e.g., nginx, S3, GitHub Pages)"
echo "2. For GitHub Pages: use ./scripts/publish_to_github.sh instead"
echo "3. Update the repo URL in Charts/addon.yaml to your serving URL"
echo "4. Apply the Addon CRD: kubectl apply -f Charts/addon.yaml"
