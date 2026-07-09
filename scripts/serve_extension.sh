#!/bin/bash
# Serve the UI extension for Rancher "Developer Load" — the fastest way to verify
# the extension renders in the Harvester/Rancher dashboard, with NO publishing,
# UIPlugin, container image, or Rancher plugin-caching involved.
#
# Usage: ./scripts/serve_extension.sh
# Requirements: Docker (Node/Yarn run inside the container — nothing needed on host).
#
# It builds the extension package and serves it on http://127.0.0.1:4500. Then, in
# the dashboard:
#   1. Click your avatar -> Preferences -> enable "Extension developer features".
#   2. Extensions -> ⋮ (top-right) -> "Developer load".
#   3. Paste the URL printed below (…/hvt-shutdown-ui-<ver>.umd.min.js) and Load.
# The extension loads client-side, so serve it on the SAME machine you browse the
# dashboard from (the browser must be able to reach 127.0.0.1:4500).
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."
EXT_NAME="hvt-shutdown-ui"

if ! command -v docker &> /dev/null; then
    echo "Error: docker is required." >&2; exit 1
fi

echo "=== Building + serving the '$EXT_NAME' extension for Developer Load ==="
echo "This runs Node/Yarn inside Docker; first run downloads dependencies (~1-2 min)."
echo

# Copy the extension into the container (avoids polluting host node_modules),
# install, build the package, then serve it on :4500. Ctrl+C to stop.
docker run --rm -it -p 4500:4500 \
    -v "$PROJECT_ROOT:/src:ro" \
    node:24 bash -c "
        set -e
        cp -r /src/$EXT_NAME /app && cd /app && rm -f .yarnrc
        echo '--- yarn install ---';    yarn install
        echo '--- yarn build-pkg ---';  yarn build-pkg $EXT_NAME
        echo '--- serving on :4500 (Ctrl+C to stop) ---'
        yarn serve-pkgs
    "
