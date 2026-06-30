#!/bin/bash
# Script to package and publish the Helm chart to a static file repository
# Usage: ./scripts/publish_chart.sh [output_directory]

set -e

CHART_DIR="$(cd "$(dirname "$0")/../Charts" && pwd)"
OUTPUT_DIR="${1:-./charts-output}"

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
echo "2. Update the repo URL in Charts/addon.yaml to your serving URL"
echo "3. Apply the Addon CRD: kubectl apply -f Charts/addon.yaml"